"""Maps raw Docker /events entries into EventEntry rows and keeps a bounded ring buffer."""
from collections import deque

from app.models import EventEntry


def map_docker_event(raw_event: dict, now_iso: str) -> EventEntry | None:
    if raw_event.get("Type") != "container":
        return None

    action = raw_event.get("Action", "")
    attrs = (raw_event.get("Actor") or {}).get("Attributes") or {}
    container = attrs.get("name", "unknown")

    level = "info"
    if action == "die":
        exit_code = attrs.get("exitCode", "0")
        level = "error" if str(exit_code) != "0" else "info"
    elif action.startswith("health_status: unhealthy"):
        level = "warn"
    elif action == "oom":
        level = "error"

    return EventEntry(ts=now_iso, level=level, container=container, message=action)


class EventRing:
    def __init__(self, maxlen: int = 200):
        self._buffer: deque[EventEntry] = deque(maxlen=maxlen)

    def add(self, entry: EventEntry) -> None:
        self._buffer.append(entry)

    def latest(self, count: int = 50) -> list[EventEntry]:
        items = list(self._buffer)
        return items[-count:]
