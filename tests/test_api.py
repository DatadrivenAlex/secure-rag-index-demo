import pytest
from fastapi.testclient import TestClient

from secure_index.api import create_app
from secure_index.index import Index


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Index(tmp_path / "index.db"))) as client:
        yield client


def test_complete_flow(client):
    headers = {"X-Tenant-ID": "demo"}
    doc = {"title": "Ops guide", "text": "Rotate gateway API keys monthly.",
           "roles": ["ops"], "expected_version": 0}
    assert client.put("/v1/documents/guide", headers=headers, json=doc).json()["version"] == 1
    assert client.get("/v1/search?q=gateway", headers=headers).json() == {"results": []}
    r = client.get("/v1/search?q=gateway", headers={**headers, "X-Roles": "ops"})
    assert r.json()["results"][0]["document_id"] == "guide"
    assert client.put("/v1/documents/guide", headers=headers, json=doc).status_code == 409
    assert client.delete("/v1/documents/guide?expected_version=1", headers=headers).status_code == 200
    assert client.get("/v1/search?q=gateway", headers={**headers, "X-Roles": "ops"}).json() == {
        "results": []}


def test_invalid_input(client):
    assert client.get("/v1/search?q=x").status_code == 422
    headers = {"X-Tenant-ID": "a"}
    assert client.get("/v1/search?q=x&limit=51", headers=headers).status_code == 422
    assert client.put("/v1/documents/test", headers=headers, json={"title": "t", "text": " ",
                      "roles": [], "expected_version": 0}).status_code == 422
    assert client.put("/v1/documents/test", headers=headers, json={"title": "t", "text": "a",
                      "roles": ["bad role"], "expected_version": 0}).status_code == 422
    assert client.get("/v1/search?q=x", headers={**headers, "X-Roles": "bad role"}).status_code == 422
