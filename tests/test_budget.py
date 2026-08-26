from app.main import (
    build_roster_budget_summary,
    salary_for_overall,
)


def test_salary_scale_boundaries():
    expected_salaries = {
        95: 16.0,
        90: 16.0,
        89: 12.0,
        88: 8.0,
        85: 5.0,
        80: 0.75,
        79: 0.5,
        75: 0.05,
        74: 0.025,
        60: 0.025,
        None: 0.0,
    }

    for overall, expected_salary in expected_salaries.items():
        assert salary_for_overall(overall) == expected_salary


def test_build_roster_budget_summary():
    players = [
        {
            "name": "Jugador Uno",
            "overall": 85,
            "market_value_m": 10,
        },
        {
            "name": "Jugador Dos",
            "overall": 80,
            "market_value_m": 2.5,
        },
        {
            "name": "Jugador Tres",
            "overall": 74,
            "market_value_m": None,
        },
    ]

    summary = build_roster_budget_summary(players)

    assert summary["budget_m"] == 300.0
    assert summary["player_count"] == 3
    assert summary["market_m"] == 12.5
    assert summary["salaries_m"] == 5.775
    assert summary["spent_m"] == 18.275
    assert summary["remaining_m"] == 281.725


def test_team_roster_returns_correct_budget(client):
    team_response = client.post(
        "/api/teams",
        json={
            "name": "Equipo Presupuesto",
            "owner": "Micca",
        },
    )

    assert team_response.status_code == 200

    team = team_response.json()

    player_payloads = [
        {
            "name": "Jugador Caro",
            "position": "DC",
            "sofifa_id": "999020",
            "overall": 85,
            "market_value_m": 10,
        },
        {
            "name": "Jugador Económico",
            "position": "MC",
            "sofifa_id": "999021",
            "overall": 80,
            "market_value_m": 2.5,
        },
    ]

    for payload in player_payloads:
        player_response = client.post(
            "/api/global-players",
            json=payload,
        )

        assert player_response.status_code == 200

        player = player_response.json()

        assignment_response = client.post(
            f"/api/teams/{team['id']}/players",
            json={"player_id": player["id"]},
        )

        assert assignment_response.status_code == 200

    roster_response = client.get(
        f"/api/teams/{team['id']}/players",
    )

    assert roster_response.status_code == 200

    summary = roster_response.json()["summary"]

    assert summary["budget_m"] == 300.0
    assert summary["player_count"] == 2
    assert summary["market_m"] == 12.5
    assert summary["salaries_m"] == 5.75
    assert summary["spent_m"] == 18.25
    assert summary["remaining_m"] == 281.75