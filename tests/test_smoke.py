def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_seed_data_is_migrated(db, warehouse):
    assert warehouse.name
