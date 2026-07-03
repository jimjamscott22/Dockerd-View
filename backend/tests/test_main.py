from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_docker_false_when_no_socket():
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["docker"] is False


def test_snapshot_endpoint_returns_empty_but_valid_payload():
    with TestClient(app) as client:
        response = client.get("/api/snapshot")
    assert response.status_code == 200
    body = response.json()
    assert body["containers"] == []
    assert body["events"] == []
    assert "cpu_pct" in body["host"]


def test_host_and_containers_convenience_endpoints():
    with TestClient(app) as client:
        host_response = client.get("/api/host")
        containers_response = client.get("/api/containers")
    assert host_response.status_code == 200
    assert "hostname" in host_response.json()
    assert containers_response.status_code == 200
    assert containers_response.json() == []


def test_cors_headers_present_for_allowed_origin():
    with TestClient(app) as client:
        response = client.get(
            "/api/health", headers={"Origin": "http://localhost:5173"}
        )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_websocket_pushes_snapshot_on_connect():
    with TestClient(app) as client:
        with client.websocket_connect("/ws/snapshot") as websocket:
            data = websocket.receive_json()
    assert "containers" in data
    assert "host" in data
