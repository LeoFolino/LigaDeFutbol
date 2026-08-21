"""Pipeline de exportacion de jugadores SoFIFA (patch 260045) al esquema CSV historico.

Subcomandos:
  discover   -> pagina el listado de SoFIFA y guarda player_id/slug/url en un checkpoint SQLite.
  extract    -> visita cada pagina de detalle de jugador y guarda los datos crudos extraidos (JSON) en el checkpoint.
  build-csv  -> transforma los datos extraidos al esquema exacto de data/raw/players.csv y escribe un CSV nuevo
                (nunca sobreescribe data/raw/players.csv).
  status     -> muestra conteos del checkpoint.

Requiere Chrome real (channel="chrome") con el perfil persistente ya verificado por el usuario contra Cloudflare.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import difflib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / ".external" / "playwright-sofifa-profile"
CHECKPOINT_DB = ROOT / ".external" / "sofifa_260045_checkpoint.sqlite3"
DEFAULT_OLD_CSV = ROOT / "data" / "raw" / "players.csv"
DEFAULT_FINAL_CSV = ROOT / "data" / "output" / "players_fc26_sofifa_260045.csv"
DEFAULT_COMPLETE_CSV = ROOT / "data" / "output" / "players_fc26_sofifa_260045_complete.csv"
DEFAULT_COMPARE_PREFIX = ROOT / "data" / "output" / "players_fc26_sofifa_260045"
VERSION_URL_PART = "260045"
LOCALE = "es-ES"
TIMEZONE_ID = "America/Argentina/Buenos_Aires"
BASE_LISTING_URL = f"https://sofifa.com/players?type=all&r={VERSION_URL_PART}&set=true&offset="
BASE_TEAM_LISTING_URL = f"https://sofifa.com/teams?type={{team_type}}&r={VERSION_URL_PART}&set=true&offset="
PAGE_SIZE = 20

# posN -> codigo de posicion (independiente del idioma, ver /memories/repo/sofifa-scraping.md)
POS_MAP = {
    0: "GK", 2: "RWB", 3: "RB", 4: "RCB", 5: "CB", 6: "LCB", 7: "LB", 8: "LWB",
    9: "RDM", 10: "CDM", 11: "LDM", 12: "RM", 13: "RCM", 14: "CM", 15: "LCM", 16: "LM",
    17: "RAM", 18: "CAM", 19: "LAM", 20: "RF", 21: "CF", 22: "LF", 23: "RW",
    24: "RS", 25: "ST", 26: "LS", 27: "LW",
}

CSV_COLUMNS = [
    "player_id", "player_url", "fifa_version", "fifa_update", "fifa_update_date", "short_name", "long_name",
    "player_positions", "overall", "potential", "value_eur", "wage_eur", "age", "dob", "height_cm", "weight_kg",
    "league_id", "league_name", "league_level", "club_team_id", "club_name", "club_position", "club_jersey_number",
    "club_loaned_from", "club_joined_date", "club_contract_valid_until_year", "nationality_id", "nationality_name",
    "nation_team_id", "nation_position", "nation_jersey_number", "preferred_foot", "weak_foot", "skill_moves",
    "international_reputation", "work_rate", "body_type", "real_face", "release_clause_eur", "player_tags",
    "player_traits", "pace", "shooting", "passing", "dribbling", "defending", "physic", "attacking_crossing",
    "attacking_finishing", "attacking_heading_accuracy", "attacking_short_passing", "attacking_volleys",
    "skill_dribbling", "skill_curve", "skill_fk_accuracy", "skill_long_passing", "skill_ball_control",
    "movement_acceleration", "movement_sprint_speed", "movement_agility", "movement_reactions", "movement_balance",
    "power_shot_power", "power_jumping", "power_stamina", "power_strength", "power_long_shots",
    "mentality_aggression", "mentality_interceptions", "mentality_positioning", "mentality_vision",
    "mentality_penalties", "mentality_composure", "defending_marking_awareness", "defending_standing_tackle",
    "defending_sliding_tackle", "goalkeeping_gk_diving", "goalkeeping_gk_handling", "goalkeeping_gk_kicking",
    "goalkeeping_gk_positioning", "goalkeeping_gk_reflexes", "goalkeeping_speed", "ls", "st", "rs", "lw", "lf", "cf",
    "rf", "rw", "lam", "cam", "ram", "lm", "lcm", "cm", "rcm", "rm", "lwb", "ldm", "cdm", "rdm", "rwb", "lb",
    "lcb", "cb", "rcb", "rb", "gk", "player_face_url",
]

# Nombres de pais en Spanish (locale del sitio) -> Ingles (esquema historico del CSV).
# Claves normalizadas: minuscula, sin acentos (ver normalize() en el JS). No exhaustivo, cubre
# las naciones futbolisticamente mas comunes; si falta una, se deja el nombre original en espanol.
COUNTRY_ES_EN = {
    "espana": "Spain", "francia": "France", "alemania": "Germany", "italia": "Italy",
    "portugal": "Portugal", "inglaterra": "England", "escocia": "Scotland", "gales": "Wales",
    "irlanda del norte": "Northern Ireland", "irlanda": "Republic of Ireland", "paises bajos": "Netherlands",
    "holanda": "Netherlands", "belgica": "Belgium", "suiza": "Switzerland", "austria": "Austria",
    "croacia": "Croatia", "serbia": "Serbia", "polonia": "Poland", "republica checa": "Czech Republic",
    "rumania": "Romania", "hungria": "Hungary", "grecia": "Greece", "turquia": "Turkey",
    "rusia": "Russia", "ucrania": "Ukraine", "suecia": "Sweden", "noruega": "Norway",
    "dinamarca": "Denmark", "finlandia": "Finland", "islandia": "Iceland", "eslovaquia": "Slovakia",
    "eslovenia": "Slovenia", "bosnia y herzegovina": "Bosnia and Herzegovina", "bosnia": "Bosnia and Herzegovina",
    "macedonia del norte": "North Macedonia", "montenegro": "Montenegro", "albania": "Albania",
    "bulgaria": "Bulgaria", "argentina": "Argentina", "brasil": "Brazil", "uruguay": "Uruguay",
    "chile": "Chile", "colombia": "Colombia", "peru": "Peru", "ecuador": "Ecuador", "bolivia": "Bolivia",
    "paraguay": "Paraguay", "venezuela": "Venezuela", "mexico": "Mexico", "estados unidos": "United States",
    "canada": "Canada", "costa rica": "Costa Rica", "panama": "Panama", "honduras": "Honduras",
    "jamaica": "Jamaica", "japon": "Japan", "corea del sur": "South Korea", "corea del norte": "North Korea",
    "china pr": "China PR", "china": "China PR", "australia": "Australia", "nueva zelanda": "New Zealand",
    "arabia saudita": "Saudi Arabia", "iran": "IR Iran", "irak": "Iraq", "qatar": "Qatar",
    "emiratos arabes unidos": "United Arab Emirates", "marruecos": "Morocco", "argelia": "Algeria",
    "tunez": "Tunisia", "egipto": "Egypt", "senegal": "Senegal", "nigeria": "Nigeria", "ghana": "Ghana",
    "camerun": "Cameroon", "costa de marfil": "Ivory Coast", "mali": "Mali", "sudafrica": "South Africa",
    "republica democratica del congo": "DR Congo", "gabon": "Gabon", "cabo verde": "Cape Verde",
    "guinea": "Guinea", "guinea ecuatorial": "Equatorial Guinea", "kenia": "Kenya", "zambia": "Zambia",
    "jordania": "Jordan", "kuwait": "Kuwait", "oman": "Oman", "siria": "Syria",
    "india": "India", "indonesia": "Indonesia", "vietnam": "Vietnam", "tailandia": "Thailand",
    "malasia": "Malaysia", "filipinas": "Philippines",
}

# JS evaluado en la pagina de detalle. Devuelve un dict plano con todos los campos crudos posibles.
DETAIL_EXTRACTION_JS = r"""
() => {
    const clean = (t) => (t || '').trim().replace(/\s+/g, ' ');
    const normalize = (t) => clean(t).toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    const posMap = %(pos_map_json)s;
    const posCodeFromEl = (el) => {
        const cls = [...el.classList].find((c) => /^pos\d+$/.test(c));
        if (!cls) return null;
        const n = parseInt(cls.replace('pos', ''), 10);
        return posMap[n] || null;
    };

    const data = {};
    const monthMap = {
        ene: '01', feb: '02', mar: '03', abr: '04', may: '05', jun: '06',
        jul: '07', ago: '08', sep: '09', sept: '09', oct: '10', nov: '11', dic: '12',
    };
    const toIsoDate = (text) => {
        const m = clean(text).match(/(\d{1,2})\s+([a-z]{3,4})\.?\s+(\d{4})/i);
        if (!m) return '';
        const month = monthMap[m[2].toLowerCase()];
        if (!month) return '';
        return `${m[3]}-${month}-${m[1].padStart(2, '0')}`;
    };
    const parseMoney = (text) => {
        const m = clean(text).replace(/[€$£,]/g, '').match(/([\d.]+)\s*([KM])?/i);
        if (!m) return '';
        const base = parseFloat(m[1]);
        if (Number.isNaN(base)) return '';
        if (m[2] && m[2].toUpperCase() === 'M') return String(Math.round(base * 1_000_000));
        if (m[2] && m[2].toUpperCase() === 'K') return String(Math.round(base * 1_000));
        return String(Math.round(base));
    };
    const countryEsToEn = %(country_map_json)s;

    // --- Nombre / posiciones / edad / altura / peso / nacionalidad (bloque .profile) ---
    const profile = document.querySelector('.profile.clearfix') || document.querySelector('.profile');
    if (profile) {
        const h1 = profile.querySelector('h1');
        if (h1) {
            // El h1 puede tener varios childNodes de texto (ej. nombre occidental + nombre en
            // script nativo separados por <br>, sin espacio entre ellos). Nos quedamos solo con
            // el primer fragmento de texto no vacio.
            const parts = [...h1.childNodes].map((n) => clean(n.textContent)).filter(Boolean);
            data.long_name = parts[0] || clean(h1.textContent);
        }
        const img = profile.querySelector('img[data-src], img[src]');
        if (img) data.player_face_url = img.getAttribute('data-src') || img.getAttribute('src') || '';
        const flagImg = profile.querySelector('img[title]');
        if (flagImg) {
            const rawNationality = flagImg.getAttribute('title') || '';
            data.nationality_name = countryEsToEn[normalize(rawNationality)] || rawNationality;
        }
        const posSpans = [...profile.querySelectorAll('.pos')].map(posCodeFromEl).filter(Boolean);
        data.player_positions = [...new Set(posSpans)].join(', ');
        const p = profile.querySelector('p');
        if (p) {
            const text = clean(p.textContent);
            const m = text.match(/(\d+)\s*y\.?o\.?\s*\(([^)]+)\)\s*(\d+)\s*cm.*?(\d+)\s*kg/i);
            if (m) {
                data.age = m[1];
                data.dob = toIsoDate(m[2]);
                data.height_cm = m[3];
                data.weight_kg = m[4];
            }
        }
    }
    const titleText = document.title || '';
    const shortNameMatch = titleText.split(' - ');
    if (shortNameMatch.length) data.short_name = clean(shortNameMatch[0]);

    // --- Grid de ratings por posicion (27 columnas ls..gk) ---
    document.querySelectorAll('.pos[class*="pos"]').forEach((el) => {
        const code = posCodeFromEl(el);
        if (!code) return;
        const em = el.querySelector('em');
        if (!em) return;
        const key = code.toLowerCase();
        if (!(key in data)) data[key] = clean(em.textContent);
    });

    // --- Resumen (overall/potential/value/wage). Los 6 agregados (pace/shooting/...) se
    // extraen en Python via regex POINT_XXX sobre page.content(), no estan en el DOM visible. ---
    document.querySelectorAll('.grid .col').forEach((col) => {
        const sub = col.querySelector('.sub');
        const em = col.querySelector('em');
        if (!sub || !em) return;
        const label = normalize(sub.textContent);
        const map = {
            'valoracion general': 'overall', 'overall': 'overall',
            'potencial': 'potential', 'potential': 'potential',
            'valor': 'value_eur', 'value': 'value_eur',
            'salario': 'wage_eur', 'wage': 'wage_eur',
        };
        const key = map[label];
        if (!key || key in data) return;
        const value = (key === 'value_eur' || key === 'wage_eur')
            ? parseMoney(em.textContent)
            : clean(em.textContent).replace(/[^0-9.]/g, '');
        data[key] = value;
    });

    // --- Perfil (usa <label>, no <em>) ---
    const footMap = { 'der.': 'Right', 'derecha': 'Right', 'right': 'Right', 'izq.': 'Left', 'izquierda': 'Left', 'left': 'Left' };
    const yesNoMap = { 'si': 'Yes', 'sí': 'Yes', 'yes': 'Yes', 'no': 'No' };
    const bodyTypeMap = {
        'delgado': 'Lean', 'musculoso': 'Stocky', 'normal': 'Normal',
        'lean': 'Lean', 'stocky': 'Stocky', 'unique': 'Unique',
    };
    document.querySelectorAll('.grid.attribute p, .card p').forEach((p) => {
        const label = p.querySelector('label');
        if (!label) return;
        const key = normalize(label.textContent);
        const value = clean(p.textContent.replace(label.textContent, ''));
        const profileMap = {
            'pierna habil': 'preferred_foot', 'preferred foot': 'preferred_foot',
            'filigranas': 'skill_moves', 'skill moves': 'skill_moves',
            'pierna mala': 'weak_foot', 'weak foot': 'weak_foot',
            'reputacion internacional': 'international_reputation', 'international reputation': 'international_reputation',
            'tipo de cuerpo': 'body_type', 'body type': 'body_type',
            'cara real': 'real_face', 'real face': 'real_face',
            'clausula de rescision': 'release_clause_eur', 'release clause': 'release_clause_eur',
            'ritmo de trabajo': 'work_rate', 'work rate': 'work_rate',
        };
        const target = profileMap[key];
        if (!target) return;
        if (target === 'preferred_foot') {
            data[target] = footMap[normalize(value)] || value;
        } else if (target === 'real_face') {
            data[target] = yesNoMap[normalize(value)] || value;
        } else if (target === 'body_type') {
            const normValue = normalize(value);
            let mapped = bodyTypeMap[normValue];
            if (!mapped) {
                const prefixMatch = Object.keys(bodyTypeMap).find((k) => normValue.startsWith(k));
                mapped = prefixMatch ? bodyTypeMap[prefixMatch] + value.substring(prefixMatch.length) : value;
            }
            data[target] = mapped;
        } else if (target === 'release_clause_eur') {
            data[target] = parseMoney(value);
        } else {
            data[target] = value;
        }
    });

    // --- Especialidades -> player_tags ---
    const h5s = [...document.querySelectorAll('h5')];
    const findSection = (...names) => h5s.find((h5) => {
        const text = normalize(h5.textContent);
        return names.some((name) => text === name || text.includes(name));
    });
    const collectSectionLinks = (...names) => {
        const h5 = findSection(...names);
        if (!h5) return [];
        const container = h5.closest('div[class*="col"]') || h5.parentElement;
        if (!container) return [];
        return [...container.querySelectorAll('a, span')]
            .map((item) => clean(item.textContent).replace(/^#/, ''))
            .filter((item) => item && item.length <= 40 && !/^\d+$/.test(item));
    };
    const tags = [...new Set(collectSectionLinks('player specialities', 'especialidades'))];
    if (tags.length) data.player_tags = tags.map((t) => `#${t}`).join(', ');

    // --- Atributos detallados (32 columnas, mapa corregido) ---
    const labelMap = {
        'velocidad': 'movement_sprint_speed', 'sprint speed': 'movement_sprint_speed',
        'aceleracion': 'movement_acceleration', 'acceleration': 'movement_acceleration',
        'definicion': 'attacking_finishing', 'finishing': 'attacking_finishing',
        'pos. ataque': 'mentality_positioning', 'attacking position': 'mentality_positioning', 'attack position': 'mentality_positioning',
        'potencia': 'power_shot_power', 'shot power': 'power_shot_power',
        'tiros lejanos': 'power_long_shots', 'long shots': 'power_long_shots',
        'penaltis': 'mentality_penalties', 'penalties': 'mentality_penalties',
        'voleas': 'attacking_volleys', 'volleys': 'attacking_volleys',
        'vision': 'mentality_vision',
        'centros': 'attacking_crossing', 'crossing': 'attacking_crossing',
        'precision faltas': 'skill_fk_accuracy', 'fk accuracy': 'skill_fk_accuracy',
        'pases largos': 'skill_long_passing', 'long passing': 'skill_long_passing',
        'pases cortos': 'attacking_short_passing', 'short passing': 'attacking_short_passing',
        'efecto': 'skill_curve', 'curve': 'skill_curve',
        'agilidad': 'movement_agility', 'agility': 'movement_agility',
        'equilibrio': 'movement_balance', 'balance': 'movement_balance',
        'reflejos': 'movement_reactions', 'reactions': 'movement_reactions',
        'compostura': 'mentality_composure', 'composure': 'mentality_composure',
        'control del balon': 'skill_ball_control', 'ball control': 'skill_ball_control',
        'regates': 'skill_dribbling', 'dribbling': 'skill_dribbling',
        'intercep.': 'mentality_interceptions', 'interceptions': 'mentality_interceptions',
        'precision cabeza': 'attacking_heading_accuracy', 'heading accuracy': 'attacking_heading_accuracy',
        'conciencia defensiva': 'defending_marking_awareness', 'defensive awareness': 'defending_marking_awareness',
        'robos': 'defending_standing_tackle', 'standing tackle': 'defending_standing_tackle',
        'entrada agresiva': 'defending_sliding_tackle', 'sliding tackle': 'defending_sliding_tackle',
        'salto': 'power_jumping', 'jumping': 'power_jumping',
        'resistencia': 'power_stamina', 'stamina': 'power_stamina',
        'fuerza': 'power_strength', 'strength': 'power_strength',
        'agresividad': 'mentality_aggression', 'aggression': 'mentality_aggression',
        'estirada': 'goalkeeping_gk_diving', 'gk diving': 'goalkeeping_gk_diving',
        'paradas': 'goalkeeping_gk_handling', 'gk handling': 'goalkeeping_gk_handling',
        'saques': 'goalkeeping_gk_kicking', 'gk kicking': 'goalkeeping_gk_kicking',
        'colocacion': 'goalkeeping_gk_positioning', 'gk positioning': 'goalkeeping_gk_positioning',
        'reflejos gk': 'goalkeeping_gk_reflexes', 'gk reflexes': 'goalkeeping_gk_reflexes',
    };
    // Reflejos es ambiguo (movement_reactions vs goalkeeping_gk_reflexes): desambiguar por categoria (h5)
    document.querySelectorAll('.grid.attribute .col').forEach((col) => {
        const h5 = col.querySelector('h5');
        const category = h5 ? normalize(h5.textContent) : '';
        const isGk = category.includes('portero') || category.includes('goalkeeping') || category === 'gk';
        col.querySelectorAll('p').forEach((p) => {
            const em = p.querySelector('em');
            if (!em) return;
            const value = clean(em.textContent).match(/\d+/);
            if (!value) return;
            const rawLabel = clean(p.textContent.replace(em.textContent, ''));
            const key = normalize(rawLabel);
            let mapped = labelMap[key];
            if (key === 'reflejos' || key === 'reflexes') {
                mapped = isGk ? 'goalkeeping_gk_reflexes' : 'movement_reactions';
            }
            if (mapped) data[mapped] = value[0];
        });
    });

    // --- Club ---
    const clubH5 = findSection('club');
    if (clubH5) {
        const clubContainer = clubH5.closest('div[class*="col"]') || clubH5.parentElement;
        if (clubContainer) {
            const clubNameLink = clubContainer.querySelector('a[href*="/team/"]');
            if (clubNameLink) {
                data.club_name = clean(clubNameLink.textContent);
                const teamIdMatch = (clubNameLink.getAttribute('href') || '').match(/\/team\/(\d+)\//);
                if (teamIdMatch) data.club_team_id = teamIdMatch[1];
            }
            const leagueLink = clubContainer.querySelector('a[href*="/league/"]');
            if (leagueLink) {
                data.league_name = clean(leagueLink.textContent);
                const leagueIdMatch = (leagueLink.getAttribute('href') || '').match(/\/league\/(\d+)/);
                if (leagueIdMatch) data.league_id = leagueIdMatch[1];
            }
            const posSpan = clubContainer.querySelector('.pos');
            if (posSpan) data.club_position = posCodeFromEl(posSpan);
            const text = clean(clubContainer.textContent);
            const dorsalMatch = text.match(/(?:Dorsal|Jersey number)\s*(\d+)/i);
            if (dorsalMatch) data.club_jersey_number = dorsalMatch[1];
            const contractMatch = text.match(/(?:Contrato v[aá]lido hasta|Contract valid until)\s*(\d{4})/i);
            if (contractMatch) data.club_contract_valid_until_year = contractMatch[1];
            const loanMatch = text.match(/(?:Prestado de|Loaned from)\s*([^\n]+)/i);
            if (loanMatch) data.club_loaned_from = clean(loanMatch[1]);
            const joinedMatch = text.match(/(?:Uni[oó]|Joined)\s*([A-Za-z0-9. ]+\d{4})/i);
            if (joinedMatch) data.club_joined_date = toIsoDate(joinedMatch[1]);
        }
    }

    // --- Seleccion nacional ---
    const nationH5 = findSection('seleccion nacional', 'national team');
    if (nationH5) {
        const nationContainer = nationH5.closest('div[class*="col"]') || nationH5.parentElement;
        if (nationContainer) {
            const nationLink = nationContainer.querySelector('a[href*="/team/"]');
            if (nationLink) {
                const teamIdMatch = (nationLink.getAttribute('href') || '').match(/\/team\/(\d+)\//);
                if (teamIdMatch) data.nation_team_id = teamIdMatch[1];
            }
            const posSpan = nationContainer.querySelector('.pos');
            if (posSpan) data.nation_position = posCodeFromEl(posSpan);
            const text = clean(nationContainer.textContent);
            const dorsalMatch = text.match(/(?:Dorsal|Jersey number)\s*(\d+)/i);
            if (dorsalMatch) data.nation_jersey_number = dorsalMatch[1];
        }
    }

    // --- Cara (fallback si no vino del bloque .profile) ---
    if (!data.player_face_url) {
        const faceImg = document.querySelector('.card img, .info img, figure.avatar img');
        if (faceImg) data.player_face_url = faceImg.getAttribute('data-src') || faceImg.src || '';
    }

    // --- Fecha de actualizacion del roster (del titulo: "... - FC 26 - 16 jul. 2026 | SoFIFA") ---
    const dateMatch = titleText.match(/(\d{1,2}\s+[a-z]{3,4}\.?\s+\d{4})/i);
    if (dateMatch) data.fifa_update_date = toIsoDate(dateMatch[1]);

    // --- Texto plano completo, por si necesitamos re-parsear despues ---
    data._body_text = document.body.innerText.substring(0, 20000);
    data._title = document.title;
    data._url = window.location.href;

    return data;
}
""" % {
    "pos_map_json": json.dumps({k: v for k, v in POS_MAP.items()}),
    "country_map_json": json.dumps(COUNTRY_ES_EN),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(db: Path = CHECKPOINT_DB) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS listing (
                player_id INTEGER PRIMARY KEY,
                slug TEXT,
                url TEXT,
                short_name TEXT,
                discovered_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                player_id INTEGER PRIMARY KEY,
                raw_json TEXT,
                actual_url TEXT,
                version_ok INTEGER,
                extracted_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_meta (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stage TEXT NOT NULL,
                player_id INTEGER,
                url TEXT,
                error TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS teams (
                team_id INTEGER PRIMARY KEY,
                slug TEXT,
                url TEXT,
                name TEXT,
                team_type TEXT,
                source_offset INTEGER,
                status TEXT DEFAULT 'pending',
                attempts INTEGER DEFAULT 0,
                last_error TEXT,
                discovered_at TEXT,
                updated_at TEXT
            )
            """
        )
        listing_columns = {row[1] for row in conn.execute("PRAGMA table_info(listing)").fetchall()}
        for column, definition in {
            "source_offset": "INTEGER",
            "source_kind": "TEXT",
            "source_team_id": "INTEGER",
            "status": "TEXT DEFAULT 'pending'",
            "attempts": "INTEGER DEFAULT 0",
            "last_error": "TEXT",
            "updated_at": "TEXT",
        }.items():
            if column not in listing_columns:
                conn.execute(f"ALTER TABLE listing ADD COLUMN {column} {definition}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_listing_status ON listing(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_listing_offset ON listing(source_offset)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_listing_source_team ON listing(source_team_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_status ON teams(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_type ON teams(team_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_players_version_ok ON players(version_ok)")
        conn.execute(
            """
            UPDATE listing
            SET status='ok', updated_at=COALESCE(updated_at, ?)
            WHERE player_id IN (SELECT player_id FROM players WHERE version_ok = 1)
              AND COALESCE(status, 'pending') != 'ok'
            """,
            (now_iso(),),
        )
        conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        """
        INSERT INTO run_meta (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, str(value), now_iso()),
    )


def add_error(conn: sqlite3.Connection, stage: str, player_id: int | None, url: str, error: str) -> None:
    conn.execute(
        "INSERT INTO errors (stage, player_id, url, error, created_at) VALUES (?, ?, ?, ?, ?)",
        (stage, player_id, url, error[:1000], now_iso()),
    )


async def new_context(playwright, headless: bool = False, cdp_url: str = ""):
    if cdp_url:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        return context, browser
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE),
        channel="chrome",
        headless=headless,
        viewport={"width": 1400, "height": 1000},
        locale=LOCALE,
        timezone_id=TIMEZONE_ID,
    )
    return context, None


async def close_browser_context(context, browser=None) -> None:
    if browser is not None:
        return
    else:
        await context.close()


async def wait_ready(page, selector: str, quick_timeout: int = 15000, slow_timeout: int = 240000) -> None:
    try:
        await page.wait_for_selector(selector, timeout=quick_timeout)
    except Exception:
        print("BLOQUEADO. Resolvela manualmente en Chrome (Cloudflare) y dejo esperando...", flush=True)
        await page.wait_for_selector(selector, timeout=slow_timeout)
        print("Resuelto, continuando.", flush=True)


POINT_TO_COLUMN = {
    "PAC": "pace",
    "SHO": "shooting",
    "PAS": "passing",
    "DRI": "dribbling",
    "DEF": "defending",
    "PHY": "physic",
}


async def extract_point_aggregates(page) -> dict[str, str]:
    """Las 6 stats agregadas (pace/shooting/...) se dibujan con un widget JS, no hay texto
    visible en el DOM. Estan embebidas como variables POINT_XXX = N en un <script> inline."""
    content = await page.content()
    result: dict[str, str] = {}
    for key, value in re.findall(r"POINT_(\w+)\s*=\s*(\d{1,2})", content):
        column = POINT_TO_COLUMN.get(key)
        if column:
            result[column] = value
    return result


LISTING_EXTRACTION_JS = """
() => {
    const table = document.querySelector('table');
    if (!table) return [];
    const seen = new Set();
    return [...table.querySelectorAll('tbody tr')].map((tr) => {
        const link = tr.querySelector('a[href*="/player/"]');
        if (!link) return null;
        const href = link.getAttribute('href') || '';
        const m = href.match(/\\/player\\/(\\d+)\\/([^/]+)\\//);
        if (!m || seen.has(m[1])) return null;
        seen.add(m[1]);
        return {
            player_id: parseInt(m[1], 10),
            slug: m[2],
            url: href,
            short_name: (link.textContent || '').trim(),
        };
    }).filter(Boolean);
}
"""


def save_listing_rows(rows: list[dict[str, Any]], offset: int, source_kind: str = "players") -> int:
    timestamp = now_iso()
    total_new = 0
    with sqlite3.connect(CHECKPOINT_DB, timeout=30) as conn:
        for row in rows:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO listing (
                    player_id, slug, url, short_name, discovered_at,
                    source_offset, source_kind, status, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    row["player_id"], row["slug"], row["url"], row["short_name"],
                    timestamp, offset, source_kind, timestamp,
                ),
            )
            total_new += cur.rowcount
            conn.execute(
                """
                UPDATE listing
                SET slug=?, url=?, short_name=?, source_offset=?, source_kind=?, updated_at=?
                WHERE player_id=?
                  AND COALESCE(status, 'pending') != 'ok'
                """,
                (row["slug"], row["url"], row["short_name"], offset, source_kind, timestamp, row["player_id"]),
            )
        set_meta(conn, "last_discover_offset", offset)
        set_meta(conn, "page_size", PAGE_SIZE)
        conn.commit()
    return total_new


async def discover_listing_offset(page, offset: int) -> list[dict[str, Any]]:
    url = f"{BASE_LISTING_URL}{offset}&hl={LOCALE}"
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await wait_ready(page, "table")
    return await page.evaluate(LISTING_EXTRACTION_JS)


async def cmd_discover_parallel(args: argparse.Namespace, context, browser) -> None:
    max_offset = args.max_offset or 26000
    offsets = list(range(args.start_offset, max_offset + 1, args.page_size))
    if args.max_pages:
        offsets = offsets[: args.max_pages]
    queue: asyncio.Queue[int] = asyncio.Queue()
    for offset in offsets:
        queue.put_nowait(offset)
    counters = {"pages": 0, "rows": 0, "new": 0, "empty": 0, "fail": 0}
    started = time.perf_counter()
    total = len(offsets)

    async def worker(worker_id: int) -> None:
        page = context.pages[0] if worker_id == 1 and context.pages else await context.new_page()
        while not queue.empty():
            offset = await queue.get()
            try:
                rows = await discover_listing_offset(page, offset)
                new_count = save_listing_rows(rows, offset, "players")
                counters["pages"] += 1
                counters["rows"] += len(rows)
                counters["new"] += new_count
                counters["empty"] += 1 if not rows else 0
                done = counters["pages"] + counters["fail"]
                elapsed = time.perf_counter() - started
                eta = elapsed / max(1, done) * max(0, total - done)
                print(
                    f"[{done}/{total}] [W{worker_id}] offset={offset}: {len(rows)} filas "
                    f"({new_count} nuevas) | total_new={counters['new']} elapsed={format_seconds(elapsed)} eta={format_seconds(eta)}",
                    flush=True,
                )
            except Exception as exc:
                counters["fail"] += 1
                with sqlite3.connect(CHECKPOINT_DB, timeout=30) as conn:
                    add_error(conn, "discover", None, f"{BASE_LISTING_URL}{offset}&hl={LOCALE}", repr(exc))
                    conn.commit()
                print(f"[W{worker_id}] offset={offset}: ERROR {exc!r}", flush=True)
                if args.stop_on_first_failure:
                    while not queue.empty():
                        queue.get_nowait()
                        queue.task_done()
            finally:
                if args.delay_seconds:
                    await page.wait_for_timeout(int(args.delay_seconds * 1000))
                queue.task_done()

    try:
        await asyncio.gather(*(worker(i + 1) for i in range(max(1, args.workers))))
    finally:
        await close_browser_context(context, browser)
    print(json.dumps(counters, indent=2, ensure_ascii=False), flush=True)



async def cmd_discover(args: argparse.Namespace) -> None:
    ensure_schema(CHECKPOINT_DB)
    async with async_playwright() as pw:
        context, browser = await new_context(pw, headless=args.headless, cdp_url=args.cdp_url)
        if args.workers > 1:
            await cmd_discover_parallel(args, context, browser)
            return
        page = context.pages[0] if context.pages else await context.new_page()
        offset = args.start_offset
        pages_done = 0
        total_new = 0
        empty_pages = 0
        try:
            while True:
                if args.max_pages and pages_done >= args.max_pages:
                    break
                url = f"{BASE_LISTING_URL}{offset}&hl={LOCALE}"
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await wait_ready(page, "table")
                rows = await page.evaluate(
                    """
                    () => {
                        const posMap = %(pos_map_json)s;
                        const posCode = (el) => {
                            const cls = [...el.classList].find((c) => /^pos\\d+$/.test(c));
                            if (!cls) return null;
                            const n = parseInt(cls.replace('pos', ''), 10);
                            return posMap[n] || null;
                        };
                        const table = document.querySelector('table');
                        if (!table) return [];
                        return [...table.querySelectorAll('tbody tr')].map((tr) => {
                            const link = tr.querySelector('a[href*="/player/"]');
                            if (!link) return null;
                            const href = link.getAttribute('href') || '';
                            const m = href.match(/\\/player\\/(\\d+)\\/([^/]+)\\//);
                            if (!m) return null;
                            return {
                                player_id: parseInt(m[1], 10),
                                slug: m[2],
                                url: href,
                                short_name: (link.textContent || '').trim(),
                            };
                        }).filter(Boolean);
                    }
                    """ % {"pos_map_json": json.dumps({k: v for k, v in POS_MAP.items()})}
                )
                if not rows:
                    empty_pages += 1
                    print(f"offset={offset}: 0 filas ({empty_pages}/{args.stop_after_empty}).", flush=True)
                    if empty_pages >= args.stop_after_empty:
                        print("Fin de listado detectado.", flush=True)
                        break
                    offset += args.page_size
                    continue
                empty_pages = 0
                now = now_iso()
                with sqlite3.connect(CHECKPOINT_DB) as conn:
                    for row in rows:
                        cur = conn.execute(
                            """
                            INSERT OR IGNORE INTO listing (
                                player_id, slug, url, short_name, discovered_at, source_offset, status, updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                            """,
                            (row["player_id"], row["slug"], row["url"], row["short_name"], now, offset, now),
                        )
                        total_new += cur.rowcount
                        conn.execute(
                            """
                            UPDATE listing
                            SET slug=?, url=?, short_name=?, source_offset=?, updated_at=?
                            WHERE player_id=?
                            """,
                            (row["slug"], row["url"], row["short_name"], offset, now, row["player_id"]),
                        )
                    set_meta(conn, "last_discover_offset", offset)
                    set_meta(conn, "page_size", args.page_size)
                    conn.commit()
                print(f"offset={offset}: {len(rows)} filas ({total_new} nuevas acumuladas).", flush=True)
                pages_done += 1
                offset += args.page_size
        finally:
            await close_browser_context(context, browser)
    print(f"Discover terminado. Paginas procesadas: {pages_done}. Nuevos jugadores: {total_new}.", flush=True)


async def cmd_discover_teams(args: argparse.Namespace) -> None:
    ensure_schema(CHECKPOINT_DB)
    team_types = ["club", "national"] if args.team_type == "all" else [args.team_type]
    total_new = 0
    async with async_playwright() as pw:
        context, browser = await new_context(pw, headless=args.headless, cdp_url=args.cdp_url)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            for team_type in team_types:
                offset = args.start_offset
                pages_done = 0
                empty_pages = 0
                while True:
                    if args.max_pages and pages_done >= args.max_pages:
                        break
                    url = f"{BASE_TEAM_LISTING_URL.format(team_type=team_type)}{offset}&hl={LOCALE}"
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await wait_ready(page, "table")
                    rows = await page.evaluate(
                        """
                        () => {
                            const table = document.querySelector('table');
                            if (!table) return [];
                            return [...table.querySelectorAll('tbody tr')].map((tr) => {
                                const link = tr.querySelector('a[href*="/team/"]');
                                if (!link) return null;
                                const href = link.getAttribute('href') || '';
                                const match = href.match(/\\/team\\/(\\d+)\\/([^/]+)/);
                                if (!match) return null;
                                return {
                                    team_id: parseInt(match[1], 10),
                                    slug: match[2],
                                    url: href,
                                    name: (link.textContent || '').trim(),
                                };
                            }).filter(Boolean);
                        }
                        """
                    )
                    if not rows:
                        empty_pages += 1
                        print(f"{team_type} offset={offset}: 0 equipos ({empty_pages}/{args.stop_after_empty}).", flush=True)
                        if empty_pages >= args.stop_after_empty:
                            break
                        offset += args.page_size
                        continue
                    empty_pages = 0
                    timestamp = now_iso()
                    with sqlite3.connect(CHECKPOINT_DB) as conn:
                        for row in rows:
                            cur = conn.execute(
                                """
                                INSERT OR IGNORE INTO teams (
                                    team_id, slug, url, name, team_type, source_offset, status, discovered_at, updated_at
                                )
                                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                                """,
                                (
                                    row["team_id"], row["slug"], row["url"], row["name"],
                                    team_type, offset, timestamp, timestamp,
                                ),
                            )
                            total_new += cur.rowcount
                            conn.execute(
                                """
                                UPDATE teams
                                SET slug=?, url=?, name=?, team_type=?, source_offset=?, updated_at=?
                                WHERE team_id=?
                                """,
                                (row["slug"], row["url"], row["name"], team_type, offset, timestamp, row["team_id"]),
                            )
                        set_meta(conn, f"last_team_offset_{team_type}", offset)
                        conn.commit()
                    print(f"{team_type} offset={offset}: {len(rows)} equipos ({total_new} nuevos acumulados).", flush=True)
                    pages_done += 1
                    offset += args.page_size
        finally:
            await close_browser_context(context, browser)
    print(f"Discover teams terminado. Nuevos equipos/selecciones: {total_new}.", flush=True)


def load_team_rows(args: argparse.Namespace) -> list[sqlite3.Row]:
    with sqlite3.connect(CHECKPOINT_DB) as conn:
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM teams"
        params: list[Any] = []
        clauses = []
        if args.team_type != "all":
            clauses.append("team_type = ?")
            params.append(args.team_type)
        if not args.force:
            clauses.append("COALESCE(status, 'pending') != 'ok'")
        if args.max_attempts:
            clauses.append("COALESCE(attempts, 0) < ?")
            params.append(args.max_attempts)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY team_type, COALESCE(source_offset, 999999999), team_id"
        if args.limit:
            query += " LIMIT ?"
            params.append(args.limit)
        return conn.execute(query, params).fetchall()


async def cmd_discover_squad_players(args: argparse.Namespace) -> None:
    ensure_schema(CHECKPOINT_DB)
    teams = load_team_rows(args)
    if not teams:
        print("No hay equipos/selecciones pendientes. Corre discover-teams primero o usa --force.", flush=True)
        return

    started = time.perf_counter()
    queue: asyncio.Queue[sqlite3.Row] = asyncio.Queue()
    for row in teams:
        queue.put_nowait(row)
    counters = {"teams_ok": 0, "teams_fail": 0, "players_new": 0}
    total = len(teams)
    print(f"Descubriendo planteles de {total} equipos/selecciones con {args.workers} workers...", flush=True)

    async with async_playwright() as pw:
        context, browser = await new_context(pw, headless=args.headless, cdp_url=args.cdp_url)

        async def worker(worker_id: int) -> None:
            page = context.pages[0] if worker_id == 1 and context.pages else await context.new_page()
            while not queue.empty():
                team = await queue.get()
                team_id = team["team_id"]
                slug = team["slug"] or "x"
                team_type = team["team_type"] or "club"
                url = f"https://sofifa.com/team/{team_id}/{slug}/{VERSION_URL_PART}/?hl={LOCALE}"
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await wait_ready(page, 'a[href*="/player/"]')
                    players = await page.evaluate(
                        """
                        () => {
                            const seen = new Set();
                            return [...document.querySelectorAll('a[href*="/player/"]')].map((link) => {
                                const href = link.getAttribute('href') || '';
                                const match = href.match(/\\/player\\/(\\d+)\\/([^/]+)/);
                                if (!match || seen.has(match[1])) return null;
                                seen.add(match[1]);
                                return {
                                    player_id: parseInt(match[1], 10),
                                    slug: match[2],
                                    url: href,
                                    short_name: (link.textContent || '').trim(),
                                };
                            }).filter(Boolean);
                        }
                        """
                    )
                    timestamp = now_iso()
                    new_players = 0
                    with sqlite3.connect(CHECKPOINT_DB) as conn:
                        for player in players:
                            cur = conn.execute(
                                """
                                INSERT OR IGNORE INTO listing (
                                    player_id, slug, url, short_name, discovered_at,
                                    source_offset, source_kind, source_team_id, status, updated_at
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                                """,
                                (
                                    player["player_id"], player["slug"], player["url"], player["short_name"],
                                    timestamp, team["source_offset"], f"squad:{team_type}", team_id, timestamp,
                                ),
                            )
                            new_players += cur.rowcount
                            conn.execute(
                                """
                                UPDATE listing
                                SET slug=?, url=?, short_name=?, source_kind=?, source_team_id=?, updated_at=?
                                WHERE player_id=?
                                  AND COALESCE(status, 'pending') != 'ok'
                                """,
                                (
                                    player["slug"], player["url"], player["short_name"],
                                    f"squad:{team_type}", team_id, timestamp, player["player_id"],
                                ),
                            )
                        conn.execute(
                            """
                            UPDATE teams
                            SET status='ok', attempts=COALESCE(attempts, 0) + 1, last_error=NULL, updated_at=?
                            WHERE team_id=?
                            """,
                            (timestamp, team_id),
                        )
                        conn.commit()
                    counters["teams_ok"] += 1
                    counters["players_new"] += new_players
                    done = counters["teams_ok"] + counters["teams_fail"]
                    elapsed = time.perf_counter() - started
                    eta = elapsed / max(1, done) * max(0, total - done)
                    print(
                        f"[{done}/{total}] [W{worker_id}] {team_type} {team_id} {slug}: "
                        f"{len(players)} jugadores ({new_players} nuevos) | elapsed={format_seconds(elapsed)} eta={format_seconds(eta)}",
                        flush=True,
                    )
                except Exception as exc:
                    counters["teams_fail"] += 1
                    error = repr(exc)
                    with sqlite3.connect(CHECKPOINT_DB) as conn:
                        timestamp = now_iso()
                        conn.execute(
                            """
                            UPDATE teams
                            SET status='error', attempts=COALESCE(attempts, 0) + 1, last_error=?, updated_at=?
                            WHERE team_id=?
                            """,
                            (error[:1000], timestamp, team_id),
                        )
                        add_error(conn, "discover_squad_players", team_id, url, error)
                        conn.commit()
                    print(f"[W{worker_id}] fail team {team_id} {slug}: {error}", flush=True)
                    if args.stop_on_first_failure:
                        while not queue.empty():
                            queue.get_nowait()
                            queue.task_done()
                finally:
                    if args.delay_seconds:
                        await page.wait_for_timeout(int(args.delay_seconds * 1000))
                    queue.task_done()

        try:
            await asyncio.gather(*(worker(i + 1) for i in range(max(1, args.workers))))
        finally:
            await close_browser_context(context, browser)
    print(json.dumps(counters, indent=2, ensure_ascii=False), flush=True)


def format_seconds(seconds: float) -> str:
    seconds = int(seconds)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {sec}s"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def load_extract_rows(args: argparse.Namespace) -> list[sqlite3.Row]:
    with sqlite3.connect(CHECKPOINT_DB) as conn:
        conn.row_factory = sqlite3.Row
        if args.ids:
            wanted = [int(x.strip()) for x in args.ids.split(",") if x.strip()]
            placeholders = ",".join("?" for _ in wanted)
            return conn.execute(f"SELECT * FROM listing WHERE player_id IN ({placeholders})", wanted).fetchall()

        query = "SELECT l.* FROM listing l"
        params: list[Any] = []
        if args.only_errors:
            query += " WHERE COALESCE(l.status, 'pending') IN ('error', 'version_mismatch')"
        elif not args.force:
            query += " WHERE l.player_id NOT IN (SELECT player_id FROM players WHERE version_ok = 1)"
        else:
            query += " WHERE 1=1"
        if args.max_attempts:
            query += " AND COALESCE(l.attempts, 0) < ?"
            params.append(args.max_attempts)
        query += " ORDER BY COALESCE(l.source_offset, 999999999), l.player_id"
        if args.limit:
            query += " LIMIT ?"
            params.append(args.limit)
        rows = conn.execute(query, params).fetchall()

    if getattr(args, "missing_from_database", False):
        database_path = Path(args.database)
        if not database_path.exists():
            raise FileNotFoundError(f"No existe la base global: {database_path}")
        with sqlite3.connect(database_path) as conn:
            existing_ids = {
                str(row[0])
                for row in conn.execute(
                    "SELECT sofifa_id FROM global_players WHERE sofifa_id IS NOT NULL AND sofifa_id != ''"
                )
            }
        rows = [row for row in rows if str(row["player_id"]) not in existing_ids]
    return rows


async def extract_one(page, row: sqlite3.Row, worker_id: int) -> str:
    player_id = row["player_id"]
    slug = row["slug"] or "x"
    url = f"https://sofifa.com/player/{player_id}/{slug}/{VERSION_URL_PART}/?hl={LOCALE}"
    started = time.perf_counter()
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await wait_ready(page, ".grid.attribute, .lineup")
    data = await page.evaluate(DETAIL_EXTRACTION_JS)
    data.update(await extract_point_aggregates(page))
    actual_url = page.url
    if f"/{VERSION_URL_PART}/" not in actual_url:
        raise ValueError(f"version_mismatch: {actual_url}")
    with sqlite3.connect(CHECKPOINT_DB) as conn:
        timestamp = now_iso()
        conn.execute(
            """
            INSERT INTO players (player_id, raw_json, actual_url, version_ok, extracted_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                raw_json=excluded.raw_json, actual_url=excluded.actual_url,
                version_ok=excluded.version_ok, extracted_at=excluded.extracted_at
            """,
            (player_id, json.dumps(data, ensure_ascii=False), actual_url, timestamp),
        )
        conn.execute(
            """
            UPDATE listing
            SET status='ok', attempts=COALESCE(attempts, 0) + 1, last_error=NULL, updated_at=?
            WHERE player_id=?
            """,
            (timestamp, player_id),
        )
        conn.commit()
    return f"[W{worker_id}] ok {player_id} {slug} took={time.perf_counter() - started:.1f}s"


async def cmd_extract(args: argparse.Namespace) -> None:
    ensure_schema(CHECKPOINT_DB)
    rows = load_extract_rows(args)
    if not rows:
        print("No hay jugadores pendientes de extraer (usa --force, --only-errors o --ids).", flush=True)
        return

    started = time.perf_counter()
    print(f"Extrayendo {len(rows)} jugadores con {args.workers} workers...", flush=True)
    queue: asyncio.Queue[sqlite3.Row] = asyncio.Queue()
    for row in rows:
        queue.put_nowait(row)
    counters = {"ok": 0, "fail": 0}
    total = len(rows)

    async with async_playwright() as pw:
        context, browser = await new_context(pw, headless=args.headless, cdp_url=args.cdp_url)

        async def worker(worker_id: int) -> None:
            page = context.pages[0] if worker_id == 1 and context.pages else await context.new_page()
            while not queue.empty():
                row = await queue.get()
                player_id = row["player_id"]
                slug = row["slug"] or "x"
                try:
                    message = await extract_one(page, row, worker_id)
                    counters["ok"] += 1
                    done = counters["ok"] + counters["fail"]
                    elapsed = time.perf_counter() - started
                    eta = (elapsed / max(1, done)) * max(0, total - done)
                    print(
                        f"[{done}/{total}] {message} | ok={counters['ok']} fail={counters['fail']} "
                        f"elapsed={format_seconds(elapsed)} eta={format_seconds(eta)}",
                        flush=True,
                    )
                except Exception as exc:
                    counters["fail"] += 1
                    error = repr(exc)
                    status = "version_mismatch" if "version_mismatch" in error else "error"
                    url = f"https://sofifa.com/player/{player_id}/{slug}/{VERSION_URL_PART}/?hl={LOCALE}"
                    with sqlite3.connect(CHECKPOINT_DB) as conn:
                        timestamp = now_iso()
                        conn.execute(
                            """
                            UPDATE listing
                            SET status=?, attempts=COALESCE(attempts, 0) + 1, last_error=?, updated_at=?
                            WHERE player_id=?
                            """,
                            (status, error[:1000], timestamp, player_id),
                        )
                        add_error(conn, "extract", player_id, url, error)
                        conn.commit()
                    done = counters["ok"] + counters["fail"]
                    print(f"[{done}/{total}] [W{worker_id}] fail {player_id} {slug}: {error}", flush=True)
                    if args.stop_on_first_failure:
                        while not queue.empty():
                            queue.get_nowait()
                            queue.task_done()
                finally:
                    if args.delay_seconds:
                        await page.wait_for_timeout(int(args.delay_seconds * 1000))
                    queue.task_done()

        try:
            await asyncio.gather(*(worker(i + 1) for i in range(max(1, args.workers))))
        finally:
            await close_browser_context(context, browser)

    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "updated": counters["ok"],
                "failed": counters["fail"],
                "elapsed": format_seconds(elapsed),
                "avg_seconds_per_player": round(elapsed / max(1, counters["ok"] + counters["fail"]), 2),
                "workers": args.workers,
                "version": "Jul 16, 2026",
                "version_url_part": VERSION_URL_PART,
            },
            indent=2,
            ensure_ascii=False,
        ),
        flush=True,
    )


def _num(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value)
    match = re.search(r"[\d.]+", text.replace(",", ""))
    return match.group(0) if match else ""


def transform_row(raw: dict[str, Any]) -> dict[str, str]:
    out = {col: "" for col in CSV_COLUMNS}
    out["fifa_version"] = "26"
    if len(VERSION_URL_PART) == 6:
        out["fifa_update"] = str(int(VERSION_URL_PART[4:6]))
    for col in CSV_COLUMNS:
        if col in raw and raw[col] not in (None, ""):
            out[col] = raw[col]
    # normalizaciones numericas simples para columnas claramente numericas
    for col in ("overall", "potential", "value_eur", "wage_eur", "age", "height_cm", "weight_kg",
                "club_jersey_number", "club_contract_valid_until_year", "nation_jersey_number",
                "weak_foot", "skill_moves", "international_reputation", "release_clause_eur"):
        if out[col]:
            out[col] = _num(out[col])
    return out


async def cmd_build_csv(args: argparse.Namespace) -> None:
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(CHECKPOINT_DB) as conn:
        conn.row_factory = sqlite3.Row
        query = (
            "SELECT p.player_id, p.raw_json, l.url as url_from_listing, l.short_name as listing_short_name "
            "FROM players p LEFT JOIN listing l ON l.player_id = p.player_id WHERE p.version_ok = 1 "
            "ORDER BY COALESCE(l.source_offset, 999999999), p.player_id"
        )
        rows = conn.execute(query).fetchall()
    if not rows:
        print("No hay jugadores extraidos con version_ok=1 todavia.", flush=True)
        return
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            raw = json.loads(row["raw_json"])
            raw["player_id"] = row["player_id"]
            raw["player_url"] = row["url_from_listing"] or raw.get("_url", "")
            # El nombre corto mostrado en la tabla de listado (ej. "L. Messi") es mas fiel al
            # esquema historico del CSV que el derivado del <title> de la pagina de detalle,
            # que a veces trae el nombre completo en vez del abreviado.
            if row["listing_short_name"]:
                raw["short_name"] = row["listing_short_name"]
            transformed = transform_row(raw)
            writer.writerow(transformed)
    print(f"CSV escrito en {out_path} ({len(rows)} filas).", flush=True)


def read_csv_by_id(path: Path) -> tuple[list[str], dict[str, dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"{path} no tiene encabezados.")
        rows = {str(row.get("player_id", "")).strip(): row for row in reader if row.get("player_id")}
    return reader.fieldnames, rows


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


async def cmd_compare(args: argparse.Namespace) -> None:
    old_path = Path(args.old_csv)
    new_path = Path(args.new_csv)
    prefix = Path(args.out_prefix)
    old_header, old_rows = read_csv_by_id(old_path)
    new_header, new_rows = read_csv_by_id(new_path)
    if old_header != new_header:
        print("ADVERTENCIA: el header nuevo no coincide exactamente con el viejo.", flush=True)

    added_ids = sorted(set(new_rows) - set(old_rows), key=int)
    removed_ids = sorted(set(old_rows) - set(new_rows), key=int)
    common_ids = sorted(set(old_rows) & set(new_rows), key=int)
    changed_rows: list[dict[str, str]] = []
    for player_id in common_ids:
        old = old_rows[player_id]
        new = new_rows[player_id]
        for column in new_header:
            if (old.get(column) or "") != (new.get(column) or ""):
                changed_rows.append(
                    {
                        "player_id": player_id,
                        "old_name": old.get("long_name") or old.get("short_name") or "",
                        "new_name": new.get("long_name") or new.get("short_name") or "",
                        "column": column,
                        "old_value": old.get(column) or "",
                        "new_value": new.get(column) or "",
                    }
                )

    write_rows(prefix.with_name(prefix.name + "_added.csv"), new_header, [new_rows[i] for i in added_ids])
    write_rows(prefix.with_name(prefix.name + "_removed.csv"), old_header, [old_rows[i] for i in removed_ids])
    write_rows(
        prefix.with_name(prefix.name + "_changed.csv"),
        ["player_id", "old_name", "new_name", "column", "old_value", "new_value"],
        changed_rows,
    )
    summary = {
        "old_rows": len(old_rows),
        "new_rows": len(new_rows),
        "added_players": len(added_ids),
        "removed_players": len(removed_ids),
        "changed_cells": len(changed_rows),
        "changed_players": len({row["player_id"] for row in changed_rows}),
        "generated_at": now_iso(),
        "version_url_part": VERSION_URL_PART,
    }
    prefix.with_name(prefix.name + "_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)


async def cmd_merge_csv(args: argparse.Namespace) -> None:
    """Fusiona el CSV historico con el CSV extraido, respetando el inventario 260045."""
    ensure_schema(CHECKPOINT_DB)
    old_path = Path(args.old_csv)
    new_path = Path(args.new_csv)
    out_path = Path(args.out)

    with sqlite3.connect(CHECKPOINT_DB) as conn:
        inventory_urls = {
            str(row[0]): row[1]
            for row in conn.execute("SELECT player_id, url FROM listing")
        }
        inventory_ids = set(inventory_urls)

    old_header, old_rows = read_csv_by_id(old_path)
    new_header, new_rows = read_csv_by_id(new_path)
    if old_header != new_header:
        raise ValueError("El CSV nuevo no tiene el mismo header que el CSV historico.")

    merged: dict[str, dict[str, str]] = {
        player_id: row for player_id, row in old_rows.items() if player_id in inventory_ids
    }
    merged.update({player_id: row for player_id, row in new_rows.items() if player_id in inventory_ids})
    for player_id, row in merged.items():
        if inventory_urls.get(player_id):
            row["player_url"] = inventory_urls[player_id]
        row["fifa_version"] = "26"
        row["fifa_update"] = str(int(VERSION_URL_PART[4:6]))
        row["fifa_update_date"] = "2026-07-16"

    missing = sorted(inventory_ids - set(merged), key=int)
    extra = sorted(set(merged) - inventory_ids, key=int)
    ordered_ids = sorted(merged, key=int)
    write_rows(out_path, old_header, [merged[player_id] for player_id in ordered_ids])

    result = {
        "inventory_ids": len(inventory_ids),
        "old_kept": len([player_id for player_id in old_rows if player_id in inventory_ids]),
        "new_extracted": len([player_id for player_id in new_rows if player_id in inventory_ids]),
        "merged_rows": len(merged),
        "missing_inventory_after_merge": len(missing),
        "extra_not_inventory_after_merge": len(extra),
        "missing_sample": missing[:20],
        "out": str(out_path),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
    if missing or extra:
        raise SystemExit(1)


async def cmd_validate(args: argparse.Namespace) -> None:
    csv_path = Path(args.csv)
    header, rows = read_csv_by_id(csv_path)
    duplicated = 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        all_ids = [row.get("player_id", "") for row in csv.DictReader(fh)]
        duplicated = len(all_ids) - len(set(all_ids))
    bad_urls = [
        player_id
        for player_id, row in rows.items()
        if row.get("player_url") and f"/{VERSION_URL_PART}/" not in row.get("player_url", "")
    ]
    missing_required = [
        player_id
        for player_id, row in rows.items()
        if not row.get("overall") or not row.get("long_name")
    ]
    with sqlite3.connect(CHECKPOINT_DB) as conn:
        listing_count = conn.execute("SELECT COUNT(*) FROM listing").fetchone()[0]
        ok_count = conn.execute("SELECT COUNT(*) FROM players WHERE version_ok = 1").fetchone()[0]
        error_count = conn.execute(
            "SELECT COUNT(*) FROM listing WHERE COALESCE(status, 'pending') IN ('error', 'version_mismatch')"
        ).fetchone()[0]
    result = {
        "csv_rows": len(rows),
        "csv_columns": len(header),
        "checkpoint_listing": listing_count,
        "checkpoint_version_ok": ok_count,
        "checkpoint_errors": error_count,
        "duplicate_player_ids": duplicated,
        "player_urls_not_260045": len(bad_urls),
        "missing_required": len(missing_required),
        "valid": (
            duplicated == 0
            and not bad_urls
            and not missing_required
            and (args.allow_partial or len(rows) in {ok_count, listing_count})
        ),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
    if not result["valid"]:
        raise SystemExit(1)


async def cmd_import_db(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(ROOT))
    from app.importer import import_players_csv

    csv_path = Path(args.csv)
    _, csv_rows = read_csv_by_id(csv_path)
    inventory_count = 0
    if CHECKPOINT_DB.exists():
        ensure_schema(CHECKPOINT_DB)
        with sqlite3.connect(CHECKPOINT_DB) as conn:
            inventory_count = conn.execute("SELECT COUNT(*) FROM listing").fetchone()[0]
    if inventory_count and len(csv_rows) < inventory_count and not args.allow_partial_import:
        raise SystemExit(
            "Import bloqueado: el CSV tiene "
            f"{len(csv_rows)} filas y el inventario 260045 tiene {inventory_count}. "
            "Usa merge-csv para generar el CSV completo o agrega --allow-partial-import si "
            "queres reemplazar la base con un parcial a proposito."
        )

    result = import_players_csv(
        csv_path=csv_path,
        database_path=Path(args.database),
        source_dataset=args.source_dataset,
        source_version=args.source_version,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)


async def cmd_diff_database(args: argparse.Namespace) -> None:
    ensure_schema(CHECKPOINT_DB)
    database_path = Path(args.database)
    if not database_path.exists():
        raise FileNotFoundError(f"No existe la base global: {database_path}")
    with sqlite3.connect(CHECKPOINT_DB) as checkpoint, sqlite3.connect(database_path) as database:
        inventory_ids = {str(row[0]) for row in checkpoint.execute("SELECT player_id FROM listing")}
        base_ids = {
            str(row[0])
            for row in database.execute(
                "SELECT sofifa_id FROM global_players WHERE sofifa_id IS NOT NULL AND sofifa_id != ''"
            )
        }
        missing = sorted(inventory_ids - base_ids, key=int)
        removed = sorted(base_ids - inventory_ids, key=int)
    result = {
        "inventory_260045": len(inventory_ids),
        "global_base": len(base_ids),
        "missing_in_global_base": len(missing),
        "present_in_global_but_not_inventory": len(removed),
        "missing_sample": missing[:20],
        "removed_sample": removed[:20],
    }
    if args.out_missing:
        Path(args.out_missing).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_missing).write_text("\n".join(missing) + ("\n" if missing else ""), encoding="utf-8")
        result["missing_ids_file"] = args.out_missing
    if args.out_removed:
        Path(args.out_removed).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_removed).write_text("\n".join(removed) + ("\n" if removed else ""), encoding="utf-8")
        result["removed_ids_file"] = args.out_removed
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)


async def cmd_status(_args: argparse.Namespace) -> None:
    if not CHECKPOINT_DB.exists():
        print("No existe checkpoint todavia (corre 'discover' primero).", flush=True)
        return
    ensure_schema(CHECKPOINT_DB)
    with sqlite3.connect(CHECKPOINT_DB) as conn:
        listing_count = conn.execute("SELECT COUNT(*) FROM listing").fetchone()[0]
        extracted_count = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        ok_count = conn.execute("SELECT COUNT(*) FROM players WHERE version_ok = 1").fetchone()[0]
        status_rows = conn.execute(
            "SELECT COALESCE(status, 'pending'), COUNT(*) FROM listing GROUP BY COALESCE(status, 'pending')"
        ).fetchall()
        status_counts = {row[0]: row[1] for row in status_rows}
        error_samples = conn.execute(
            """
            SELECT player_id, short_name, last_error
            FROM listing
            WHERE COALESCE(status, 'pending') IN ('error', 'version_mismatch')
            ORDER BY updated_at DESC
            LIMIT 10
            """
        ).fetchall()
        team_count = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
        team_status_rows = conn.execute(
            "SELECT team_type, COALESCE(status, 'pending'), COUNT(*) FROM teams GROUP BY team_type, COALESCE(status, 'pending')"
        ).fetchall()
    print(
        json.dumps(
            {
                "listing": listing_count,
                "extracted": extracted_count,
                "version_ok": ok_count,
                "pending": max(0, listing_count - ok_count),
                "status": status_counts,
                "teams": team_count,
                "team_status": [
                    {"team_type": row[0], "status": row[1], "count": row[2]} for row in team_status_rows
                ],
                "recent_errors": [
                    {"player_id": row[0], "short_name": row[1], "error": row[2]} for row in error_samples
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_discover = sub.add_parser("discover", help="Pagina el listado y guarda player_id/slug/url.")
    p_discover.add_argument("--start-offset", type=int, default=0)
    p_discover.add_argument("--max-pages", type=int, default=0, help="0 = sin limite (recorre todo).")
    p_discover.add_argument("--page-size", type=int, default=PAGE_SIZE)
    p_discover.add_argument("--max-offset", type=int, default=0, help="Para workers: 0 usa 26000.")
    p_discover.add_argument("--workers", type=int, default=1)
    p_discover.add_argument("--delay-seconds", type=float, default=0.02)
    p_discover.add_argument("--stop-on-first-failure", action="store_true")
    p_discover.add_argument("--stop-after-empty", type=int, default=2)
    p_discover.add_argument("--headless", action="store_true")
    p_discover.add_argument("--cdp-url", default="", help="Ej: http://127.0.0.1:9222 para usar Chrome ya abierto.")
    p_discover.set_defaults(func=cmd_discover)

    p_teams = sub.add_parser("discover-teams", help="Descubre clubes y selecciones del roster 260045.")
    p_teams.add_argument("--team-type", choices=["club", "national", "all"], default="all")
    p_teams.add_argument("--start-offset", type=int, default=0)
    p_teams.add_argument("--max-pages", type=int, default=0, help="0 = sin limite.")
    p_teams.add_argument("--page-size", type=int, default=PAGE_SIZE)
    p_teams.add_argument("--stop-after-empty", type=int, default=2)
    p_teams.add_argument("--headless", action="store_true")
    p_teams.add_argument("--cdp-url", default="", help="Ej: http://127.0.0.1:9222 para usar Chrome ya abierto.")
    p_teams.set_defaults(func=cmd_discover_teams)

    p_squads = sub.add_parser("discover-squad-players", help="Descubre jugadores activos desde planteles de equipos/selecciones.")
    p_squads.add_argument("--team-type", choices=["club", "national", "all"], default="all")
    p_squads.add_argument("--limit", type=int, default=0, help="0 = sin limite.")
    p_squads.add_argument("--force", action="store_true")
    p_squads.add_argument("--max-attempts", type=int, default=0, help="0 = sin limite.")
    p_squads.add_argument("--workers", type=int, default=4)
    p_squads.add_argument("--delay-seconds", type=float, default=0.1)
    p_squads.add_argument("--stop-on-first-failure", action="store_true")
    p_squads.add_argument("--headless", action="store_true")
    p_squads.add_argument("--cdp-url", default="", help="Ej: http://127.0.0.1:9222 para usar Chrome ya abierto.")
    p_squads.set_defaults(func=cmd_discover_squad_players)

    p_extract = sub.add_parser("extract", help="Extrae datos detallados por jugador.")
    p_extract.add_argument("--limit", type=int, default=0, help="0 = sin limite.")
    p_extract.add_argument("--ids", default="", help="player_ids separados por coma.")
    p_extract.add_argument("--force", action="store_true", help="Re-extrae aunque ya este OK.")
    p_extract.add_argument("--only-errors", action="store_true", help="Reintenta solo errores/version_mismatch.")
    p_extract.add_argument("--missing-from-database", action="store_true", help="Extrae solo IDs que no existen en la base global.")
    p_extract.add_argument("--database", default=str(ROOT / "app" / "data" / "global_players.sqlite3"))
    p_extract.add_argument("--max-attempts", type=int, default=0, help="0 = sin limite.")
    p_extract.add_argument("--workers", type=int, default=8)
    p_extract.add_argument("--delay-seconds", type=float, default=0.05)
    p_extract.add_argument("--stop-on-first-failure", action="store_true")
    p_extract.add_argument("--headless", action="store_true")
    p_extract.add_argument("--cdp-url", default="", help="Ej: http://127.0.0.1:9222 para usar Chrome ya abierto.")
    p_extract.set_defaults(func=cmd_extract)

    p_build = sub.add_parser("build-csv", help="Genera el CSV final desde el checkpoint.")
    p_build.add_argument("--out", default=str(DEFAULT_FINAL_CSV))
    p_build.set_defaults(func=cmd_build_csv)

    p_compare = sub.add_parser("compare", help="Compara el CSV nuevo contra data/raw/players.csv.")
    p_compare.add_argument("--old-csv", default=str(DEFAULT_OLD_CSV))
    p_compare.add_argument("--new-csv", default=str(DEFAULT_FINAL_CSV))
    p_compare.add_argument("--out-prefix", default=str(DEFAULT_COMPARE_PREFIX))
    p_compare.set_defaults(func=cmd_compare)

    p_merge = sub.add_parser("merge-csv", help="Fusiona CSV historico + extraido contra el inventario 260045.")
    p_merge.add_argument("--old-csv", default=str(DEFAULT_OLD_CSV))
    p_merge.add_argument("--new-csv", default=str(DEFAULT_FINAL_CSV))
    p_merge.add_argument("--out", default=str(DEFAULT_COMPLETE_CSV))
    p_merge.set_defaults(func=cmd_merge_csv)

    p_validate = sub.add_parser("validate", help="Valida header, duplicados y URLs 260045.")
    p_validate.add_argument("--csv", default=str(DEFAULT_FINAL_CSV))
    p_validate.add_argument("--allow-partial", action="store_true")
    p_validate.set_defaults(func=cmd_validate)

    p_import = sub.add_parser("import-db", help="Importa el CSV final a app/data/global_players.sqlite3.")
    p_import.add_argument("--csv", default=str(DEFAULT_FINAL_CSV))
    p_import.add_argument("--database", default=str(ROOT / "app" / "data" / "global_players.sqlite3"))
    p_import.add_argument("--source-dataset", default="sofifa-active-players")
    p_import.add_argument("--source-version", default="Jul 16, 2026")
    p_import.add_argument(
        "--allow-partial-import",
        action="store_true",
        help="Permite reemplazar la DB con un CSV menor al inventario 260045. Usar solo a proposito.",
    )
    p_import.set_defaults(func=cmd_import_db)

    p_diff_db = sub.add_parser("diff-database", help="Compara inventario 260045 contra la base global actual.")
    p_diff_db.add_argument("--database", default=str(ROOT / "app" / "data" / "global_players.sqlite3"))
    p_diff_db.add_argument("--out-missing", default=str(ROOT / "data" / "output" / "sofifa_260045_missing_ids.txt"))
    p_diff_db.add_argument("--out-removed", default=str(ROOT / "data" / "output" / "sofifa_260045_removed_ids.txt"))
    p_diff_db.set_defaults(func=cmd_diff_database)

    p_status = sub.add_parser("status", help="Muestra conteos del checkpoint.")
    p_status.set_defaults(func=cmd_status)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
