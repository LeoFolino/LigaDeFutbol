PLAYER_PAYLOAD = {
    "name": "Micca Test",
    "position": "MC",
    "club": "Club de Prueba",
    "nationality": "Argentina",
    "sofifa_id": "999001",
    "overall": 85,
    "market_value_m": 12.5,
}


def create_test_player(client):
    response = client.post(
        "/api/global-players",
        json=PLAYER_PAYLOAD,
    )

    assert response.status_code == 200

    return response.json()


def test_create_search_and_update_player(client):
    player = create_test_player(client)

    assert player["name"] == "Micca Test"
    assert player["overall"] == 85
    assert player["market_value_m"] == 12.5
    assert player["salary_m"] == 5.0
    assert player["total_cost_m"] == 17.5

    search_response = client.get(
        "/api/global-players",
        params={
            "q": "Micca Test",
            "page": 1,
            "limit": 10,
        },
    )

    assert search_response.status_code == 200

    search_data = search_response.json()

    assert search_data["pagination"]["total"] == 1
    assert search_data["players"][0]["id"] == player["id"]

    update_response = client.patch(
        f"/api/global-players/{player['id']}",
        json={
            "overall": 86,
            "market_value_m": 15,
        },
    )

    assert update_response.status_code == 200

    updated_player = update_response.json()

    assert updated_player["overall"] == 86
    assert updated_player["market_value_m"] == 15
    assert updated_player["salary_m"] == 6.0
    assert updated_player["total_cost_m"] == 21.0


def test_delete_player(client):
    player = create_test_player(client)

    delete_response = client.delete(
        f"/api/global-players/{player['id']}",
    )

    assert delete_response.status_code == 200
    assert delete_response.json()["ok"] is True

    second_delete_response = client.delete(
        f"/api/global-players/{player['id']}",
    )

    assert second_delete_response.status_code == 404


def test_reject_player_with_invalid_overall(client):
    invalid_payload = {
        **PLAYER_PAYLOAD,
        "sofifa_id": "999002",
        "overall": 120,
    }

    response = client.post(
        "/api/global-players",
        json=invalid_payload,
    )

    assert response.status_code == 422