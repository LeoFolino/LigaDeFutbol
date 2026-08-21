from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "data" / "raw" / "players.csv"
DEFAULT_DATABASE = ROOT / "app" / "data" / "global_players.sqlite3"

sys.path.insert(0, str(ROOT))

from app.importer import import_players_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa jugadores FC/SoFIFA desde CSV a SQLite.")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="Ruta al CSV de jugadores.")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE), help="Ruta de salida SQLite.")
    parser.add_argument("--source-dataset", default="", help="Nombre del dataset fuente.")
    parser.add_argument("--source-version", default="", help="Version o roster del dataset.")
    args = parser.parse_args()

    result = import_players_csv(
        csv_path=Path(args.csv),
        database_path=Path(args.database),
        source_dataset=args.source_dataset,
        source_version=args.source_version,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
