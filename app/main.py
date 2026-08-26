from __future__ import annotations

import base64
import json
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import SOFIFA_LOCALE, SOFIFA_VERSION_URL_PART, TRANSFERMARKT_BATCH_LIMIT
from app.importer import ensure_schema, import_players_csv
from app.modules.players.schemas import GlobalPlayerCreate, GlobalPlayerPatch
from app.modules.scans.schemas import (
    CsvImportRequest,
    FetchRequest,
    SofifaFetchRequest,
    TransfermarktBatchRequest,
    TransfermarktFetchRequest,
)
from app.modules.teams.schemas import (
    TeamAssignPlayer,
    TeamCreate,
    TeamLogoPayload,
    TeamPatch,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "app" / "data"
GLOBAL_PLAYERS_FILE = DATA_DIR / "global_players.json"
GLOBAL_PLAYERS_DB = DATA_DIR / "global_players.sqlite3"
RAW_DATA_DIR = ROOT / "data" / "raw"
PLAYER_IMAGES_DIR = DATA_DIR / "player_images"
TEAM_LOGOS_DIR = DATA_DIR / "team_logos"
FRONTEND_DIR = ROOT / "frontend"
HOME_TEAM_ID = "club-atletico-horizonte"
HOME_TEAM_NAME = "Club Atletico Horizonte"
HOME_TEAM_OWNER = "Leo"
SQLITE_TIMEOUT_SECONDS = 30
GLOBAL_SCHEMA_READY = False
TEAMS_SCHEMA_READY = False
SCHEMA_LOCK = threading.Lock()
GLOBAL_SUMMARY_CACHE_SECONDS = 2
GLOBAL_SUMMARY_CACHE: dict[str, Any] = {"expires_at": 0.0, "summary": None}
BUDGET_M = 300.0

SALARY_BY_OVERALL_M = [
    (90, 16.0),
    (89, 12.0),
    (88, 8.0),
    (87, 7.0),
    (86, 6.0),
    (85, 5.0),
    (84, 4.0),
    (83, 3.0),
    (82, 2.0),
    (81, 1.0),
    (80, 0.75),
    (79, 0.5),
    (78, 0.25),
    (77, 0.1),
    (76, 0.075),
    (75, 0.05),
]


app = FastAPI(title="Eva Peron League Manager")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")


def salary_for_overall(overall: int | None) -> float:
    if overall is None:
        return 0.0
    for minimum, salary in SALARY_BY_OVERALL_M:
        if overall >= minimum:
            return salary
    return 0.025


def normalize_global_player(player: dict[str, Any]) -> dict[str, Any]:
    salary_m = salary_for_overall(player.get("overall"))
    market_m = player.get("market_value_m") or 0.0
    enriched = dict(player)
    enriched["salary_m"] = salary_m
    enriched["total_cost_m"] = round(market_m + salary_m, 3)
    return enriched


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or uuid.uuid4().hex[:8]


def ensure_global_players_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not GLOBAL_PLAYERS_FILE.exists():
        save_global_players([])


def load_global_players() -> list[dict[str, Any]]:
    ensure_global_players_file()
    return json.loads(GLOBAL_PLAYERS_FILE.read_text(encoding="utf-8"))


def save_global_players(players: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    GLOBAL_PLAYERS_FILE.write_text(json.dumps(players, ensure_ascii=False, indent=2), encoding="utf-8")


def build_roster_budget_summary(players: list[dict[str, Any]]) -> dict[str, Any]:
    enriched = [normalize_global_player(player) for player in players]
    spent_m = round(sum(player["total_cost_m"] for player in enriched), 3)
    salaries_m = round(sum(player["salary_m"] for player in enriched), 3)
    market_m = round(sum((player.get("market_value_m") or 0.0) for player in enriched), 3)
    return {
        "budget_m": BUDGET_M,
        "spent_m": spent_m,
        "market_m": market_m,
        "salaries_m": salaries_m,
        "remaining_m": round(BUDGET_M - spent_m, 3),
        "player_count": len(enriched),
    }


def build_global_summary(players: list[dict[str, Any]]) -> dict[str, Any]:
    enriched = [normalize_global_player(player) for player in players]
    completed_market = [player for player in enriched if player.get("market_value_m") is not None]
    completed_sofifa = [player for player in enriched if player.get("overall") is not None]
    return {
        "total_count": len(enriched),
        "sofifa_completed": len(completed_sofifa),
        "market_completed": len(completed_market),
        "avg_overall": round(sum(player["overall"] for player in completed_sofifa) / len(completed_sofifa), 2)
        if completed_sofifa
        else None,
        "avg_total_cost_m": round(sum(player["total_cost_m"] for player in enriched) / len(enriched), 3)
        if enriched
        else 0,
    }


def sqlite_available() -> bool:
    return GLOBAL_PLAYERS_DB.exists()


def sqlite_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(GLOBAL_PLAYERS_DB, timeout=SQLITE_TIMEOUT_SECONDS)
    connection.execute(f"PRAGMA busy_timeout = {SQLITE_TIMEOUT_SECONDS * 1000}")
    return connection


def clear_global_summary_cache() -> None:
    GLOBAL_SUMMARY_CACHE["expires_at"] = 0.0
    GLOBAL_SUMMARY_CACHE["summary"] = None


def ensure_global_sqlite_schema() -> None:
    global GLOBAL_SCHEMA_READY
    if not sqlite_available():
        return
    if GLOBAL_SCHEMA_READY:
        return
    with SCHEMA_LOCK:
        if GLOBAL_SCHEMA_READY:
            return
        with sqlite_connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            ensure_schema(connection)
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(global_players)").fetchall()
            }
            extra_columns = {
                "transfermarkt_checked_at": "TEXT",
                "transfermarkt_player_name": "TEXT",
                "transfermarkt_last_update": "TEXT",
                "transfermarkt_validation_status": "TEXT",
                "transfermarkt_validation_score": "TEXT",
                "transfermarkt_error_at": "TEXT",
                "transfermarkt_error_detail": "TEXT",
                "sofifa_roster_status": "TEXT",
                "player_kind": "TEXT",
                "search_text": "TEXT",
            }
            for column, definition in extra_columns.items():
                if column not in columns:
                    connection.execute(f"ALTER TABLE global_players ADD COLUMN {column} {definition}")
            connection.row_factory = sqlite3.Row
            rows_to_index = connection.execute(
                """
                SELECT
                    id,
                    name,
                    long_name,
                    club,
                    nationality,
                    sofifa_id,
                    position,
                    transfermarkt_player_name,
                    transfermarkt_url
                FROM global_players
                WHERE search_text IS NULL OR search_text = ''
                """
            ).fetchall()
            if rows_to_index:
                connection.executemany(
                    "UPDATE global_players SET search_text = ? WHERE id = ?",
                    [
                        (build_global_search_text(dict(row)), row["id"])
                        for row in rows_to_index
                    ],
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_global_players_overall_name ON global_players(overall, name)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_global_players_search_text ON global_players(search_text)"
            )
            connection.commit()
        GLOBAL_SCHEMA_READY = True


def ensure_teams_sqlite_schema() -> None:
    global TEAMS_SCHEMA_READY
    if TEAMS_SCHEMA_READY:
        return
    with SCHEMA_LOCK:
        if TEAMS_SCHEMA_READY:
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with sqlite_connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            ensure_schema(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS teams (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    owner TEXT DEFAULT '',
                    logo_url TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS team_players (
                    player_id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    sort_order INTEGER DEFAULT 0,
                    notes TEXT DEFAULT '',
                    FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_team_players_team ON team_players(team_id)")
            existing = connection.execute(
                "SELECT id FROM teams WHERE id = ? OR lower(name) = lower(?)",
                (HOME_TEAM_ID, HOME_TEAM_NAME),
            ).fetchone()
            if not existing:
                now = datetime.now(timezone.utc).isoformat()
                connection.execute(
                    """
                    INSERT INTO teams (id, name, owner, logo_url, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (HOME_TEAM_ID, HOME_TEAM_NAME, HOME_TEAM_OWNER, "", now, now),
                )
            else:
                connection.execute(
                    """
                    UPDATE teams
                    SET name = ?, owner = ?, updated_at = COALESCE(updated_at, ?)
                    WHERE id = ?
                    """,
                    (HOME_TEAM_NAME, HOME_TEAM_OWNER, datetime.now(timezone.utc).isoformat(), HOME_TEAM_ID),
                )
            connection.commit()
        TEAMS_SCHEMA_READY = True


def team_logo_url(team_id: str) -> str:
    for extension in ("png", "jpg", "jpeg", "webp"):
        path = TEAM_LOGOS_DIR / f"{team_id}.{extension}"
        if path.exists():
            return f"/api/teams/{team_id}/logo"
    return ""


def sqlite_row_to_team(row: sqlite3.Row, roster_count: int | None = None) -> dict[str, Any]:
    logo_url = optional_text(row, "logo_url") or team_logo_url(row["id"])
    return {
        "id": row["id"],
        "name": row["name"] or "",
        "owner": optional_text(row, "owner"),
        "logo_url": logo_url,
        "roster_count": int(roster_count if roster_count is not None else optional_int(row, "roster_count") or 0),
        "created_at": optional_text(row, "created_at"),
        "updated_at": optional_text(row, "updated_at"),
    }


def list_teams_sqlite() -> list[dict[str, Any]]:
    ensure_teams_sqlite_schema()
    with sqlite_connect() as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT t.*, COUNT(tp.player_id) AS roster_count
            FROM teams t
            LEFT JOIN team_players tp ON tp.team_id = t.id
            GROUP BY t.id
            ORDER BY lower(t.name)
            """
        ).fetchall()
    return [sqlite_row_to_team(row) for row in rows]


def get_team_sqlite(team_id: str) -> dict[str, Any] | None:
    ensure_teams_sqlite_schema()
    with sqlite_connect() as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT t.*, COUNT(tp.player_id) AS roster_count
            FROM teams t
            LEFT JOIN team_players tp ON tp.team_id = t.id
            WHERE t.id = ?
            GROUP BY t.id
            """,
            (team_id,),
        ).fetchone()
    return sqlite_row_to_team(row) if row else None


def find_team_by_name_sqlite(name: str) -> dict[str, Any] | None:
    normalized = normalize_person_name(name)
    for team in list_teams_sqlite():
        team_norm = normalize_person_name(team["name"])
        if team_norm == normalized or normalized in team_norm:
            return team
    return None


def assign_global_player_to_team(player: dict[str, Any], team_id: str, force: bool = False) -> dict[str, Any]:
    team = get_team_sqlite(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Equipo no encontrado.")
    if player.get("player_kind") == "generic_unlicensed":
        raise HTTPException(status_code=400, detail="No se pueden asignar jugadores genericos/no licenciados.")
    with sqlite_connect() as connection:
        connection.row_factory = sqlite3.Row
        existing = connection.execute(
            """
            SELECT tp.team_id, t.name
            FROM team_players tp
            JOIN teams t ON t.id = tp.team_id
            WHERE tp.player_id = ?
            """,
            (player["id"],),
        ).fetchone()
        if existing and existing["team_id"] != team_id and not force:
            raise HTTPException(
                status_code=409,
                detail=f"{player['name']} ya pertenece a {existing['name']}. Quitalo de ese equipo antes de reasignarlo.",
            )
        if existing and existing["team_id"] == team_id:
            updated_player = get_sqlite_global_player(player["id"]) or player
            return {"team": team, "player": updated_player, "already_assigned": True}
        if existing and force:
            connection.execute("DELETE FROM team_players WHERE player_id = ?", (player["id"],))
        next_order = connection.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM team_players WHERE team_id = ?",
            (team_id,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO team_players (player_id, team_id, assigned_at, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (player["id"], team_id, datetime.now(timezone.utc).isoformat(), next_order),
        )
        connection.commit()
    updated_player = get_sqlite_global_player(player["id"]) or player
    return {"team": get_team_sqlite(team_id), "player": updated_player, "already_assigned": False}


def unassign_global_player_from_team(player_id: str, team_id: str | None = None) -> dict[str, Any]:
    player = get_sqlite_global_player(player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Jugador global no encontrado.")
    with sqlite_connect() as connection:
        if team_id:
            cursor = connection.execute(
                "DELETE FROM team_players WHERE team_id = ? AND player_id = ?",
                (team_id, player_id),
            )
        else:
            cursor = connection.execute("DELETE FROM team_players WHERE player_id = ?", (player_id,))
        connection.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Ese jugador no esta asignado a ese equipo.")
    return {"ok": True, "player": get_sqlite_global_player(player_id) or player}


def optional_int(row: sqlite3.Row, column: str) -> int | None:
    if column not in row.keys() or row[column] in (None, ""):
        return None
    return int(row[column])


def optional_float(row: sqlite3.Row, column: str) -> float | None:
    if column not in row.keys() or row[column] in (None, ""):
        return None
    return float(row[column])


def optional_text(row: sqlite3.Row, column: str) -> str:
    return row[column] if column in row.keys() and row[column] not in (None, "") else ""


def optional_json(row: sqlite3.Row, column: str) -> Any:
    if column not in row.keys() or row[column] in (None, ""):
        return None
    try:
        return json.loads(row[column])
    except (TypeError, json.JSONDecodeError):
        return None


def sqlite_row_to_global_player(row: sqlite3.Row) -> dict[str, Any]:
    market_value = row["market_value_m"]
    wage = row["wage_m"]
    attributes = optional_json(row, "attributes_json") or {}
    if "goalkeeping_reflexes" in attributes and "goalkeeping_gk_reflexes" not in attributes:
        attributes["goalkeeping_gk_reflexes"] = attributes["goalkeeping_reflexes"]
    player = {
        "id": row["id"],
        "name": row["name"] or row["long_name"] or "",
        "long_name": row["long_name"] or "",
        "position": row["position"] or "",
        "club": row["club"] or "",
        "nationality": row["nationality"] or "",
        "sofifa_id": row["sofifa_id"] or "",
        "sofifa_url": (
            f"https://sofifa.com/player/{row['sofifa_id']}/x/{SOFIFA_VERSION_URL_PART}/?hl={SOFIFA_LOCALE}"
            if row["sofifa_id"]
            else ""
        ),
        "sofifa_version": row["source_version"] or "",
        "transfermarkt_url": optional_text(row, "transfermarkt_url"),
        "image_url": optional_text(row, "image_url"),
        "overall": int(row["overall"]) if row["overall"] not in (None, "") else None,
        "potential": int(row["potential"]) if row["potential"] not in (None, "") else None,
        "market_value_m": float(market_value) if market_value not in (None, "") else None,
        "wage_m": float(wage) if wage not in (None, "") else None,
        "market_value_currency": "EUR",
        "market_value_checked_at": optional_text(row, "transfermarkt_checked_at") or row["imported_at"] or "",
        "transfermarkt_player_name": optional_text(row, "transfermarkt_player_name"),
        "transfermarkt_last_update": optional_text(row, "transfermarkt_last_update"),
        "transfermarkt_validation_status": optional_text(row, "transfermarkt_validation_status"),
        "transfermarkt_validation_score": optional_float(row, "transfermarkt_validation_score"),
        "transfermarkt_error_at": optional_text(row, "transfermarkt_error_at"),
        "transfermarkt_error_detail": optional_text(row, "transfermarkt_error_detail"),
        "age": optional_int(row, "age"),
        "height_cm": optional_int(row, "height_cm"),
        "weight_kg": optional_int(row, "weight_kg"),
        "preferred_foot": optional_text(row, "preferred_foot"),
        "weak_foot": optional_int(row, "weak_foot"),
        "skill_moves": optional_int(row, "skill_moves"),
        "international_reputation": optional_int(row, "international_reputation"),
        "body_type": optional_text(row, "body_type"),
        "real_face": optional_text(row, "real_face"),
        "release_clause_m": optional_float(row, "release_clause_m"),
        "acceleration_type": optional_text(row, "acceleration_type"),
        "play_styles": optional_text(row, "play_styles"),
        "specialities": optional_text(row, "specialities"),
        "roles": optional_json(row, "roles_json") or [],
        "pace": int(row["pace"]) if row["pace"] not in (None, "") else None,
        "shooting": int(row["shooting"]) if row["shooting"] not in (None, "") else None,
        "passing": int(row["passing"]) if row["passing"] not in (None, "") else None,
        "dribbling": int(row["dribbling"]) if row["dribbling"] not in (None, "") else None,
        "defending": int(row["defending"]) if row["defending"] not in (None, "") else None,
        "physical": int(row["physical"]) if row["physical"] not in (None, "") else None,
        "tags": ["csv-import"],
        "notes": "",
        "source_dataset": row["source_dataset"] or "",
        "source_version": row["source_version"] or "",
        "sofifa_roster_status": optional_text(row, "sofifa_roster_status") or "active",
        "player_kind": optional_text(row, "player_kind") or "real",
        "assigned_team_id": optional_text(row, "assigned_team_id"),
        "assigned_team_name": optional_text(row, "assigned_team_name"),
        "assigned_team_owner": optional_text(row, "assigned_team_owner"),
        "assigned_team_logo_url": optional_text(row, "assigned_team_logo_url"),
        "imported_at": row["imported_at"] or "",
        "attributes": attributes,
    }
    return normalize_global_player(player)


def query_sqlite_global_players(
    q: str = "",
    position: str = "",
    tm_status: str = "all",
    min_overall: int | None = None,
    max_value_m: float | None = None,
    page: int = 1,
    limit: int = 100,
) -> dict[str, Any]:
    ensure_global_sqlite_schema()
    ensure_teams_sqlite_schema()
    page = max(page, 1)
    limit = min(max(limit, 1), 250)
    offset = (page - 1) * limit
    where = []
    params: list[Any] = []
    query_filter = normalize_person_name(q.strip())

    if position.strip():
        where.append("gp.position LIKE ?")
        params.append(f"%{position.strip()}%")
    if min_overall is not None:
        where.append("CAST(gp.overall AS INTEGER) >= ?")
        params.append(min_overall)
    if max_value_m is not None:
        where.append("(gp.market_value_m IS NULL OR CAST(gp.market_value_m AS REAL) <= ?)")
        params.append(max_value_m)
    if tm_status == "generic":
        where.append("COALESCE(gp.player_kind, 'real') = 'generic_unlicensed'")
    else:
        where.append("COALESCE(gp.player_kind, 'real') != 'generic_unlicensed'")

    if tm_status == "failed":
        where.append(
            "gp.transfermarkt_error_detail IS NOT NULL AND gp.transfermarkt_error_detail != '' "
            "AND (gp.sofifa_roster_status IS NULL OR gp.sofifa_roster_status != 'retired') "
            "AND (gp.transfermarkt_validation_status IS NULL OR gp.transfermarkt_validation_status != 'retired')"
        )
    elif tm_status == "warning":
        where.append("gp.transfermarkt_validation_status = 'warning'")
    elif tm_status == "match":
        where.append("gp.transfermarkt_validation_status = 'match'")
    elif tm_status == "no_value":
        where.append("gp.transfermarkt_validation_status = 'no_value'")
    elif tm_status == "retired":
        where.append("(gp.sofifa_roster_status = 'retired' OR gp.transfermarkt_validation_status = 'retired')")
    elif tm_status == "pending":
        where.append(
            "gp.transfermarkt_url IS NOT NULL AND gp.transfermarkt_url != '' "
            "AND (gp.transfermarkt_checked_at IS NULL OR gp.transfermarkt_checked_at = '') "
            "AND (gp.transfermarkt_error_detail IS NULL OR gp.transfermarkt_error_detail = '')"
        )
    if query_filter:
        for token in query_filter.split():
            where.append("COALESCE(gp.search_text, '') LIKE ?")
            params.append(f"%{token}%")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    with sqlite_connect() as connection:
        connection.row_factory = sqlite3.Row
        select_sql = f"""
        SELECT
            gp.*,
            tp.team_id AS assigned_team_id,
            t.name AS assigned_team_name,
            t.owner AS assigned_team_owner,
            t.logo_url AS assigned_team_logo_url
        FROM global_players gp
        LEFT JOIN team_players tp ON tp.player_id = gp.id
        LEFT JOIN teams t ON t.id = tp.team_id
        {where_sql}
        ORDER BY CAST(gp.overall AS INTEGER) DESC, gp.name ASC
        """
        total_count = connection.execute(
            f"""
            SELECT COUNT(*)
            FROM global_players gp
            LEFT JOIN team_players tp ON tp.player_id = gp.id
            LEFT JOIN teams t ON t.id = tp.team_id
            {where_sql}
            """,
            params,
        ).fetchone()[0]
        rows = connection.execute(
            f"{select_sql} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        summary = get_sqlite_global_summary(connection)
    return {
        "players": [sqlite_row_to_global_player(row) for row in rows],
        "summary": summary,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_count,
            "pages": (total_count + limit - 1) // limit,
        },
    }


def get_sqlite_global_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    cached = GLOBAL_SUMMARY_CACHE.get("summary")
    if cached and time.monotonic() < float(GLOBAL_SUMMARY_CACHE.get("expires_at") or 0):
        return dict(cached)

    summary_row = connection.execute(
        """
        SELECT
            COUNT(*) AS total_count,
            SUM(CASE WHEN overall IS NOT NULL AND overall != '' THEN 1 ELSE 0 END) AS sofifa_completed,
            SUM(CASE WHEN market_value_m IS NOT NULL AND market_value_m != '' THEN 1 ELSE 0 END) AS market_completed,
            SUM(CASE WHEN transfermarkt_url IS NOT NULL AND transfermarkt_url != '' THEN 1 ELSE 0 END) AS transfermarkt_available,
            SUM(CASE WHEN transfermarkt_checked_at IS NOT NULL AND transfermarkt_checked_at != '' THEN 1 ELSE 0 END) AS transfermarkt_completed,
            SUM(CASE WHEN transfermarkt_error_detail IS NOT NULL AND transfermarkt_error_detail != '' AND (sofifa_roster_status IS NULL OR sofifa_roster_status != 'retired') AND (transfermarkt_validation_status IS NULL OR transfermarkt_validation_status != 'retired') THEN 1 ELSE 0 END) AS transfermarkt_failed,
            SUM(CASE WHEN transfermarkt_validation_status = 'warning' THEN 1 ELSE 0 END) AS transfermarkt_warnings,
            SUM(CASE WHEN transfermarkt_validation_status = 'match' THEN 1 ELSE 0 END) AS transfermarkt_matches,
            SUM(CASE WHEN transfermarkt_validation_status = 'no_value' THEN 1 ELSE 0 END) AS transfermarkt_no_value,
            SUM(CASE WHEN sofifa_roster_status = 'retired' OR transfermarkt_validation_status = 'retired' THEN 1 ELSE 0 END) AS sofifa_retired,
            AVG(CASE WHEN overall IS NOT NULL AND overall != '' THEN CAST(overall AS REAL) END) AS avg_overall,
            AVG(CASE WHEN market_value_m IS NOT NULL AND market_value_m != '' THEN CAST(market_value_m AS REAL) END) AS avg_market_value_m
        FROM global_players
        WHERE COALESCE(player_kind, 'real') != 'generic_unlicensed'
        """
    ).fetchone()
    generic_count = connection.execute(
        "SELECT COUNT(*) FROM global_players WHERE COALESCE(player_kind, 'real') = 'generic_unlicensed'"
    ).fetchone()[0]
    metadata = connection.execute(
        """
        SELECT *
        FROM import_metadata
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    summary = {
        "total_count": summary_row["total_count"],
        "sofifa_completed": summary_row["sofifa_completed"] or 0,
        "market_completed": summary_row["market_completed"] or 0,
        "transfermarkt_available": summary_row["transfermarkt_available"] or 0,
        "transfermarkt_completed": summary_row["transfermarkt_completed"] or 0,
        "transfermarkt_failed": summary_row["transfermarkt_failed"] or 0,
        "transfermarkt_warnings": summary_row["transfermarkt_warnings"] or 0,
        "transfermarkt_matches": summary_row["transfermarkt_matches"] or 0,
        "transfermarkt_no_value": summary_row["transfermarkt_no_value"] or 0,
        "sofifa_retired": summary_row["sofifa_retired"] or 0,
        "generic_unlicensed": generic_count,
        "avg_overall": round(summary_row["avg_overall"], 2) if summary_row["avg_overall"] is not None else None,
        "avg_market_value_m": round(summary_row["avg_market_value_m"], 3)
        if summary_row["avg_market_value_m"] is not None
        else None,
        "storage": "sqlite",
        "metadata": dict(metadata) if metadata else None,
    }
    GLOBAL_SUMMARY_CACHE["summary"] = dict(summary)
    GLOBAL_SUMMARY_CACHE["expires_at"] = time.monotonic() + GLOBAL_SUMMARY_CACHE_SECONDS
    return summary


def global_payload_to_sqlite_row(player_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    today = date.today().isoformat()
    return {
        "id": player_id,
        "sofifa_id": payload.get("sofifa_id") or player_id.replace("sofifa-", ""),
        "name": payload.get("name") or "",
        "long_name": payload.get("long_name") or payload.get("name") or "",
        "position": payload.get("position") or "",
        "club": payload.get("club") or "",
        "nationality": payload.get("nationality") or "",
        "overall": payload.get("overall"),
        "potential": payload.get("potential"),
        "market_value_m": payload.get("market_value_m"),
        "wage_m": payload.get("wage_m"),
        "transfermarkt_url": payload.get("transfermarkt_url") or "",
        "image_url": payload.get("image_url") or "",
        "age": payload.get("age"),
        "height_cm": payload.get("height_cm"),
        "weight_kg": payload.get("weight_kg"),
        "preferred_foot": payload.get("preferred_foot") or "",
        "weak_foot": payload.get("weak_foot"),
        "skill_moves": payload.get("skill_moves"),
        "international_reputation": payload.get("international_reputation"),
        "body_type": payload.get("body_type") or "",
        "real_face": payload.get("real_face") or "",
        "release_clause_m": payload.get("release_clause_m"),
        "acceleration_type": payload.get("acceleration_type") or "",
        "play_styles": payload.get("play_styles") or "",
        "specialities": payload.get("specialities") or "",
        "roles_json": json.dumps(payload.get("roles") or [], ensure_ascii=False),
        "pace": payload.get("pace"),
        "shooting": payload.get("shooting"),
        "passing": payload.get("passing"),
        "dribbling": payload.get("dribbling"),
        "defending": payload.get("defending"),
        "physical": payload.get("physical"),
        "attributes_json": json.dumps(payload.get("attributes") or {}, ensure_ascii=False),
        "source_dataset": "manual",
        "source_version": payload.get("sofifa_version") or "manual",
        "sofifa_roster_status": payload.get("sofifa_roster_status") or "active",
        "player_kind": payload.get("player_kind") or "real",
        "search_text": build_global_search_text(payload),
        "imported_at": today,
        "raw_json": json.dumps(payload, ensure_ascii=False),
    }


def get_sqlite_global_player(player_id: str) -> dict[str, Any] | None:
    ensure_global_sqlite_schema()
    ensure_teams_sqlite_schema()
    with sqlite_connect() as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT
                gp.*,
                tp.team_id AS assigned_team_id,
                t.name AS assigned_team_name,
                t.owner AS assigned_team_owner,
                t.logo_url AS assigned_team_logo_url
            FROM global_players gp
            LEFT JOIN team_players tp ON tp.player_id = gp.id
            LEFT JOIN teams t ON t.id = tp.team_id
            WHERE gp.id = ?
            """,
            (player_id,),
        ).fetchone()
    return sqlite_row_to_global_player(row) if row else None


def get_sqlite_global_player_by_sofifa_id(sofifa_id: str) -> dict[str, Any] | None:
    clean_id = str(sofifa_id or "").strip()
    if not clean_id:
        return None
    ensure_global_sqlite_schema()
    ensure_teams_sqlite_schema()
    with sqlite_connect() as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT
                gp.*,
                tp.team_id AS assigned_team_id,
                t.name AS assigned_team_name,
                t.owner AS assigned_team_owner,
                t.logo_url AS assigned_team_logo_url
            FROM global_players gp
            LEFT JOIN team_players tp ON tp.player_id = gp.id
            LEFT JOIN teams t ON t.id = tp.team_id
            WHERE gp.sofifa_id = ?
              AND COALESCE(gp.player_kind, 'real') != 'generic_unlicensed'
            LIMIT 1
            """,
            (clean_id,),
        ).fetchone()
    return sqlite_row_to_global_player(row) if row else None


def resolve_global_player_for_assignment(identifier: str) -> dict[str, Any]:
    clean_identifier = str(identifier or "").strip()
    if not clean_identifier:
        raise HTTPException(status_code=400, detail="Ingresa nombre, ID SoFIFA o jugador global.")

    direct_player = get_sqlite_global_player(clean_identifier) or get_sqlite_global_player_by_sofifa_id(clean_identifier)
    if direct_player:
        return direct_player

    normalized_identifier = normalize_person_name(clean_identifier)
    matches = query_sqlite_global_players(q=clean_identifier, page=1, limit=20)["players"]
    exact_name = next(
        (player for player in matches if normalize_person_name(player.get("name", "")) == normalized_identifier),
        None,
    )
    if exact_name:
        return exact_name
    if matches:
        return matches[0]
    raise HTTPException(status_code=404, detail="Jugador global no encontrado.")


def build_sofifa_image_url(sofifa_id: str) -> str:
    clean_id = re.sub(r"\D", "", sofifa_id or "")
    if len(clean_id) < 4:
        return ""
    return f"https://cdn.sofifa.net/players/{clean_id[:-3]}/{clean_id[-3:]}/26_360.png"


def insert_sqlite_global_player(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_global_sqlite_schema()
    sofifa_id = payload.get("sofifa_id") or ""
    player_id = f"sofifa-{sofifa_id}" if sofifa_id else f"manual-{slugify(payload['name'])}-{uuid.uuid4().hex[:6]}"
    row = global_payload_to_sqlite_row(player_id, payload)
    with sqlite_connect() as connection:
        columns = list(row.keys())
        placeholders = ", ".join("?" for _ in columns)
        connection.execute(
            f"INSERT OR REPLACE INTO global_players ({', '.join(columns)}) VALUES ({placeholders})",
            [row[column] for column in columns],
        )
        connection.commit()
    clear_global_summary_cache()
    created = get_sqlite_global_player(player_id)
    if not created:
        raise HTTPException(status_code=500, detail="No se pudo crear el jugador.")
    return created


def update_sqlite_global_player(player_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_global_sqlite_schema()
    existing = get_sqlite_global_player(player_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Global player not found")
    merged = {**existing, **payload}
    row = global_payload_to_sqlite_row(player_id, merged)
    with sqlite_connect() as connection:
        assignments = ", ".join(f"{column} = ?" for column in row if column != "id")
        values = [value for column, value in row.items() if column != "id"]
        connection.execute(
            f"UPDATE global_players SET {assignments} WHERE id = ?",
            [*values, player_id],
        )
        connection.commit()
    clear_global_summary_cache()
    updated = get_sqlite_global_player(player_id)
    if not updated:
        raise HTTPException(status_code=500, detail="No se pudo actualizar el jugador.")
    return updated


def delete_sqlite_global_player(player_id: str) -> dict[str, Any]:
    ensure_teams_sqlite_schema()
    with sqlite_connect() as connection:
        connection.execute("DELETE FROM team_players WHERE player_id = ?", (player_id,))
        cursor = connection.execute("DELETE FROM global_players WHERE id = ?", (player_id,))
        connection.commit()
    clear_global_summary_cache()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Global player not found")
    return {"ok": True}


def parse_money_to_millions(text: str) -> float | None:
    cleaned = text.replace("\xa0", " ").strip()
    match = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*(bn|mil\.?|m|mill\.?|k)?", cleaned, re.I)
    if not match:
        return None
    raw_value = match.group(1)
    if "," in raw_value:
        value = float(raw_value.replace(".", "").replace(",", "."))
    else:
        value = float(raw_value)
    suffix = (match.group(2) or "").lower()
    if suffix.startswith("bn"):
        return round(value * 1000, 3)
    if suffix.startswith("k") or suffix.startswith("mil"):
        return round(value / 1000, 3)
    return round(value, 3)


def normalize_person_name(value: str) -> str:
    value = re.sub(r"#[0-9]+\s*", "", value or "")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.lower()
    replacements = {
        "Ã¡": "a",
        "Ã©": "e",
        "Ã­": "i",
        "Ã³": "o",
        "Ãº": "u",
        "Ã¼": "u",
        "Ã±": "n",
        "Ã§": "c",
        "Ã£": "a",
        "Ãµ": "o",
        "Ã¸": "o",
        "Å‚": "l",
        "ÃŸ": "ss",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def build_global_search_text(payload: dict[str, Any]) -> str:
    return normalize_person_name(
        " ".join(
            str(payload.get(key) or "")
            for key in [
                "name",
                "long_name",
                "club",
                "nationality",
                "sofifa_id",
                "position",
                "transfermarkt_player_name",
                "transfermarkt_url",
            ]
        )
    )


def _token_similarity(token_a: str, token_b: str) -> float:
    if not token_a or not token_b:
        return 0.0
    if token_a == token_b:
        return 1.0
    # Trata un token de una sola letra como inicial (ej. "Vishnu P V" vs "Vishnu Puthiya Valappill").
    if len(token_a) == 1 and token_b[0] == token_a:
        return 0.92
    if len(token_b) == 1 and token_a[0] == token_b:
        return 0.92
    return SequenceMatcher(None, token_a, token_b).ratio()


def compare_player_names(expected: str, actual: str) -> dict[str, Any]:
    expected_norm = normalize_person_name(expected)
    actual_norm = normalize_person_name(actual)
    if not expected_norm or not actual_norm:
        return {"status": "unchecked", "score": 0.0}

    expected_tokens = expected_norm.split()
    actual_tokens = actual_norm.split()
    expected_set = set(expected_tokens)
    actual_set = set(actual_tokens)
    if expected_set and actual_set and (
        expected_set.issubset(actual_set) or actual_set.issubset(expected_set)
    ):
        meaningful = [token for token in expected_set & actual_set if len(token) > 2]
        if meaningful:
            return {"status": "match", "score": 0.8}

    # Comparacion por token (sin importar el orden), tolera abreviaturas, iniciales
    # y pequenas diferencias de transliteracion (ej. nombre y apellido invertidos,
    # o una letra distinta dentro de un mismo token).
    per_token_scores = [
        max((_token_similarity(token, other) for other in actual_tokens), default=0.0)
        for token in expected_tokens
    ]
    token_score = sum(per_token_scores) / len(per_token_scores) if per_token_scores else 0.0

    overlap = len(expected_set & actual_set) / max(len(expected_set), 1)
    ratio = SequenceMatcher(None, expected_norm, actual_norm).ratio()
    score = round(max(overlap, ratio, token_score), 3)
    return {
        "status": "match" if score >= 0.62 else "warning",
        "score": score,
    }


def best_transfermarkt_name_match(expected_name: str, candidates: list[str]) -> dict[str, Any]:
    unique_candidates = []
    seen = set()
    for candidate in candidates:
        clean = re.sub(r"\s+", " ", candidate or "").strip()
        if not clean:
            continue
        key = normalize_person_name(clean)
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(clean)

    if not unique_candidates:
        validation = compare_player_names(expected_name, "")
        return {"name": "", "validation": validation}

    scored = [
        (compare_player_names(expected_name, candidate), candidate)
        for candidate in unique_candidates
    ]
    scored.sort(key=lambda item: item[0]["score"], reverse=True)
    validation, name = scored[0]
    return {"name": name, "validation": validation}


def build_sofifa_url(url_or_id: str) -> str:
    value = url_or_id.strip()
    if not value:
        raise HTTPException(status_code=400, detail="Ingresa una URL o ID de SoFIFA")
    if value.isdigit():
        return f"https://sofifa.com/player/{value}/x/{SOFIFA_VERSION_URL_PART}/?hl={SOFIFA_LOCALE}"
    if value.startswith("http://") or value.startswith("https://"):
        return value
    raise HTTPException(status_code=400, detail="El valor debe ser una URL o ID numerico de SoFIFA")


def parse_sofifa_player(html: str, source_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    if "Just a moment" in text or "Cloudflare" in text:
        raise HTTPException(
            status_code=502,
            detail="SoFIFA bloqueo la consulta automatica. Proba de nuevo mas tarde o carga los datos manualmente.",
        )

    title = soup.find("title")
    title_text = title.get_text(" ", strip=True) if title else ""
    name = re.sub(r"\s*-\s*(FC|FIFA).*$", "", title_text, flags=re.I).strip()
    if not name:
        h1 = soup.find("h1")
        name = h1.get_text(" ", strip=True) if h1 else ""

    sofifa_id_match = re.search(r"/player/(\d+)", source_url)
    version_match = re.search(r"/player/\d+/(?:[^/]+/)?(\d{6})(?:[/?#]|$)", source_url)
    if not version_match:
        version_match = re.search(r"(\d{2})\s*(?:jul|juil|lip|de jul|jul\.)\s*\.?\s*(2026)", text, re.I)

    def number_before(label: str) -> int | None:
        match = re.search(rf"\b([1-9][0-9])\s+{label}\b", text, re.I)
        return int(match.group(1)) if match else None

    def number_after(label: str) -> int | None:
        match = re.search(rf"\b{label}\s+([1-9][0-9])\b", text, re.I)
        return int(match.group(1)) if match else None

    positions = []
    for item in soup.select(".pos, span[class*='pos']"):
        value = item.get_text(" ", strip=True)
        if value and len(value) <= 4 and value not in positions:
            positions.append(value)

    club = ""
    club_link = soup.select_one("a[href*='/team/']")
    if club_link:
        club = club_link.get_text(" ", strip=True)

    return {
        "name": name,
        "sofifa_id": sofifa_id_match.group(1) if sofifa_id_match else "",
        "sofifa_url": source_url,
        "sofifa_version": version_match.group(1) if version_match else "",
        "position": ", ".join(positions),
        "club": club,
        "overall": number_before("Overall rating") or number_after("Overall rating"),
        "pace": number_before("Pace"),
        "shooting": number_before("Shooting"),
        "passing": number_before("Passing"),
        "dribbling": number_before("Dribbling"),
        "defending": number_before("Defending"),
        "physical": number_before("Physical"),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/global-players")
def get_global_players(
    q: str = "",
    position: str = "",
    tm_status: str = "all",
    min_overall: int | None = None,
    max_value_m: float | None = None,
    page: int = 1,
    limit: int = 100,
) -> dict[str, Any]:
    if sqlite_available():
        return query_sqlite_global_players(q, position, tm_status, min_overall, max_value_m, page, limit)

    players = [normalize_global_player(player) for player in load_global_players()]
    query = normalize_person_name(q.strip())
    position_filter = normalize_person_name(position.strip())

    def matches(player: dict[str, Any]) -> bool:
        haystack = " ".join(
            str(player.get(key) or "")
            for key in ["name", "position", "club", "nationality", "notes", "sofifa_id"]
        )
        haystack = normalize_person_name(haystack)
        if query and query not in haystack:
            return False
        if position_filter and position_filter not in normalize_person_name(str(player.get("position") or "")):
            return False
        if tm_status == "failed" and not player.get("transfermarkt_error_detail"):
            return False
        if tm_status == "warning" and player.get("transfermarkt_validation_status") != "warning":
            return False
        if tm_status == "match" and player.get("transfermarkt_validation_status") != "match":
            return False
        if tm_status == "no_value" and player.get("transfermarkt_validation_status") != "no_value":
            return False
        if tm_status == "pending" and (
            not player.get("transfermarkt_url")
            or player.get("market_value_checked_at")
            or player.get("transfermarkt_error_detail")
        ):
            return False
        if min_overall is not None and (player.get("overall") or 0) < min_overall:
            return False
        if max_value_m is not None and (player.get("market_value_m") or 0) > max_value_m:
            return False
        return True

    filtered = [player for player in players if matches(player)]
    filtered.sort(key=lambda player: (player.get("name") or "").lower())
    return {
        "players": filtered,
        "summary": {**build_global_summary(players), "storage": "json", "metadata": None},
        "pagination": {"page": 1, "limit": len(filtered), "total": len(filtered), "pages": 1},
    }


@app.post("/api/global-players/import-csv")
def import_global_players_csv(payload: CsvImportRequest) -> dict[str, Any]:
    csv_path = Path(payload.csv_path)
    if not csv_path.is_absolute():
        csv_path = ROOT / csv_path
    try:
        result = import_players_csv(
            csv_path=csv_path,
            database_path=GLOBAL_PLAYERS_DB,
            source_dataset=payload.source_dataset,
            source_version=payload.source_version,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    clear_global_summary_cache()
    return {"ok": True, "result": result}


@app.post("/api/global-players")
def create_global_player(payload: GlobalPlayerCreate) -> dict[str, Any]:
    if sqlite_available():
        return insert_sqlite_global_player(payload.model_dump())

    players = load_global_players()
    new_player = payload.model_dump()
    new_player["id"] = f"gp-{slugify(new_player['name'])}-{uuid.uuid4().hex[:6]}"
    new_player["created_at"] = date.today().isoformat()
    new_player["updated_at"] = date.today().isoformat()
    players.append(new_player)
    save_global_players(players)
    return normalize_global_player(new_player)


@app.patch("/api/global-players/{player_id}")
def update_global_player(player_id: str, patch: GlobalPlayerPatch) -> dict[str, Any]:
    if sqlite_available():
        return update_sqlite_global_player(player_id, patch.model_dump(exclude_unset=True))

    players = load_global_players()
    for index, player in enumerate(players):
        if player["id"] == player_id:
            update = patch.model_dump(exclude_unset=True)
            players[index] = {**player, **update, "updated_at": date.today().isoformat()}
            save_global_players(players)
            return normalize_global_player(players[index])
    raise HTTPException(status_code=404, detail="Global player not found")


@app.get("/api/global-players/{player_id}/image")
def get_global_player_image(player_id: str) -> Response:
    ensure_global_sqlite_schema()
    with sqlite_connect() as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT sofifa_id, image_url FROM global_players WHERE id = ?",
            (player_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Global player not found")

    sofifa_id = row["sofifa_id"] or player_id.replace("sofifa-", "")
    image_url = optional_text(row, "image_url") or build_sofifa_image_url(sofifa_id)
    if not image_url:
        raise HTTPException(status_code=404, detail="Player image not available")

    PLAYER_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    image_path = PLAYER_IMAGES_DIR / f"{sofifa_id}.png"
    if not image_path.exists():
        try:
            response = requests.get(
                image_url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://sofifa.com/",
                },
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail="No se pudo descargar la imagen del jugador.") from exc
        image_path.write_bytes(response.content)

    return Response(
        content=image_path.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.post("/api/global-players/{player_id}/refresh-sofifa")
def refresh_global_player_from_sofifa(player_id: str) -> dict[str, Any]:
    if sqlite_available():
        player = get_sqlite_global_player(player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Global player not found")
        source = player.get("sofifa_url") or player.get("sofifa_id")
        if not source:
            raise HTTPException(status_code=400, detail="El jugador no tiene URL o ID de SoFIFA")
        fetched = fetch_sofifa(SofifaFetchRequest(url_or_id=source))["player"]
        update = {key: value for key, value in fetched.items() if value not in ("", None)}
        return update_sqlite_global_player(player_id, update)

    players = load_global_players()
    for index, player in enumerate(players):
        if player["id"] != player_id:
            continue
        source = player.get("sofifa_url") or player.get("sofifa_id")
        if not source:
            raise HTTPException(status_code=400, detail="El jugador no tiene URL o ID de SoFIFA")
        fetched = fetch_sofifa(SofifaFetchRequest(url_or_id=source))["player"]
        update = {key: value for key, value in fetched.items() if value not in ("", None)}
        players[index] = {**player, **update, "updated_at": date.today().isoformat()}
        save_global_players(players)
        return normalize_global_player(players[index])
    raise HTTPException(status_code=404, detail="Global player not found")


@app.delete("/api/global-players/{player_id}")
def delete_global_player(player_id: str) -> dict[str, Any]:
    if sqlite_available():
        return delete_sqlite_global_player(player_id)

    players = load_global_players()
    kept = [player for player in players if player["id"] != player_id]
    if len(kept) == len(players):
        raise HTTPException(status_code=404, detail="Global player not found")
    save_global_players(kept)
    return {"ok": True}


@app.post("/api/fetch/sofifa")
def fetch_sofifa(payload: SofifaFetchRequest) -> dict[str, Any]:
    source_url = build_sofifa_url(payload.url_or_id)
    try:
        response = requests.get(
            source_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            },
            timeout=12,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code == 403:
            detail = (
                "SoFIFA bloqueo la consulta automatica con 403. "
                "Carga URL/ID y completa manualmente, o intenta mas tarde."
            )
        else:
            detail = f"No se pudo leer SoFIFA automaticamente: {exc}"
        raise HTTPException(
            status_code=502,
            detail=detail,
        ) from exc

    return {"player": parse_sofifa_player(response.text, response.url), "source": response.url}


@app.get("/api/teams")
def get_teams() -> dict[str, Any]:
    teams = list_teams_sqlite()
    return {"teams": teams}


@app.post("/api/teams")
def create_team(payload: TeamCreate) -> dict[str, Any]:
    ensure_teams_sqlite_schema()
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre del equipo es obligatorio.")
    if find_team_by_name_sqlite(name):
        raise HTTPException(status_code=409, detail="Ya existe un equipo con ese nombre.")
    team_id = f"{slugify(name)}-{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc).isoformat()
    with sqlite_connect() as connection:
        connection.execute(
            """
            INSERT INTO teams (id, name, owner, logo_url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (team_id, name, payload.owner.strip(), payload.logo_url.strip(), now, now),
        )
        connection.commit()
    team = get_team_sqlite(team_id)
    if not team:
        raise HTTPException(status_code=500, detail="No se pudo crear el equipo.")
    return team


@app.patch("/api/teams/{team_id}")
def update_team(team_id: str, patch: TeamPatch) -> dict[str, Any]:
    ensure_teams_sqlite_schema()
    existing = get_team_sqlite(team_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Equipo no encontrado.")
    update = patch.model_dump(exclude_unset=True)
    if team_id == HOME_TEAM_ID:
        if "name" in update and update["name"] not in (None, "", HOME_TEAM_NAME):
            raise HTTPException(status_code=400, detail=f"{HOME_TEAM_NAME} no puede cambiar de nombre.")
        if "owner" in update and update["owner"] not in (None, "", HOME_TEAM_OWNER):
            raise HTTPException(status_code=400, detail=f"{HOME_TEAM_NAME} pertenece a {HOME_TEAM_OWNER} y no puede cambiar de owner.")
        update.pop("name", None)
        update.pop("owner", None)
    if "name" in update and update["name"] is not None:
        update["name"] = update["name"].strip()
        if not update["name"]:
            raise HTTPException(status_code=400, detail="El nombre del equipo es obligatorio.")
        duplicated = find_team_by_name_sqlite(update["name"])
        if duplicated and duplicated["id"] != team_id:
            raise HTTPException(status_code=409, detail="Ya existe un equipo con ese nombre.")
    if "owner" in update and update["owner"] is not None:
        update["owner"] = update["owner"].strip()
    if "logo_url" in update and update["logo_url"] is not None:
        update["logo_url"] = update["logo_url"].strip()
    if not update:
        return existing
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    with sqlite_connect() as connection:
        assignments = ", ".join(f"{key} = ?" for key in update)
        connection.execute(
            f"UPDATE teams SET {assignments} WHERE id = ?",
            [*update.values(), team_id],
        )
        connection.commit()
    return get_team_sqlite(team_id) or existing


@app.post("/api/teams/{team_id}/logo")
def upload_team_logo(team_id: str, payload: TeamLogoPayload) -> dict[str, Any]:
    team = get_team_sqlite(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Equipo no encontrado.")
    extension = Path(payload.filename).suffix.lower().lstrip(".")
    if extension not in {"png", "jpg", "jpeg", "webp"}:
        raise HTTPException(status_code=400, detail="El escudo debe ser PNG, JPG, JPEG o WEBP.")
    match = re.match(r"^data:image/[^;]+;base64,(.+)$", payload.data_url or "")
    if not match:
        raise HTTPException(status_code=400, detail="Formato de imagen invalido.")
    TEAM_LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    for old_extension in ("png", "jpg", "jpeg", "webp"):
        old_path = TEAM_LOGOS_DIR / f"{team_id}.{old_extension}"
        if old_path.exists():
            old_path.unlink()
    image_path = TEAM_LOGOS_DIR / f"{team_id}.{extension}"
    image_path.write_bytes(base64.b64decode(match.group(1)))
    with sqlite_connect() as connection:
        connection.execute(
            "UPDATE teams SET logo_url = ?, updated_at = ? WHERE id = ?",
            (f"/api/teams/{team_id}/logo", datetime.now(timezone.utc).isoformat(), team_id),
        )
        connection.commit()
    return get_team_sqlite(team_id) or team


@app.get("/api/teams/{team_id}/logo")
def get_team_logo(team_id: str) -> Response:
    for extension, media_type in (
        ("png", "image/png"),
        ("jpg", "image/jpeg"),
        ("jpeg", "image/jpeg"),
        ("webp", "image/webp"),
    ):
        path = TEAM_LOGOS_DIR / f"{team_id}.{extension}"
        if path.exists():
            return Response(
                content=path.read_bytes(),
                media_type=media_type,
                headers={"Cache-Control": "public, max-age=604800"},
            )
    raise HTTPException(status_code=404, detail="Escudo no encontrado.")


@app.delete("/api/teams/{team_id}")
def delete_team(team_id: str) -> dict[str, Any]:
    ensure_teams_sqlite_schema()
    team = get_team_sqlite(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Equipo no encontrado.")
    if team_id == HOME_TEAM_ID:
        raise HTTPException(status_code=400, detail=f"{HOME_TEAM_NAME} no se puede borrar.")
    with sqlite_connect() as connection:
        connection.execute("DELETE FROM team_players WHERE team_id = ?", (team_id,))
        connection.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        connection.commit()
    return {"ok": True}


@app.get("/api/teams/{team_id}/players")
def get_team_players(team_id: str) -> dict[str, Any]:
    ensure_teams_sqlite_schema()
    team = get_team_sqlite(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Equipo no encontrado.")
    with sqlite_connect() as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                gp.*,
                tp.team_id AS assigned_team_id,
                t.name AS assigned_team_name,
                t.owner AS assigned_team_owner,
                t.logo_url AS assigned_team_logo_url
            FROM team_players tp
            JOIN global_players gp ON gp.id = tp.player_id
            JOIN teams t ON t.id = tp.team_id
            WHERE tp.team_id = ?
            ORDER BY COALESCE(tp.sort_order, 0), CAST(gp.overall AS INTEGER) DESC, gp.name ASC
            """,
            (team_id,),
        ).fetchall()
    players = [sqlite_row_to_global_player(row) for row in rows]
    return {"team": get_team_sqlite(team_id), "players": players, "summary": build_roster_budget_summary(players)}


@app.post("/api/teams/{team_id}/players")
def assign_team_player(team_id: str, payload: TeamAssignPlayer) -> dict[str, Any]:
    ensure_teams_sqlite_schema()
    team = get_team_sqlite(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Equipo no encontrado.")
    identifier = (payload.player_id or payload.sofifa_id or payload.query or "").strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="Ingresa nombre, ID SoFIFA o jugador global.")
    player = resolve_global_player_for_assignment(identifier)
    return assign_global_player_to_team(player, team_id, payload.force)


@app.delete("/api/teams/{team_id}/players/{player_id}")
def unassign_team_player(team_id: str, player_id: str) -> dict[str, Any]:
    ensure_teams_sqlite_schema()
    team = get_team_sqlite(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Equipo no encontrado.")
    result = unassign_global_player_from_team(player_id, team_id)
    return {**result, "team": get_team_sqlite(team_id)}


def parse_transfermarkt_player(html: str, source_url: str, expected_name: str = "") -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    headline = soup.select_one(".data-header__headline-wrapper")
    title = soup.find("title")
    name_candidates = []
    player_name = headline.get_text(" ", strip=True) if headline else ""
    player_name = re.sub(r"^#[0-9]+\s*", "", player_name).strip()
    if player_name:
        name_candidates.append(player_name)
    if not player_name and title:
        player_name = re.sub(r"\s*-\s*Player profile.*$", "", title.get_text(" ", strip=True), flags=re.I)
    if title:
        title_name = re.sub(r"\s*-\s*Player profile.*$", "", title.get_text(" ", strip=True), flags=re.I).strip()
        if title_name:
            name_candidates.append(title_name)

    profile_name_labels = {
        "full name",
        "name in home country",
        "nombre completo",
        "nombre en pais de origen",
        "nome completo",
        "nome no pais de origem",
    }
    for label in soup.select(".info-table__content--regular"):
        label_key = normalize_person_name(label.get_text(" ", strip=True).rstrip(":"))
        if label_key in profile_name_labels:
            value = label.find_next_sibling(class_="info-table__content--bold")
            profile_name = value.get_text(" ", strip=True) if value else ""
            if profile_name:
                name_candidates.append(profile_name)

    best_name = best_transfermarkt_name_match(expected_name, name_candidates)
    validation_name = best_name["name"]

    value_block = soup.select_one(".data-header__market-value-wrapper")
    value_text = value_block.get_text(" ", strip=True) if value_block else ""
    if not value_text:
        text = soup.get_text(" ", strip=True)
        match = re.search(r"â‚¬\s*[0-9]+(?:[,.][0-9]+)?\s*(?:bn|m|k|mil\.|mill\.)?", text, re.I)
        value_text = match.group(0) if match else ""

    last_update = ""
    update_match = re.search(r"Last update:\s*([0-9./-]+)", value_text, re.I)
    if update_match:
        last_update = update_match.group(1)

    validation = best_name["validation"]
    return {
        "market_value_m": parse_money_to_millions(value_text) if value_text else None,
        "market_value_currency": "EUR",
        "transfermarkt_player_name": validation_name,
        "transfermarkt_last_update": last_update,
        "transfermarkt_validation_status": validation["status"],
        "transfermarkt_validation_score": validation["score"],
        "source": source_url,
    }


TRANSFERMARKT_MAX_ATTEMPTS = 3
TRANSFERMARKT_RETRY_DELAY_SECONDS = 3.0


def fetch_transfermarkt_html(url: str) -> requests.Response:
    last_exc: requests.RequestException | None = None
    for attempt in range(1, TRANSFERMARKT_MAX_ATTEMPTS + 1):
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
                },
                timeout=15,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            is_connection_issue = isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))
            is_gateway_error = (
                isinstance(exc, requests.exceptions.HTTPError)
                and exc.response is not None
                and exc.response.status_code in (429, 502, 503, 504)
            )
            if attempt < TRANSFERMARKT_MAX_ATTEMPTS and (is_connection_issue or is_gateway_error):
                time.sleep(TRANSFERMARKT_RETRY_DELAY_SECONDS * attempt)
                continue
            raise HTTPException(status_code=502, detail=f"No se pudo leer Transfermarkt: {exc}") from exc
    raise HTTPException(status_code=502, detail=f"No se pudo leer Transfermarkt: {last_exc}")


def request_transfermarkt(payload: TransfermarktFetchRequest) -> dict[str, Any]:
    response = fetch_transfermarkt_html(payload.url)

    data = parse_transfermarkt_player(response.text, response.url, payload.expected_name)
    if data["market_value_m"] is None:
        data["market_value_m"] = None
        data["transfermarkt_validation_status"] = "no_value"
        data["transfermarkt_validation_score"] = data["transfermarkt_validation_score"] or 0
    return data


def mark_transfermarkt_failure(
    player_id: str,
    detail: str,
    *,
    player_name: str = "",
    validation_status: str = "failed",
    validation_score: float | None = None,
) -> None:
    ensure_global_sqlite_schema()
    failed_at = datetime.now(timezone.utc).isoformat()
    with sqlite_connect() as connection:
        connection.execute(
            """
            UPDATE global_players
            SET
                transfermarkt_error_at = ?,
                transfermarkt_error_detail = ?,
                transfermarkt_player_name = COALESCE(NULLIF(?, ''), transfermarkt_player_name),
                transfermarkt_validation_status = ?,
                transfermarkt_validation_score = ?
            WHERE id = ?
            """,
            (
                failed_at,
                detail,
                player_name,
                validation_status,
                validation_score,
                player_id,
            ),
        )
        connection.commit()
    clear_global_summary_cache()


def update_global_player_transfermarkt_value(player_id: str) -> dict[str, Any]:
    ensure_global_sqlite_schema()
    with sqlite_connect() as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, name, long_name, transfermarkt_url FROM global_players WHERE id = ?",
            (player_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Global player not found")
    if not row["transfermarkt_url"]:
        raise HTTPException(status_code=400, detail="El jugador no tiene URL de Transfermarkt.")

    expected_name = row["long_name"] or row["name"] or ""
    try:
        data = request_transfermarkt(TransfermarktFetchRequest(url=row["transfermarkt_url"], expected_name=expected_name))
    except HTTPException as exc:
        mark_transfermarkt_failure(player_id, str(exc.detail), validation_status="failed")
        raise

    # El link de Transfermarkt ya esta asociado manualmente al jugador (junto con SoFIFA),
    # asi que se confia en esa asociacion y no se bloquea el guardado por baja coincidencia
    # de nombres (esa validacion daba falsos negativos con apodos/nombres localizados).
    checked_at = datetime.now(timezone.utc).isoformat()
    with sqlite_connect() as connection:
        connection.execute(
            """
            UPDATE global_players
            SET
                market_value_m = ?,
                transfermarkt_checked_at = ?,
                transfermarkt_player_name = ?,
                transfermarkt_last_update = ?,
                transfermarkt_validation_status = ?,
                transfermarkt_validation_score = ?,
                transfermarkt_error_at = '',
                transfermarkt_error_detail = ''
            WHERE id = ?
            """,
            (
                data["market_value_m"],
                checked_at,
                data["transfermarkt_player_name"],
                data["transfermarkt_last_update"],
                data["transfermarkt_validation_status"],
                data["transfermarkt_validation_score"],
                player_id,
            ),
        )
        connection.commit()
    clear_global_summary_cache()
    return get_sqlite_global_player(player_id) or {}


def load_transfermarkt_update_targets(limit: int, skip_updated: bool) -> list[sqlite3.Row]:
    ensure_global_sqlite_schema()
    filters = [
        "transfermarkt_url IS NOT NULL",
        "transfermarkt_url != ''",
        "(sofifa_roster_status IS NULL OR sofifa_roster_status != 'retired')",
        "(transfermarkt_validation_status IS NULL OR transfermarkt_validation_status != 'retired')",
        "COALESCE(player_kind, 'real') != 'generic_unlicensed'",
    ]
    if skip_updated:
        filters.append("(transfermarkt_checked_at IS NULL OR transfermarkt_checked_at = '')")
    where_sql = " AND ".join(filters)
    with sqlite_connect() as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            f"""
            SELECT id, sofifa_id, name, overall, transfermarkt_url
            FROM global_players
            WHERE {where_sql}
            ORDER BY CAST(overall AS INTEGER) DESC, name ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


@app.get("/api/global-players/transfermarkt-targets")
def get_transfermarkt_update_targets(limit: int = TRANSFERMARKT_BATCH_LIMIT, skip_updated: bool = True) -> dict[str, Any]:
    safe_limit = min(max(limit, 1), 25000)
    targets = load_transfermarkt_update_targets(safe_limit, skip_updated)
    return {
        "targets": [
            {
                "id": row["id"],
                "sofifa_id": row["sofifa_id"],
                "name": row["name"],
                "overall": row["overall"],
            }
            for row in targets
        ],
        "summary": query_sqlite_global_players(limit=1)["summary"],
    }


@app.post("/api/global-players/refresh-transfermarkt-batch")
def refresh_global_players_transfermarkt_batch(payload: TransfermarktBatchRequest) -> dict[str, Any]:
    targets = load_transfermarkt_update_targets(payload.limit, payload.skip_updated)
    updated_players: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    consecutive_failures = 0
    stopped = False

    for row in targets:
        try:
            player = update_global_player_transfermarkt_value(row["id"])
            updated_players.append(
                {
                    "id": player["id"],
                    "name": player["name"],
                    "sofifa_id": player["sofifa_id"],
                    "market_value_m": player["market_value_m"],
                    "total_cost_m": player["total_cost_m"],
                    "validation": player.get("transfermarkt_validation_status"),
                }
            )
            consecutive_failures = 0
        except HTTPException as exc:
            consecutive_failures += 1
            errors.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "sofifa_id": row["sofifa_id"],
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                }
            )
        except Exception as exc:
            consecutive_failures += 1
            errors.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "sofifa_id": row["sofifa_id"],
                    "status_code": 500,
                    "detail": str(exc),
                }
            )

        if (
            payload.stop_after_consecutive_failures > 0
            and consecutive_failures >= payload.stop_after_consecutive_failures
        ):
            stopped = True
            break

    return {
        "targets": len(targets),
        "updated": len(updated_players),
        "failed": len(errors),
        "stopped": stopped,
        "skip_updated": payload.skip_updated,
        "players": updated_players[:25],
        "errors": errors[:25],
        "summary": query_sqlite_global_players(limit=1)["summary"],
    }


@app.post("/api/global-players/{player_id}/refresh-transfermarkt")
def refresh_global_player_transfermarkt(player_id: str) -> dict[str, Any]:
    return update_global_player_transfermarkt_value(player_id)


@app.post("/api/fetch/transfermarkt")
def fetch_transfermarkt(payload: FetchRequest) -> dict[str, Any]:
    return request_transfermarkt(TransfermarktFetchRequest(url=payload.url))
    try:
        response = requests.get(
            payload.url,
            headers={"User-Agent": "Mozilla/5.0 EvaPeronLeagueManager/1.0"},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo leer Transfermarkt: {exc}") from exc

    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text(" ", strip=True)
    money_match = re.search(r"valor de mercado[:\s]*([^|]+?)(?:\s{2,}|$)", text, re.I)
    if not money_match:
        money_match = re.search(r"â‚¬\s*[0-9]+(?:[,.][0-9]+)?\s*(?:mill\.|m|k|mil\.)?", text, re.I)

    value_m = parse_money_to_millions(money_match.group(0)) if money_match else None
    return {"market_value_m": value_m, "source": payload.url}
