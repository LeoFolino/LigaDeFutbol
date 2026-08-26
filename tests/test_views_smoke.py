import pytest


def test_home_contains_the_three_main_views(client):
    response = client.get("/")

    assert response.status_code == 200

    html = response.text

    assert 'id="teamsView"' in html
    assert 'id="globalView"' in html
    assert 'id="calculatorView"' in html

    assert 'id="teamsTab"' in html
    assert 'id="globalTab"' in html
    assert 'id="calculatorTab"' in html


@pytest.mark.parametrize(
    "asset_path",
    [
        "/assets/app.js",
        "/assets/styles.css",
    ],
)
def test_frontend_assets_are_available(client, asset_path):
    response = client.get(asset_path)

    assert response.status_code == 200
    assert response.content