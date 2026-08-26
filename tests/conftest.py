import pytest
from app import main
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    test_database = tmp_path / "test.sqlite3"

    monkeypatch.setattr(main, "GLOBAL_PLAYERS_DB", test_database)
    monkeypatch.setattr(
        main,
        "GLOBAL_PLAYERS_FILE",
        tmp_path / "global_players.json",
    )
    monkeypatch.setattr(
        main,
        "PLAYER_IMAGES_DIR",
        tmp_path / "player_images",
    )
    monkeypatch.setattr(
        main,
        "TEAM_LOGOS_DIR",
        tmp_path / "team_logos",
    )

    main.GLOBAL_SCHEMA_READY = False
    main.TEAMS_SCHEMA_READY = False
    main.clear_global_summary_cache()
    main.ensure_teams_sqlite_schema()

    with TestClient(main.app) as test_client:
        yield test_client

    main.GLOBAL_SCHEMA_READY = False
    main.TEAMS_SCHEMA_READY = False
    main.clear_global_summary_cache()