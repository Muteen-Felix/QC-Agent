from routes.items import get_item


def test_get_item():
    assert get_item(1)["id"] == 1
