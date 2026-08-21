from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import TRANSFERMARKT_BATCH_LIMIT
from app.main import GLOBAL_PLAYERS_DB
from app.main import ensure_global_sqlite_schema
from app.main import update_global_player_transfermarkt_value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Actualiza valores de mercado desde Transfermarkt.")
    parser.add_argument("--database", default=str(GLOBAL_PLAYERS_DB))
    parser.add_argument("--limit", type=int, default=TRANSFERMARKT_BATCH_LIMIT)
    parser.add_argument("--ids", default="", help="IDs SoFIFA separados por coma.")
    parser.add_argument("--min-overall", type=int, default=None)
    parser.add_argument("--skip-updated", action="store_true")
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--stop-after-consecutive-failures", type=int, default=5)
    return parser.parse_args()


def load_targets(database: Path, args: argparse.Namespace) -> list[sqlite3.Row]:
    filters = ["transfermarkt_url IS NOT NULL", "transfermarkt_url != ''"]
    params: list[Any] = []

    if args.ids.strip():
        wanted_ids = [item.strip() for item in args.ids.split(",") if item.strip()]
        placeholders = ", ".join("?" for _ in wanted_ids)
        filters.append(f"sofifa_id IN ({placeholders})")
        params.extend(wanted_ids)
    if args.min_overall is not None:
        filters.append("CAST(overall AS INTEGER) >= ?")
        params.append(args.min_overall)
    if args.skip_updated:
        filters.append("(transfermarkt_checked_at IS NULL OR transfermarkt_checked_at = '')")

    where_sql = " AND ".join(filters)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            f"""
            SELECT id, sofifa_id, name, overall, transfermarkt_url
            FROM global_players
            WHERE {where_sql}
            ORDER BY CAST(overall AS INTEGER) DESC, name ASC
            LIMIT ?
            """,
            [*params, args.limit],
        ).fetchall()


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def main() -> None:
    args = parse_args()
    database = Path(args.database)
    ensure_global_sqlite_schema()
    targets = load_targets(database, args)
    print(f"Targets: {len(targets)}")

    updated = 0
    failed = 0
    consecutive_failures = 0
    started_at = time.monotonic()
    for index, row in enumerate(targets, start=1):
        elapsed = time.monotonic() - started_at
        print(
            f"[{index}/{len(targets)}] {row['name']} ({row['sofifa_id']}) "
            f"| ok={updated} fail={failed} elapsed={format_duration(elapsed)}"
        )
        try:
            player = update_global_player_transfermarkt_value(row["id"])
            updated += 1
            consecutive_failures = 0
            print(
                f"  ok: {player['name']} tm={player['market_value_m']}M "
                f"total={player['total_cost_m']}M validation={player.get('transfermarkt_validation_status')}"
            )
        except Exception as exc:
            failed += 1
            consecutive_failures += 1
            print(f"  failed: {type(exc).__name__}: {exc}")
            if (
                args.stop_after_consecutive_failures > 0
                and consecutive_failures >= args.stop_after_consecutive_failures
            ):
                print(f"  stop: {consecutive_failures} fallos consecutivos.")
                break
        time.sleep(args.delay_seconds)

    total_elapsed = time.monotonic() - started_at
    print(
        {
            "updated": updated,
            "failed": failed,
            "elapsed": format_duration(total_elapsed),
            "consecutive_failures": consecutive_failures,
        }
    )


if __name__ == "__main__":
    main()
