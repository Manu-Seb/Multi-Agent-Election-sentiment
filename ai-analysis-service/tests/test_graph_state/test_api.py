from __future__ import annotations

from fastapi.testclient import TestClient

from graph_state.api import app


def test_graph_update_and_endpoints() -> None:
    client = TestClient(app)

    payload = {
        "article_id": "manual-1",
        "processed_at": 1772201903528,
        "entities": [
            {
                "text": "Alice",
                "type": "PERSON",
                "sentence": "Alice met Bob.",
                "sentiment_score": 0.8,
                "confidence": 0.9,
                "char_start": 0,
                "char_end": 5,
            },
            {
                "text": "Bob",
                "type": "PERSON",
                "sentence": "Alice met Bob.",
                "sentiment_score": 0.7,
                "confidence": 0.9,
                "char_start": 10,
                "char_end": 13,
            },
        ],
        "co_occurrence_pairs": [{"entity_1": "Alice", "entity_2": "Bob", "count": 1}],
        "processing_metadata": {
            "processing_time_ms": 10,
            "model_versions": {"spacy": "en_core_web_sm", "distilbert": "test-model"},
        },
    }

    update_resp = client.post("/graph/update", json=payload)
    assert update_resp.status_code == 200

    current_resp = client.get("/graph/current")
    assert current_resp.status_code == 200
    current = current_resp.json()
    assert current["node_count"] >= 2

    entity_resp = client.get("/graph/entity/Alice")
    assert entity_resp.status_code == 200
    assert entity_resp.json()["id"] == "Alice"

    edges_resp = client.get("/graph/edges", params={"entity": "Alice"})
    assert edges_resp.status_code == 200
    assert len(edges_resp.json()) >= 1

    metrics_resp = client.get("/graph/metrics")
    assert metrics_resp.status_code == 200
    assert "memory_current_bytes" in metrics_resp.json()
