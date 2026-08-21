from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CANONICAL_COLUMNS = [
    "id",
    "sofifa_id",
    "name",
    "long_name",
    "position",
    "club",
    "nationality",
    "overall",
    "potential",
    "market_value_m",
    "wage_m",
    "transfermarkt_url",
    "image_url",
    "age",
    "height_cm",
    "weight_kg",
    "preferred_foot",
    "weak_foot",
    "skill_moves",
    "international_reputation",
    "body_type",
    "real_face",
    "release_clause_m",
    "acceleration_type",
    "play_styles",
    "specialities",
    "roles_json",
    "pace",
    "shooting",
    "passing",
    "dribbling",
    "defending",
    "physical",
    "attributes_json",
    "source_dataset",
    "source_version",
    "sofifa_roster_status",
    "imported_at",
    "raw_json",
]

COLUMN_ALIASES = {
    "sofifa_id": ["sofifa_id", "player_id", "id", "ea_id"],
    "name": ["short_name", "name", "known_as", "player_name"],
    "long_name": ["long_name", "full_name"],
    "position": ["player_positions", "positions", "position"],
    "club": ["club_name", "club", "team_name"],
    "nationality": ["nationality_name", "nationality", "nation_name"],
    "overall": ["overall", "rating", "ova"],
    "potential": ["potential", "pot"],
    "market_value_m": ["value_eur", "value", "market_value", "market_value_eur"],
    "wage_m": ["wage_eur", "wage"],
    "image_url": ["image_url", "player_face_url", "face_url"],
    "age": ["age"],
    "height_cm": ["height_cm"],
    "weight_kg": ["weight_kg"],
    "preferred_foot": ["preferred_foot"],
    "weak_foot": ["weak_foot"],
    "skill_moves": ["skill_moves"],
    "international_reputation": ["international_reputation"],
    "body_type": ["body_type"],
    "real_face": ["real_face"],
    "release_clause_m": ["release_clause_eur", "release_clause"],
    "acceleration_type": ["accele_rate", "acceleration_type"],
    "play_styles": ["player_traits", "play_styles"],
    "specialities": ["specialities", "player_specialities"],
    "pace": ["pace", "pac"],
    "shooting": ["shooting", "sho"],
    "passing": ["passing", "pas"],
    "dribbling": ["dribbling", "dri"],
    "defending": ["defending", "def"],
    "physical": ["physic", "physical", "phy"],
}


