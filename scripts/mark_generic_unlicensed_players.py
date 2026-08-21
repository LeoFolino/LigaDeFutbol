from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "app" / "data" / "global_players.sqlite3"


def ensure_player_kind_column(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(global_players)").fetchall()
    }
    if "player_kind" not in columns:
        connection.execute("ALTER TABLE global_players ADD COLUMN player_kind TEXT")


def mark_generic_players(database: Path) -> dict[str, int]:
    with sqlite3.connect(database) as connection:
        ensure_player_kind_column(connection)
        connection.execute(
            """
            UPDATE global_players
            SET player_kind = 'real'
            WHERE player_kind IS NULL OR player_kind = ''
            """
        )
        marked = connection.execute(
            """
            UPDATE global_players
            SET
                player_kind = 'generic_unlicensed',
                transfermarkt_validation_status =
                    CASE
                        WHEN transfermarkt_validation_status IS NULL
                            OR transfermarkt_validation_status = ''
                        THEN 'generic_unlicensed'
                        ELSE transfermarkt_validation_status
                    END,
                transfermarkt_error_detail =
                    CASE
                        WHEN transfermarkt_error_detail IS NULL
                            OR transfermarkt_error_detail = ''
                        THEN 'Jugador generico/no licenciado sin identidad real de Transfermarkt'
                        ELSE transfermarkt_error_detail
                    END
            WHERE transfermarkt_url IS NULL OR transfermarkt_url = ''
            """
        ).rowcount
        counts = dict(
            connection.execute(
                """
                SELECT COALESCE(player_kind, 'real'), COUNT(*)
                FROM global_players
                GROUP BY COALESCE(player_kind, 'real')
                """
            ).fetchall()
        )
        real_with_tm = connection.execute(
            """
            SELECT COUNT(*)
            FROM global_players
            WHERE COALESCE(player_kind, 'real') = 'real'
                AND transfermarkt_url IS NOT NULL
                AND transfermarkt_url != ''
            """
        ).fetchone()[0]
        connection.commit()

    return {
        "marked_generic_unlicensed": marked,
        "real": counts.get("real", 0),
        "generic_unlicensed": counts.get("generic_unlicensed", 0),
        "with_transfermarkt_url_real": real_with_tm,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mark unlicensed/generic SoFIFA players without Transfermarkt identity."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()

    if not args.database.exists():
        raise SystemExit(f"Database not found: {args.database}")

    result = mark_generic_players(args.database)
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
