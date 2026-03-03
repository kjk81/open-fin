from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from models import KGEdge, KGNode
from routers import graph as graph_router
from routers import notes as notes_router


def _build_client() -> tuple[TestClient, sessionmaker]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(notes_router.router, prefix="/api")
    app.include_router(graph_router.router, prefix="/api")

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    notes_router.engine = engine

    return TestClient(app), TestingSessionLocal


def test_ticker_notes_create_list_delete_and_validation():
    client, _ = _build_client()

    invalid = client.post("/api/ticker/$$$/notes", json={"content": "hello"})
    assert invalid.status_code == 422

    first = client.post("/api/ticker/aapl/notes", json={"content": "first note"})
    assert first.status_code == 201
    first_payload = first.json()
    assert first_payload["ticker"] == "AAPL"
    assert first_payload["content"] == "first note"

    second = client.post("/api/ticker/AAPL/notes", json={"content": "second note"})
    assert second.status_code == 201
    second_id = second.json()["id"]

    listed = client.get("/api/ticker/AAPL/notes?offset=0&limit=10")
    assert listed.status_code == 200
    list_payload = listed.json()
    assert list_payload["total"] == 2
    assert list_payload["items"][0]["id"] == second_id

    deleted = client.delete(f"/api/ticker/AAPL/notes/{second_id}")
    assert deleted.status_code == 204

    listed_after = client.get("/api/ticker/AAPL/notes")
    assert listed_after.status_code == 200
    assert listed_after.json()["total"] == 1


def test_graph_nodes_includes_metric_fields_with_values():
    client, session_factory = _build_client()

    with session_factory() as db:
        aapl = KGNode(node_type="ticker", name="AAPL", metadata_json="{}", is_deleted=False, updated_at=datetime.now(timezone.utc))
        msft = KGNode(node_type="ticker", name="MSFT", metadata_json="{}", is_deleted=False, updated_at=datetime.now(timezone.utc))
        sector = KGNode(node_type="sector", name="Technology", metadata_json="{}", is_deleted=False, updated_at=datetime.now(timezone.utc))
        industry = KGNode(node_type="industry", name="Software", metadata_json="{}", is_deleted=False, updated_at=datetime.now(timezone.utc))
        db.add_all([aapl, msft, sector, industry])
        db.flush()

        db.add_all([
            KGEdge(source_id=aapl.id, target_id=sector.id, relationship="IN_SECTOR", weight=1.0),
            KGEdge(source_id=aapl.id, target_id=industry.id, relationship="IN_INDUSTRY", weight=1.0),
            KGEdge(source_id=aapl.id, target_id=msft.id, relationship="CO_MENTION", weight=0.8),
        ])
        db.commit()

    res = client.get("/api/graph/nodes?search=AAPL")
    assert res.status_code == 200
    payload = res.json()
    assert payload["total"] >= 1

    item = next(x for x in payload["items"] if x["id"] == "AAPL")
    assert item["degree"] == 3
    assert item["in_sector_count"] == 1
    assert item["in_industry_count"] == 1
    assert item["co_mention_count"] == 1
    assert "updated_at" in item

    sorted_filtered = client.get("/api/graph/nodes?sort_by=degree&sort_dir=desc&min_degree=1")
    assert sorted_filtered.status_code == 200
    sf_payload = sorted_filtered.json()
    assert sf_payload["total"] >= 1
    assert all(row["degree"] >= 1 for row in sf_payload["items"])
