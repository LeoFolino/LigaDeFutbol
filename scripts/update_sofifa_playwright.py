from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "app" / "data" / "global_players.sqlite3"
DEFAULT_SCRAPER_SRC = ROOT / ".external" / "sofifa-web-scraper" / "src"
DEFAULT_PROFILE = ROOT / ".external" / "playwright-sofifa-profile"

sys.path.insert(0, str(ROOT))

if DEFAULT_SCRAPER_SRC.exists():
    sys.path.insert(0, str(DEFAULT_SCRAPER_SRC))

from app.config import SOFIFA_LOCALE, SOFIFA_VERSION_LABEL, SOFIFA_VERSION_URL_PART

try:
    from player_scraper import PlayerScraper
except ImportError as exc:
    raise SystemExit(
        "No se encontro player_scraper.py. Clona primero:\n"
        "git clone https://github.com/1erkandogan/sofifa-web-scraper .external/sofifa-web-scraper"
    ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Actualiza jugadores desde SoFIFA usando Playwright y una sesion de navegador verificable."
    )
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE))
    parser.add_argument(
        "--cdp-url",
        default="",
        help="Conecta a un Chrome ya abierto con remote debugging. Ej: http://127.0.0.1:9222",
    )
    parser.add_argument("--version-url-part", default=SOFIFA_VERSION_URL_PART, help="Roster/version de SoFIFA en la URL.")
    parser.add_argument("--version-label", default=SOFIFA_VERSION_LABEL, help="Etiqueta guardada en SQLite.")
    parser.add_argument("--locale", default=SOFIFA_LOCALE, help="Idioma de SoFIFA. Ej: es-ES")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--min-overall", type=int, default=None)
    parser.add_argument("--ids", default="", help="IDs SoFIFA separados por coma. Ej: 277846,231747")
    parser.add_argument(
        "--only-missing-transfermarkt-url",
        action="store_true",
        help="Actualiza solo jugadores sin URL de Transfermarkt cargada.",
    )
    parser.add_argument(
        "--attributes-only",
        action="store_true",
        help="Actualiza solo atributos SoFIFA y metadatos de version, sin pisar mercado/Transfermarkt.",
    )
    parser.add_argument("--workers", type=int, default=1, help="Cantidad de pestanas simultaneas.")
    parser.add_argument("--delay-seconds", type=float, default=0.8)
    parser.add_argument("--post-verify-wait", type=float, default=1.2)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--keep-open-on-failure", action="store_true")
    parser.add_argument(
        "--stop-on-first-failure",
        action="store_true",
        help="Corta la tanda al primer fallo para reiniciar Chrome/proceso antes de acumular errores.",
    )
    parser.add_argument(
        "--stop-after-consecutive-failures",
        type=int,
        default=0,
        help="Corta la tanda despues de N fallos consecutivos. Mejor para evitar falsos positivos aislados.",
    )
    parser.add_argument("--skip-updated", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser.parse_args()


def value_to_millions(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return round(number / 1_000_000, 3)


def int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def money_to_millions(value: Any) -> float | None:
    return value_to_millions(value)


def collect_detailed_attributes(data: dict[str, Any]) -> dict[str, int | str]:
    prefixes = (
        "attacking_",
        "skill_",
        "movement_",
        "power_",
        "mentality_",
        "defending_",
        "goalkeeping_",
    )
    details: dict[str, int | str] = {}
    for key, value in data.items():
        if key.startswith(prefixes):
            details[key] = int_or_none(value) if int_or_none(value) is not None else str(value)
    return details


def load_targets(
    database: Path,
    version_label: str,
    limit: int,
    min_overall: int | None,
    ids: str,
    skip_updated: bool,
    only_missing_transfermarkt_url: bool,
    attributes_only: bool,
) -> list[sqlite3.Row]:
    ensure_runtime_columns(database)
    filters = [
        "sofifa_id IS NOT NULL",
        "sofifa_id != ''",
        "COALESCE(player_kind, 'real') != 'generic_unlicensed'",
    ]
    params: list[Any] = []

    if ids.strip():
        wanted_ids = [item.strip() for item in ids.split(",") if item.strip()]
        placeholders = ", ".join("?" for _ in wanted_ids)
        filters.append(f"sofifa_id IN ({placeholders})")
        params.extend(wanted_ids)
    if min_overall is not None:
        filters.append("CAST(overall AS INTEGER) >= ?")
        params.append(min_overall)
    if skip_updated:
        target_dataset = "sofifa-playwright-attributes" if attributes_only else "sofifa-playwright-manual"
        filters.append("(source_dataset != ? OR source_version != ?)")
        params.append(target_dataset)
        params.append(version_label)
    if only_missing_transfermarkt_url:
        filters.append("(transfermarkt_url IS NULL OR transfermarkt_url = '')")

    where_sql = " AND ".join(filters)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            f"""
            SELECT *
            FROM global_players
            WHERE {where_sql}
            ORDER BY CAST(overall AS INTEGER) DESC, name ASC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()


def ensure_runtime_columns(database: Path) -> None:
    runtime_columns = {
        "transfermarkt_url": "TEXT",
        "image_url": "TEXT",
        "weak_foot": "TEXT",
        "skill_moves": "TEXT",
        "international_reputation": "TEXT",
        "body_type": "TEXT",
        "real_face": "TEXT",
        "release_clause_m": "TEXT",
        "acceleration_type": "TEXT",
        "play_styles": "TEXT",
        "specialities": "TEXT",
        "roles_json": "TEXT",
        "attributes_json": "TEXT",
        "sofifa_roster_status": "TEXT",
        "player_kind": "TEXT",
    }
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(global_players)").fetchall()
        }
        for column, definition in runtime_columns.items():
            if column not in columns:
                connection.execute(f"ALTER TABLE global_players ADD COLUMN {column} {definition}")
        connection.commit()


async def extract_summary_attributes(page) -> dict[str, str]:
    html = await page.content()
    mapping = {
        "POINT_PAC": "pace",
        "POINT_SHO": "shooting",
        "POINT_PAS": "passing",
        "POINT_DRI": "dribbling",
        "POINT_DEF": "defending",
        "POINT_PHY": "physical",
    }
    data = {}
    for source_key, target_key in mapping.items():
        match = re.search(rf"\b{source_key}\s*=\s*(\d{{1,2}})", html)
        if match:
            data[target_key] = match.group(1)
    return data


async def extract_profile_extras(page) -> dict[str, str]:
    return await page.evaluate(
        """
        () => {
            const cleanText = (text) => text ? text.trim().replace(/\\s+/g, ' ') : '';
            const extractNumber = (text) => cleanText(text).match(/\\d+/)?.[0] || '';
            const parseValue = (text) => {
                let value = cleanText(text).replace(/[â‚¬$Â£,]/g, '');
                if (!value) return '';
                if (value.includes('M')) return String(parseFloat(value) * 1000000);
                if (value.includes('K')) return String(parseFloat(value) * 1000);
                return value;
            };
            const normalize = (text) => cleanText(text)
                .toLowerCase()
                .normalize('NFD')
                .replace(/[\\u0300-\\u036f]/g, '');
            const data = {};

            document.querySelectorAll('.grid .col').forEach((col) => {
                const sub = col.querySelector('.sub');
                const em = col.querySelector('em');
                if (!sub || !em) return;
                const labelText = normalize(sub.textContent);
                const valueText = cleanText(em.textContent);
                if (labelText.includes('overall') || labelText.includes('valoracion general')) {
                    data.overall_rating = extractNumber(valueText);
                } else if (labelText.includes('potential') || labelText.includes('potencial')) {
                    data.potential = extractNumber(valueText);
                } else if (labelText === 'value' || labelText === 'valor') {
                    data.value = parseValue(valueText);
                } else if (labelText === 'wage' || labelText === 'salario') {
                    data.wage = parseValue(valueText);
                }
            });

            document.querySelectorAll('.grid.attribute p').forEach((p) => {
                const label = p.querySelector('label');
                if (!label) return;
                const labelText = normalize(label.textContent);
                const valueText = cleanText(p.textContent.replace(label.textContent, ''));
                if (labelText.includes('preferred foot') || labelText.includes('pierna habil')) {
                    data.preferred_foot = valueText;
                } else if (labelText.includes('weak foot') || labelText.includes('pierna mala')) {
                    data.weak_foot = extractNumber(valueText);
                } else if (labelText.includes('skill moves') || labelText.includes('filigranas')) {
                    data.skill_moves = extractNumber(valueText);
                } else if (labelText.includes('international reputation') || labelText.includes('reputacion internacional')) {
                    data.international_reputation = extractNumber(valueText);
                } else if (labelText.includes('release clause') || labelText.includes('clausula')) {
                    data.release_clause = parseValue(valueText);
                } else if (labelText.includes('acceleration type') || labelText.includes('tipo de aceler')) {
                    data.acceleration_type = valueText;
                } else if (labelText.includes('body type') || labelText.includes('tipo de cuerpo')) {
                    data.body_type = valueText;
                } else if (labelText.includes('real face') || labelText.includes('cara real')) {
                    data.real_face = valueText;
                }
            });
            return data;
        }
        """
    ) or {}


async def extract_lists(page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
            const cleanText = (text) => text ? text.trim().replace(/\\s+/g, ' ') : '';
            const normalize = (text) => cleanText(text)
                .toLowerCase()
                .normalize('NFD')
                .replace(/[\\u0300-\\u036f]/g, '');
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
                    .map((item) => cleanText(item.textContent).replace(/^#/, ''))
                    .filter((item) => item && item.length <= 40 && !/^\\d+$/.test(item));
            };

            const collectRoles = () => {
                const h5 = findSection('roles');
                if (!h5) return [];
                const rolesGrid = h5.nextElementSibling;
                if (!rolesGrid || !rolesGrid.classList.contains('grid')) return [];
                return [...rolesGrid.querySelectorAll('.col')]
                    .map((col) => [...col.querySelectorAll('p')]
                        .map((item) => cleanText(item.innerText || item.textContent))
                        .filter(Boolean)
                        .join(' | '))
                    .filter((item) => item && item.length <= 160);
            };

            const unique = (items) => [...new Set(items)];
            return {
                specialities: unique(collectSectionLinks('player specialities', 'especialidades')),
                play_styles_list: unique(collectSectionLinks('playstyles', 'estilos de juego')),
                roles: unique(collectRoles()),
            };
        }
        """
    ) or {}


async def extract_visible_detailed_attributes(page) -> dict[str, str]:
    return await page.evaluate(
        """
        () => {
            const cleanText = (text) => text ? text.trim().replace(/\\s+/g, ' ') : '';
            const normalize = (text) => cleanText(text)
                .toLowerCase()
                .normalize('NFD')
                .replace(/[\\u0300-\\u036f]/g, '');
            const labelMap = {
                'crossing': 'attacking_crossing',
                'centros': 'attacking_crossing',
                'finishing': 'attacking_finishing',
                'definicion': 'attacking_finishing',
                'heading accuracy': 'attacking_heading_accuracy',
                'precision cabeza': 'attacking_heading_accuracy',
                'short passing': 'attacking_short_passing',
                'pases cortos': 'attacking_short_passing',
                'volleys': 'attacking_volleys',
                'voleas': 'attacking_volleys',
                'dribbling': 'skill_dribbling',
                'regates': 'skill_dribbling',
                'curve': 'skill_curve',
                'efecto': 'skill_curve',
                'fk accuracy': 'skill_fk_accuracy',
                'precision faltas': 'skill_fk_accuracy',
                'long passing': 'skill_long_passing',
                'pases largos': 'skill_long_passing',
                'ball control': 'skill_ball_control',
                'control del balon': 'skill_ball_control',
                'acceleration': 'movement_acceleration',
                'aceleracion': 'movement_acceleration',
                'sprint speed': 'movement_sprint_speed',
                'velocidad': 'movement_sprint_speed',
                'agility': 'movement_agility',
                'agilidad': 'movement_agility',
                'reactions': 'movement_reactions',
                'balance': 'movement_balance',
                'equilibrio': 'movement_balance',
                'shot power': 'power_shot_power',
                'potencia': 'power_shot_power',
                'jumping': 'power_jumping',
                'salto': 'power_jumping',
                'stamina': 'power_stamina',
                'resistencia': 'power_stamina',
                'strength': 'power_strength',
                'fuerza': 'power_strength',
                'long shots': 'power_long_shots',
                'tiros lejanos': 'power_long_shots',
                'aggression': 'mentality_aggression',
                'agresividad': 'mentality_aggression',
                'interceptions': 'mentality_interceptions',
                'intercep.': 'mentality_interceptions',
                'attack position': 'mentality_attack_position',
                'pos. ataque': 'mentality_attack_position',
                'vision': 'mentality_vision',
                'penalties': 'mentality_penalties',
                'penaltis': 'mentality_penalties',
                'composure': 'mentality_composure',
                'compostura': 'mentality_composure',
                'defensive awareness': 'defending_defensive_awareness',
                'conciencia defensiva': 'defending_defensive_awareness',
                'standing tackle': 'defending_standing_tackle',
                'robos': 'defending_standing_tackle',
                'sliding tackle': 'defending_sliding_tackle',
                'entrada agresiva': 'defending_sliding_tackle',
                'gk diving': 'goalkeeping_gk_diving',
                'estirada': 'goalkeeping_gk_diving',
                'gk handling': 'goalkeeping_gk_handling',
                'paradas': 'goalkeeping_gk_handling',
                'gk kicking': 'goalkeeping_gk_kicking',
                'saques': 'goalkeeping_gk_kicking',
                'gk positioning': 'goalkeeping_gk_positioning',
                'colocacion': 'goalkeeping_gk_positioning',
                'gk reflexes': 'goalkeeping_gk_reflexes',
            };
            const data = {};
            const blocks = document.querySelectorAll('.grid.attribute .col, .attribute .col');
            const sources = blocks.length ? blocks : document.querySelectorAll('body');
            sources.forEach((block) => {
                const title = normalize(block.querySelector('h5, h4, h3')?.textContent || '');
                const isGoalkeeping = (
                    title.includes('portero')
                    || title.includes('goalkeeping')
                    || title === 'gk'
                );
                block.querySelectorAll('p').forEach((p) => {
                    const em = p.querySelector('em');
                    if (!em) return;
                    const value = cleanText(em.textContent).match(/\\d+/)?.[0];
                    if (!value) return;
                    const rawLabel = cleanText(p.textContent.replace(em.textContent, ''));
                    const normalizedLabel = normalize(rawLabel);
                    let key = labelMap[normalizedLabel];
                    if (normalizedLabel === 'reflejos' || normalizedLabel === 'reflexes') {
                        key = isGoalkeeping ? 'goalkeeping_gk_reflexes' : 'movement_reactions';
                    }
                    if (key) data[key] = value;
                });
            });
            return data;
        }
        """
    ) or {}


async def enrich_sofifa_data(page, data: dict[str, Any]) -> dict[str, Any]:
    data["actual_url"] = page.url
    data["actual_version_label"] = await extract_current_roster_label(page)
    summary_attributes = await extract_summary_attributes(page)
    for key, value in summary_attributes.items():
        data[key] = value
    data.update(await extract_profile_extras(page))
    data.update(await extract_visible_detailed_attributes(page))
    lists = await extract_lists(page)
    if lists.get("play_styles_list"):
        data["play_styles"] = ", ".join(lists["play_styles_list"])
    if lists.get("specialities"):
        data["specialities"] = ", ".join(lists["specialities"])
    if lists.get("roles"):
        data["roles_json"] = json.dumps(lists["roles"], ensure_ascii=False)
    data["transfermarkt_url"] = await extract_transfermarkt_url(page)
    data["attributes_json"] = json.dumps(collect_detailed_attributes(data), ensure_ascii=False)
    data["release_clause_m"] = money_to_millions(data.get("release_clause"))
    return data


def build_sofifa_url(row: sqlite3.Row, version_url_part: str, locale: str) -> str:
    sofifa_id = row["sofifa_id"]
    return f"https://sofifa.com/player/{sofifa_id}/x/{version_url_part}/?hl={locale}"


async def extract_current_roster_label(page) -> str:
    return await page.evaluate(
        """
        () => {
            const text = document.body?.innerText || '';
            const match = text.match(/\\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\s+\\d{1,2},\\s+20\\d{2}\\b/);
            return match ? match[0] : '';
        }
        """
    ) or ""


def extract_version_url_part(url: str) -> str:
    match = re.search(r"/player/\d+/(?:[^/]+/)?(\d{6})(?:/|\?|$)", url or "")
    return match.group(1) if match else ""


def is_cloudflare_page(content: str, title: str) -> bool:
    markers = [
        "Verifique que es un ser humano",
        "Verification",
        "Just a moment",
        "Attention Required",
        "Checking your browser",
        "cf-browser-verification",
    ]
    return any(marker in content or marker in title for marker in markers)


async def extract_transfermarkt_url(page) -> str:
    return await page.evaluate(
        """
        () => {
            const links = [...document.querySelectorAll('a[href]')];
            const link = links.find((item) => {
                const href = item.href || '';
                const text = (item.textContent || '').toLowerCase();
                return href.includes('transfermarkt.') || text.includes('transfermarkt');
            });
            return link ? link.href : '';
        }
        """
    ) or ""


async def close_old_pages(context, protected_pages, keep_pages: int = 2) -> None:
    pages = [page for page in context.pages if not page.is_closed()]
    if len(pages) <= keep_pages:
        return

    if isinstance(protected_pages, list):
        protected = set(protected_pages)
    else:
        protected = {protected_pages}
    pages_to_close = [page for page in pages if page not in protected]
    for page in pages_to_close[: max(0, len(pages) - keep_pages)]:
        try:
            await page.close()
        except Exception:
            pass


def update_player(
    database: Path,
    row_id: str,
    data: dict[str, Any],
    version_label: str,
    requested_version_url_part: str,
    attributes_only: bool = False,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    actual_version_url_part = extract_version_url_part(data.get("actual_url") or data.get("url") or "")
    actual_version_label = data.get("actual_version_label") or ""
    roster_status = "active"
    if actual_version_url_part and actual_version_url_part != requested_version_url_part:
        roster_status = "retired"
    data["requested_version_url_part"] = requested_version_url_part
    data["actual_version_url_part"] = actual_version_url_part
    data["sofifa_roster_status"] = roster_status
    with sqlite3.connect(database, timeout=30) as connection:
        if attributes_only:
            connection.execute(
                """
                UPDATE global_players
                SET
                    pace = COALESCE(?, pace),
                    shooting = COALESCE(?, shooting),
                    passing = COALESCE(?, passing),
                    dribbling = COALESCE(?, dribbling),
                    defending = COALESCE(?, defending),
                    physical = COALESCE(?, physical),
                    attributes_json = COALESCE(NULLIF(?, ''), attributes_json),
                    source_dataset = 'sofifa-playwright-attributes',
                    source_version = ?,
                    sofifa_roster_status = ?,
                    imported_at = ?,
                    raw_json = ?
                WHERE id = ?
                """,
                (
                    int_or_none(data.get("pace")),
                    int_or_none(data.get("shooting")),
                    int_or_none(data.get("passing")),
                    int_or_none(data.get("dribbling")),
                    int_or_none(data.get("defending")),
                    int_or_none(data.get("physical")),
                    data.get("attributes_json"),
                    version_label,
                    roster_status,
                    now,
                    json.dumps(data, ensure_ascii=False),
                    row_id,
                ),
            )
            connection.commit()
            return

        connection.execute(
            """
            UPDATE global_players
            SET
                name = COALESCE(NULLIF(?, ''), name),
                long_name = COALESCE(NULLIF(?, ''), long_name),
                position = COALESCE(NULLIF(?, ''), position),
                club = COALESCE(NULLIF(?, ''), club),
                nationality = COALESCE(NULLIF(?, ''), nationality),
                overall = COALESCE(?, overall),
                potential = COALESCE(?, potential),
                market_value_m = COALESCE(?, market_value_m),
                wage_m = COALESCE(?, wage_m),
                transfermarkt_url = COALESCE(NULLIF(?, ''), transfermarkt_url),
                image_url = COALESCE(NULLIF(?, ''), image_url),
                height_cm = COALESCE(?, height_cm),
                weight_kg = COALESCE(?, weight_kg),
                preferred_foot = COALESCE(NULLIF(?, ''), preferred_foot),
                weak_foot = COALESCE(?, weak_foot),
                skill_moves = COALESCE(?, skill_moves),
                international_reputation = COALESCE(?, international_reputation),
                body_type = COALESCE(NULLIF(?, ''), body_type),
                real_face = COALESCE(NULLIF(?, ''), real_face),
                release_clause_m = COALESCE(?, release_clause_m),
                acceleration_type = COALESCE(NULLIF(?, ''), acceleration_type),
                play_styles = COALESCE(NULLIF(?, ''), play_styles),
                specialities = COALESCE(NULLIF(?, ''), specialities),
                roles_json = COALESCE(NULLIF(?, ''), roles_json),
                pace = COALESCE(?, pace),
                shooting = COALESCE(?, shooting),
                passing = COALESCE(?, passing),
                dribbling = COALESCE(?, dribbling),
                defending = COALESCE(?, defending),
                physical = COALESCE(?, physical),
                attributes_json = COALESCE(NULLIF(?, ''), attributes_json),
                source_dataset = 'sofifa-playwright-manual',
                source_version = ?,
                sofifa_roster_status = ?,
                imported_at = ?,
                raw_json = ?
            WHERE id = ?
            """,
            (
                data.get("short_name") or data.get("name"),
                data.get("full_name") or data.get("name"),
                data.get("positions"),
                data.get("club_name"),
                data.get("country_name"),
                int_or_none(data.get("overall_rating")),
                int_or_none(data.get("potential")),
                value_to_millions(data.get("value")),
                value_to_millions(data.get("wage")),
                data.get("transfermarkt_url"),
                data.get("image"),
                int_or_none(data.get("height_cm")),
                int_or_none(data.get("weight_kg")),
                data.get("preferred_foot"),
                int_or_none(data.get("weak_foot")),
                int_or_none(data.get("skill_moves")),
                int_or_none(data.get("international_reputation")),
                data.get("body_type"),
                data.get("real_face"),
                data.get("release_clause_m"),
                data.get("acceleration_type"),
                data.get("play_styles"),
                data.get("specialities"),
                data.get("roles_json"),
                int_or_none(data.get("pace")),
                int_or_none(data.get("shooting")),
                int_or_none(data.get("passing")),
                int_or_none(data.get("dribbling")),
                int_or_none(data.get("defending")),
                int_or_none(data.get("physical")),
                data.get("attributes_json"),
                version_label,
                roster_status,
                now,
                json.dumps(data, ensure_ascii=False),
                row_id,
            ),
        )
        connection.commit()


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


async def wait_until_unblocked(page, timeout_seconds: int) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            title = await page.title()
            content = await page.content()
        except Exception:
            await page.wait_for_timeout(2000)
            continue
        if not is_cloudflare_page(content, title):
            return True
        print("Cloudflare activo. Completa la verificacion en el navegador...")
        await page.wait_for_timeout(5000)
    return False


async def wait_for_player_page(page, row: sqlite3.Row, timeout_seconds: int) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    expected_id = str(row["sofifa_id"])
    expected_name = str(row["name"] or "").split(" ")[0]

    while asyncio.get_running_loop().time() < deadline:
        try:
            title = await page.title()
            content = await page.content()
            current_url = page.url
        except Exception:
            await page.wait_for_timeout(1500)
            continue

        if is_cloudflare_page(content, title):
            await page.wait_for_timeout(2500)
            continue

        has_player_url = f"/player/{expected_id}" in current_url
        has_player_text = expected_name and expected_name.lower() in content.lower()
        has_rating_text = "overall" in content.lower() or "valoraciÃ³n general" in content.lower()
        if has_player_url and (has_player_text or has_rating_text):
            await page.wait_for_timeout(1500)
            return True

        await page.wait_for_timeout(1500)
    return False


async def main() -> None:
    args = parse_args()
    database = Path(args.database)
    targets = load_targets(
        database=database,
        version_label=args.version_label,
        limit=args.limit,
        min_overall=args.min_overall,
        ids=args.ids,
        skip_updated=args.skip_updated,
        only_missing_transfermarkt_url=args.only_missing_transfermarkt_url,
        attributes_only=args.attributes_only,
    )
    print(f"Targets: {len(targets)}")
    if not targets:
        return

    async with async_playwright() as playwright:
        browser = None
        if args.cdp_url:
            browser = await playwright.chromium.connect_over_cdp(args.cdp_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
        else:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=args.profile_dir,
                headless=args.headless,
                viewport={"width": 1400, "height": 1000},
                locale="es-ES",
                timezone_id="America/Argentina/Buenos_Aires",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
            )
        workers = max(1, args.workers)
        keep_pages = max(2, workers)
        pages = [await context.new_page() for _ in range(workers)]
        await close_old_pages(context, pages, keep_pages)

        queue: asyncio.Queue[tuple[int, sqlite3.Row]] = asyncio.Queue()
        for index, row in enumerate(targets, start=1):
            queue.put_nowait((index, row))

        stats = {
            "updated": 0,
            "failed": 0,
            "keep_open": False,
            "stopped": False,
            "consecutive_failures": 0,
        }
        stop_event = asyncio.Event()
        print_lock = asyncio.Lock()
        started_at = time.monotonic()

        async def print_progress(worker_id: int, index: int, row: sqlite3.Row) -> None:
            async with print_lock:
                completed = stats["updated"] + stats["failed"]
                elapsed = time.monotonic() - started_at
                avg_per_done = elapsed / max(completed, 1)
                remaining = len(targets) - completed
                eta = avg_per_done * remaining if completed else 0
                print(
                    f"[W{worker_id} {index}/{len(targets)}] {row['name']} ({row['sofifa_id']}) "
                    f"| ok={stats['updated']} fail={stats['failed']} "
                    f"elapsed={format_duration(elapsed)} eta={format_duration(eta)}"
                )

        async def mark_result(message: str, ok: bool, keep_open: bool = False) -> None:
            async with print_lock:
                if ok:
                    stats["updated"] += 1
                    stats["consecutive_failures"] = 0
                else:
                    stats["failed"] += 1
                    stats["consecutive_failures"] += 1
                    should_stop = args.stop_on_first_failure or (
                        args.stop_after_consecutive_failures > 0
                        and stats["consecutive_failures"] >= args.stop_after_consecutive_failures
                    )
                    if should_stop and not stop_event.is_set():
                        stats["stopped"] = True
                        stop_event.set()
                        while True:
                            try:
                                queue.get_nowait()
                                queue.task_done()
                            except asyncio.QueueEmpty:
                                break
                        message = (
                            f"{message}\n  stop: {stats['consecutive_failures']} fallo(s) "
                            "consecutivo(s), corto la tanda."
                        )
                stats["keep_open"] = stats["keep_open"] or keep_open
                print(message)

        async def scrape_worker(worker_id: int, page) -> None:
            while True:
                if stop_event.is_set():
                    return
                try:
                    index, row = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return

                item_started_at = time.monotonic()
                url = build_sofifa_url(row, args.version_url_part, args.locale)
                await print_progress(worker_id, index, row)
                try:
                    await close_old_pages(context, pages, keep_pages)
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    if not await wait_until_unblocked(page, args.timeout_seconds):
                        await mark_result(
                            "  blocked: timeout esperando verificacion humana "
                            f"took={format_duration(time.monotonic() - item_started_at)}",
                            ok=False,
                            keep_open=args.keep_open_on_failure,
                        )
                        continue
                    if not await wait_for_player_page(page, row, args.timeout_seconds):
                        await mark_result(
                            "  failed: no se estabilizo la pagina del jugador "
                            f"took={format_duration(time.monotonic() - item_started_at)}",
                            ok=False,
                            keep_open=args.keep_open_on_failure,
                        )
                        continue
                    await page.wait_for_timeout(int(args.post_verify_wait * 1000))
                    data = await PlayerScraper.scrape_player_data(page, url)
                    data = await enrich_sofifa_data(page, data)
                    if not data.get("player_id") or not data.get("overall_rating"):
                        async with print_lock:
                            print("  datos incompletos; reintento una vez despues de esperar...")
                        await page.wait_for_timeout(int(args.post_verify_wait * 1000))
                        data = await PlayerScraper.scrape_player_data(page, url)
                        data = await enrich_sofifa_data(page, data)
                        if not data.get("player_id") or not data.get("overall_rating"):
                            await mark_result(
                                "  failed: datos incompletos "
                                f"took={format_duration(time.monotonic() - item_started_at)}",
                                ok=False,
                                keep_open=args.keep_open_on_failure,
                            )
                            continue
                    update_player(
                        database,
                        row["id"],
                        data,
                        args.version_label,
                        args.version_url_part,
                        attributes_only=args.attributes_only,
                    )
                    await mark_result(
                        f"  ok: {data.get('name')} overall={data.get('overall_rating')} "
                        f"value={data.get('value')} tm={'yes' if data.get('transfermarkt_url') else 'no'} "
                        f"took={format_duration(time.monotonic() - item_started_at)}",
                        ok=True,
                    )
                except PlaywrightTimeoutError:
                    await mark_result(
                        f"  failed: timeout took={format_duration(time.monotonic() - item_started_at)}",
                        ok=False,
                        keep_open=args.keep_open_on_failure,
                    )
                except Exception as exc:
                    await mark_result(
                        f"  failed: {type(exc).__name__}: {exc} "
                        f"took={format_duration(time.monotonic() - item_started_at)}",
                        ok=False,
                        keep_open=args.keep_open_on_failure,
                    )
                finally:
                    queue.task_done()
                    await close_old_pages(context, pages, keep_pages)
                    await page.wait_for_timeout(int(args.delay_seconds * 1000))

        await asyncio.gather(
            *(scrape_worker(worker_id, page) for worker_id, page in enumerate(pages, start=1))
        )

        if stats["keep_open"]:
            print("Dejo Chromium abierto por falla. Presiona Enter para cerrarlo.")
            input()
        try:
            if args.cdp_url and browser:
                await browser.close()
            else:
                await context.close()
        except Exception:
            pass
        total_elapsed = time.monotonic() - started_at
        print(
            json.dumps(
                {
                    "updated": stats["updated"],
                    "failed": stats["failed"],
                    "elapsed": format_duration(total_elapsed),
                    "avg_seconds_per_player": round(total_elapsed / max(len(targets), 1), 2),
                    "workers": workers,
                    "stopped_on_first_failure": stats["stopped"],
                    "consecutive_failures": stats["consecutive_failures"],
                    "version": args.version_label,
                    "version_url_part": args.version_url_part,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
