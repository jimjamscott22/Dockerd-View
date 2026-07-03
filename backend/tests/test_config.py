import socket

from app.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.docker_sock == "/var/run/docker.sock"
    assert s.sample_interval_ms == 1500
    assert s.hostname_label == socket.gethostname()
    assert s.allowed_origins == "http://localhost:5173,http://localhost:8000"
    assert s.port == 8000
    assert s.docker_data_root == "/var/lib/docker"


def test_allowed_origins_list_splits_and_strips():
    s = Settings(_env_file=None, allowed_origins=" http://a.com ,http://b.com,,")
    assert s.allowed_origins_list == ["http://a.com", "http://b.com"]


def test_env_override(monkeypatch):
    monkeypatch.setenv("SAMPLE_INTERVAL_MS", "500")
    monkeypatch.setenv("PORT", "9000")
    s = Settings(_env_file=None)
    assert s.sample_interval_ms == 500
    assert s.port == 9000
