PLAYER_PAYLOAD = {
    "name": "Jugador de Equipo",
    "position": "DC",
    "club": "Club de Prueba",
    "nationality": "Argentina",
    "sofifa_id": "999010",
    "overall": 85,
    "market_value_m": 10,
}


def create_team(client, name="Equipo de Prueba"):
    response = client.post(
        "/api/teams",
        json={
            "name": name,
            "owner": "Micca",
            "logo_url": "",
        },
    )

    assert response.status_code == 200

    return response.json()


def create_player(client):
    response = client.post(
        "/api/global-players",
        json=PLAYER_PAYLOAD,
    )

    assert response.status_code == 200

    return response.json()


def test_create_update_and_delete_team(client):
    team = create_team(client)

    assert team["name"] == "Equipo de Prueba"
    assert team["owner"] == "Micca"
    assert team["roster_count"] == 0

    duplicate_response = client.post(
        "/api/teams",
        json={
            "name": "Equipo de Prueba",
            "owner": "Otra persona",
        },
    )

    assert duplicate_response.status_code == 409

    update_response = client.patch(
        f"/api/teams/{team['id']}",
        json={
            "name": "Equipo Actualizado",
            "owner": "Micca Zerba",
        },
    )

    assert update_response.status_code == 200

    updated_team = update_response.json()

    assert updated_team["name"] == "Equipo Actualizado"
    assert updated_team["owner"] == "Micca Zerba"

    delete_response = client.delete(
        f"/api/teams/{team['id']}",
    )

    assert delete_response.status_code == 200
    assert delete_response.json()["ok"] is True

    teams_response = client.get("/api/teams")

    assert teams_response.status_code == 200
    assert all(
        current_team["id"] != team["id"]
        for current_team in teams_response.json()["teams"]
    )


def test_assign_and_unassign_player(client):
    team = create_team(client)
    player = create_player(client)

    assign_response = client.post(
        f"/api/teams/{team['id']}/players",
        json={"player_id": player["id"]},
    )

    assert assign_response.status_code == 200

    assignment = assign_response.json()

    assert assignment["already_assigned"] is False
    assert assignment["player"]["assigned_team_id"] == team["id"]

    roster_response = client.get(
        f"/api/teams/{team['id']}/players",
    )

    assert roster_response.status_code == 200

    roster = roster_response.json()

    assert len(roster["players"]) == 1
    assert roster["players"][0]["id"] == player["id"]
    assert roster["summary"]["player_count"] == 1

    unassign_response = client.delete(
        f"/api/teams/{team['id']}/players/{player['id']}",
    )

    assert unassign_response.status_code == 200
    assert unassign_response.json()["ok"] is True

    empty_roster_response = client.get(
        f"/api/teams/{team['id']}/players",
    )

    empty_roster = empty_roster_response.json()

    assert empty_roster["players"] == []
    assert empty_roster["summary"]["player_count"] == 0


def test_prevent_and_force_player_reassignment(client):
    first_team = create_team(client, "Primer Equipo")
    second_team = create_team(client, "Segundo Equipo")
    player = create_player(client)

    first_assignment = client.post(
        f"/api/teams/{first_team['id']}/players",
        json={"player_id": player["id"]},
    )

    assert first_assignment.status_code == 200

    rejected_assignment = client.post(
        f"/api/teams/{second_team['id']}/players",
        json={"player_id": player["id"]},
    )

    assert rejected_assignment.status_code == 409
    assert "ya pertenece" in rejected_assignment.json()["detail"]

    forced_assignment = client.post(
        f"/api/teams/{second_team['id']}/players",
        json={
            "player_id": player["id"],
            "force": True,
        },
    )

    assert forced_assignment.status_code == 200
    assert (
        forced_assignment.json()["player"]["assigned_team_id"]
        == second_team["id"]
    )

    first_roster = client.get(
        f"/api/teams/{first_team['id']}/players",
    ).json()

    second_roster = client.get(
        f"/api/teams/{second_team['id']}/players",
    ).json()

    assert first_roster["players"] == []
    assert len(second_roster["players"]) == 1
    assert second_roster["players"][0]["id"] == player["id"]