PLAYERS = [
    {
        "name": "Lionel Prueba",
        "position": "RW",
        "club": "Atlético Aurora",
        "nationality": "Argentina",
        "sofifa_id": "999101",
        "overall": 91,
        "market_value_m": 80,
    },
    {
        "name": "Alexia Prueba",
        "position": "MC",
        "club": "Barcelona Demo",
        "nationality": "España",
        "sofifa_id": "999102",
        "overall": 90,
        "market_value_m": 75,
    },
    {
        "name": "Marta Demo",
        "position": "DC",
        "club": "Rosario Centralizado",
        "nationality": "Brasil",
        "sofifa_id": "999103",
        "overall": 86,
        "market_value_m": 40,
    },
    {
        "name": "Defensor Test",
        "position": "CB",
        "club": "Defensa Norte",
        "nationality": "Argentina",
        "sofifa_id": "999104",
        "overall": 82,
        "market_value_m": 15,
    },
    {
        "name": "Arquera Test",
        "position": "GK",
        "club": "Arqueras del Sur",
        "nationality": "España",
        "sofifa_id": "999105",
        "overall": 80,
        "market_value_m": None,
    },
]


def seed_players(client):
    created_players = []

    for payload in PLAYERS:
        response = client.post(
            "/api/global-players",
            json=payload,
        )

        assert response.status_code == 200

        created_players.append(response.json())

    return created_players


def test_search_players_by_name_club_and_sofifa_id(client):
    seed_players(client)

    name_response = client.get(
        "/api/global-players",
        params={"q": "Lionel"},
    )

    assert name_response.status_code == 200
    assert name_response.json()["pagination"]["total"] == 1
    assert name_response.json()["players"][0]["name"] == "Lionel Prueba"

    club_response = client.get(
        "/api/global-players",
        params={"q": "Rosario"},
    )

    assert club_response.status_code == 200
    assert club_response.json()["pagination"]["total"] == 1
    assert club_response.json()["players"][0]["name"] == "Marta Demo"

    sofifa_response = client.get(
        "/api/global-players",
        params={"q": "999104"},
    )

    assert sofifa_response.status_code == 200
    assert sofifa_response.json()["pagination"]["total"] == 1
    assert sofifa_response.json()["players"][0]["name"] == "Defensor Test"


def test_filter_players(client):
    seed_players(client)

    position_response = client.get(
        "/api/global-players",
        params={"position": "MC"},
    )

    assert position_response.status_code == 200
    assert position_response.json()["pagination"]["total"] == 1
    assert position_response.json()["players"][0]["name"] == "Alexia Prueba"

    overall_response = client.get(
        "/api/global-players",
        params={"min_overall": 88},
    )

    assert overall_response.status_code == 200
    assert overall_response.json()["pagination"]["total"] == 2

    overall_names = [
        player["name"]
        for player in overall_response.json()["players"]
    ]

    assert overall_names == [
        "Lionel Prueba",
        "Alexia Prueba",
    ]

    value_response = client.get(
        "/api/global-players",
        params={"max_value_m": 20},
    )

    assert value_response.status_code == 200
    assert value_response.json()["pagination"]["total"] == 2

    value_names = {
        player["name"]
        for player in value_response.json()["players"]
    }

    assert value_names == {
        "Defensor Test",
        "Arquera Test",
    }


def test_paginate_players(client):
    seed_players(client)

    first_page_response = client.get(
        "/api/global-players",
        params={
            "page": 1,
            "limit": 2,
        },
    )

    assert first_page_response.status_code == 200

    first_page = first_page_response.json()

    assert first_page["pagination"] == {
        "page": 1,
        "limit": 2,
        "total": 5,
        "pages": 3,
    }

    assert [
        player["name"]
        for player in first_page["players"]
    ] == [
        "Lionel Prueba",
        "Alexia Prueba",
    ]

    second_page_response = client.get(
        "/api/global-players",
        params={
            "page": 2,
            "limit": 2,
        },
    )

    second_page = second_page_response.json()

    assert second_page["pagination"]["page"] == 2
    assert [
        player["name"]
        for player in second_page["players"]
    ] == [
        "Marta Demo",
        "Defensor Test",
    ]

    third_page_response = client.get(
        "/api/global-players",
        params={
            "page": 3,
            "limit": 2,
        },
    )

    third_page = third_page_response.json()

    assert third_page["pagination"]["page"] == 3
    assert [
        player["name"]
        for player in third_page["players"]
    ] == [
        "Arquera Test",
    ]