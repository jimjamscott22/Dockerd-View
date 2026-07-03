from app.events import EventRing, map_docker_event
from app.models import EventEntry


def test_map_start_event_is_info():
    raw = {"Type": "container", "Action": "start", "Actor": {"Attributes": {"name": "traefik"}}}
    entry = map_docker_event(raw, now_iso="2026-07-02T18:04:11Z")
    assert entry.level == "info"
    assert entry.container == "traefik"
    assert "start" in entry.message


def test_map_die_with_zero_exit_is_info():
    raw = {
        "Type": "container",
        "Action": "die",
        "Actor": {"Attributes": {"name": "postgres", "exitCode": "0"}},
    }
    entry = map_docker_event(raw, now_iso="2026-07-02T18:04:11Z")
    assert entry.level == "info"


def test_map_die_with_nonzero_exit_is_error():
    raw = {
        "Type": "container",
        "Action": "die",
        "Actor": {"Attributes": {"name": "postgres", "exitCode": "1"}},
    }
    entry = map_docker_event(raw, now_iso="2026-07-02T18:04:11Z")
    assert entry.level == "error"


def test_map_unhealthy_health_status_is_warn():
    raw = {
        "Type": "container",
        "Action": "health_status: unhealthy",
        "Actor": {"Attributes": {"name": "web"}},
    }
    entry = map_docker_event(raw, now_iso="2026-07-02T18:04:11Z")
    assert entry.level == "warn"


def test_map_oom_is_error():
    raw = {"Type": "container", "Action": "oom", "Actor": {"Attributes": {"name": "worker"}}}
    entry = map_docker_event(raw, now_iso="2026-07-02T18:04:11Z")
    assert entry.level == "error"


def test_map_non_container_event_is_ignored():
    raw = {"Type": "network", "Action": "connect"}
    assert map_docker_event(raw, now_iso="2026-07-02T18:04:11Z") is None


def test_event_ring_bounded_and_returns_newest_last():
    ring = EventRing(maxlen=3)
    for i in range(5):
        ring.add(EventEntry(ts=f"t{i}", level="info", container="c", message=str(i)))
    latest = ring.latest(count=50)
    assert [e.message for e in latest] == ["2", "3", "4"]


def test_event_ring_latest_respects_count():
    ring = EventRing(maxlen=10)
    for i in range(5):
        ring.add(EventEntry(ts=f"t{i}", level="info", container="c", message=str(i)))
    latest = ring.latest(count=2)
    assert [e.message for e in latest] == ["3", "4"]