def normalize_column_name(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def calculate_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def first_present(row: dict[str, str], aliases: list[str]) -> str:
    for alias in aliases:
        value = row.get(alias)
        if value not in (None, ""):
            return value
    return ""


def to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", ".")))
    except ValueError:
        return None


def money_to_millions(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("€", "").replace("$", "")
    multiplier = 1.0
    if text.lower().endswith("m"):
        text = text[:-1]
    elif text.lower().endswith("k"):
        text = text[:-1]
        multiplier = 0.001
    if "," in text:
        normalized = text.replace(".", "").replace(",", ".")
    else:
        normalized = text
    try:
        number = float(normalized)
    except ValueError:
        return None
    if multiplier == 1.0 and number > 1000:
        return round(number / 1_000_000, 3)
    return round(number * multiplier, 3)


def load_csv_rows(csv_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        sample = file.read(4096)
        file.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        reader = csv.DictReader(file, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("El CSV no tiene encabezados.")
        raw_columns = [normalize_column_name(column) for column in reader.fieldnames]
        rows = []
        for row in reader:
            normalized = {
                normalize_column_name(key): (value or "").strip()
                for key, value in row.items()
                if key is not None
            }
            rows.append(normalized)
    return raw_columns, rows


def canonicalize_row(row: dict[str, str], source_dataset: str, source_version: str, imported_at: str) -> dict[str, Any]:
    canonical: dict[str, Any] = {}
    for column, aliases in COLUMN_ALIASES.items():
        canonical[column] = first_present(row, aliases)

    sofifa_id = str(canonical.get("sofifa_id") or "").strip()
    canonical["id"] = f"sofifa-{sofifa_id}" if sofifa_id else ""
    canonical["overall"] = to_int(canonical["overall"])
    canonical["potential"] = to_int(canonical["potential"])
    canonical["market_value_m"] = money_to_millions(canonical["market_value_m"])
    canonical["wage_m"] = money_to_millions(canonical["wage_m"])
    canonical["release_clause_m"] = money_to_millions(canonical["release_clause_m"])

    for column in [
        "age",
        "height_cm",
        "weight_kg",
        "weak_foot",
        "skill_moves",
        "international_reputation",
        "pace",
        "shooting",
        "passing",
        "dribbling",
        "defending",
        "physical",
    ]:
        canonical[column] = to_int(canonical[column])

    canonical["attributes_json"] = json.dumps(
        {
            key: value
            for key, value in row.items()
            if key.startswith(
                (
                    "attacking_",
                    "skill_",
                    "movement_",
                    "power_",
                    "mentality_",
                    "defending_",
                    "goalkeeping_",
                )
            )
        },
        ensure_ascii=False,
    )
    canonical["source_dataset"] = source_dataset
    canonical["source_version"] = source_version
    canonical["sofifa_roster_status"] = "active"
    canonical["imported_at"] = imported_at
    canonical["raw_json"] = json.dumps(row, ensure_ascii=False)
    return canonical


def ensure_schema(connection: sqlite3.Connection) -> None:
    column_definitions = ", ".join(f"{column} TEXT" for column in CANONICAL_COLUMNS)
    connection.execute(f"CREATE TABLE IF NOT EXISTS global_players ({column_definitions})")
    existing_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(global_players)").fetchall()
    }
    for column in CANONICAL_COLUMNS:
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE global_players ADD COLUMN {column} TEXT")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_global_players_id ON global_players(id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_global_players_name ON global_players(name)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_global_players_club ON global_players(club)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_global_players_position ON global_players(position)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_global_players_overall ON global_players(overall)")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS import_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imported_at TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            total_rows INTEGER NOT NULL,
            imported_rows INTEGER NOT NULL,
            total_columns INTEGER NOT NULL,
            source_dataset TEXT NOT NULL,
            source_version TEXT NOT NULL
        )
        """
    )


def import_players_csv(
    csv_path: Path,
    database_path: Path,
    source_dataset: str = "",
    source_version: str = "",
) -> dict[str, Any]:
    if not csv_path.exists():
        raise FileNotFoundError(f"No existe el CSV: {csv_path}")

    source_dataset = source_dataset or csv_path.stem
    source_version = source_version or "manual"
    imported_at = datetime.now(timezone.utc).isoformat()
    raw_columns, rows = load_csv_rows(csv_path)
    source_hash = calculate_sha256(csv_path)

    canonical_rows = [
        canonicalize_row(row, source_dataset, source_version, imported_at)
        for row in rows
    ]
    canonical_rows = [row for row in canonical_rows if row["id"] and row["name"]]

    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA synchronous = NORMAL")
        ensure_schema(connection)
        connection.execute("DELETE FROM global_players")
        placeholders = ", ".join("?" for _ in CANONICAL_COLUMNS)
        connection.executemany(
            f"INSERT OR REPLACE INTO global_players ({', '.join(CANONICAL_COLUMNS)}) VALUES ({placeholders})",
            [[row.get(column) for column in CANONICAL_COLUMNS] for row in canonical_rows],
        )
        connection.execute(
            """
            INSERT INTO import_metadata (
                imported_at,
                source_file,
                source_sha256,
                total_rows,
                imported_rows,
                total_columns,
                source_dataset,
                source_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                imported_at,
                str(csv_path),
                source_hash,
                len(rows),
                len(canonical_rows),
                len(raw_columns),
                source_dataset,
                source_version,
            ),
        )
        connection.commit()

    return {
        "source_file": str(csv_path),
        "source_sha256": source_hash,
        "total_rows": len(rows),
        "imported_rows": len(canonical_rows),
        "total_columns": len(raw_columns),
        "database": str(database_path),
        "source_dataset": source_dataset,
        "source_version": source_version,
        "imported_at": imported_at,
    }
