import uuid

from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_create_loadout_defaults_and_list():
    ticker = f"T{uuid.uuid4().hex[:4]}"

    create = client.post(
        "/api/loadouts",
        json={
            "ticker": ticker,
            "strategy_name": "momentum",
            "schedule": "0 9 * * 1-5",
            "parameters": {},
            "max_qty": 50,
            "dry_run": True,
        },
    )
    assert create.status_code == 201
    body = create.json()
    assert body["is_active"] is False
    assert body["dry_run"] is True

    list_res = client.get("/api/loadouts")
    assert list_res.status_code == 200
    rows = list_res.json()
    assert any(row["id"] == body["id"] for row in rows)


def test_worker_status_endpoint_shape():
    res = client.get("/api/worker/status")
    assert res.status_code == 200
    payload = res.json()
    assert "online" in payload
    assert "status" in payload
    assert "stale" in payload


def test_create_loadout_normalizes_lowercase_ticker():
    create = client.post(
        "/api/loadouts",
        json={
            "ticker": "aapl",
            "strategy_name": "momentum",
            "schedule": "0 9 * * 1-5",
            "parameters": {},
            "max_qty": 10,
            "dry_run": True,
        },
    )
    assert create.status_code == 201
    assert create.json()["ticker"] == "AAPL"


def test_create_loadout_invalid_schedule_rejected():
    create = client.post(
        "/api/loadouts",
        json={
            "ticker": "AAPL",
            "strategy_name": "momentum",
            "schedule": "not a cron",
            "parameters": {},
            "max_qty": 10,
            "dry_run": True,
        },
    )
    assert create.status_code == 422
