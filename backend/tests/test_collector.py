import asyncio
from datetime import datetime, timezone

import pytest

from app.collector import Collector, SnapshotCache
from app.config import Settings
from app.events import EventRing
from app.models import Snapshot


class FakeDockerClient:
    def __init__(self):
        self.containers = [
            {"Id": "abc123456789", "Names": ["/web"], "Image": "nginx:latest", "State": "running"},
        ]
        self.inspect_data = {
            "abc123456789": {
                "State": {
                    "Status": "running",
                    "StartedAt": "2026-07-02T17:00:00Z",
                    "FinishedAt": "0001-01-01T00:00:00Z",
                    "ExitCode": 0,
                    "Pid": 100,
                },
                "RestartCount": 0,
            }
        }
        self.stats_data = {
            "abc123456789": {
                "cpu_stats": {
                    "cpu_usage": {"total_usage": 2_000_000_000},
                    "system_cpu_usage": 100_000_000_000,
                    "online_cpus": 4,
                },
                "precpu_stats": {
                    "cpu_usage": {"total_usage": 1_000_000_000},
                    "system_cpu_usage": 90_000_000_000,
                },
                "memory_stats": {"usage": 100 * 1048576, "limit": 512 * 1048576, "stats": {}},
                "networks": {"eth0": {"rx_bytes": 2048, "tx_bytes": 1024}},
                "blkio_stats": {"io_service_bytes_recursive": [{"op": "Read", "value": 4096}]},
                "pids_stats": {"current": 7},
            }
        }
        self.raise_timeout_for: set[str] = set()

    async def ping(self) -> bool:
        return True

    async def version(self) -> dict:
        return {"Version": "27.1.1"}

    async def info(self) -> dict:
        return {"NCPU": 4}

    async def list_containers(self, all: bool = True) -> list[dict]:
        return self.containers

    async def inspect(self, container_id: str) -> dict:
        return self.inspect_data[container_id]

    async def stats(self, container_id: str, timeout: float = 2.0) -> dict:
        if container_id in self.raise_timeout_for:
            raise asyncio.TimeoutError()
        return self.stats_data[container_id]

    async def events(self):
        return
        yield  # pragma: no cover - makes this an async generator


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, docker_data_root=str(tmp_path), hostname_label="test-host")


async def test_tick_produces_snapshot_with_running_container(settings):
    docker = FakeDockerClient()
    cache = SnapshotCache()
    collector = Collector(docker=docker, settings=settings, cache=cache, event_ring=EventRing())

    snapshot = await collector.tick(now=datetime(2026, 7, 2, 18, 0, 0, tzinfo=timezone.utc))

    assert isinstance(snapshot, Snapshot)
    assert snapshot.host.hostname == "test-host"
    assert snapshot.host.cores == 4
    assert len(snapshot.containers) == 1
    container = snapshot.containers[0]
    assert container.id == "abc123456789"[:12]
    assert container.name == "web"
    assert container.state == "running"
    assert container.cpu_pct == 40.0
    assert container.mem_used_mb == 100.0
    assert cache.get() is snapshot


async def test_tick_skips_container_on_stats_timeout(settings):
    docker = FakeDockerClient()
    docker.raise_timeout_for.add("abc123456789")
    cache = SnapshotCache()
    collector = Collector(docker=docker, settings=settings, cache=cache, event_ring=EventRing())

    snapshot = await collector.tick(now=datetime(2026, 7, 2, 18, 0, 0, tzinfo=timezone.utc))

    assert snapshot.containers == []


async def test_tick_marks_stopped_container_with_exit_info(settings):
    docker = FakeDockerClient()
    docker.containers = [
        {"Id": "deadbeefcafe", "Names": ["/batch"], "Image": "job:latest", "State": "exited"}
    ]
    docker.inspect_data = {
        "deadbeefcafe": {
            "State": {
                "Status": "exited",
                "StartedAt": "2026-07-02T17:00:00Z",
                "FinishedAt": "2026-07-02T17:55:00Z",
                "ExitCode": 137,
                "Pid": 0,
            },
            "RestartCount": 2,
        }
    }
    cache = SnapshotCache()
    collector = Collector(docker=docker, settings=settings, cache=cache, event_ring=EventRing())

    snapshot = await collector.tick(now=datetime(2026, 7, 2, 18, 0, 0, tzinfo=timezone.utc))

    container = snapshot.containers[0]
    assert container.state == "exited"
    assert container.exit_code == 137
    assert container.restarts == 2
    assert container.exited_ago_sec == 300
    assert container.cpu_pct == 0.0
    assert container.pids == 0


def test_snapshot_cache_seeds_empty_but_valid_snapshot():
    cache = SnapshotCache()
    snapshot = cache.get()
    assert snapshot.containers == []
    assert snapshot.events == []
    assert snapshot.host.hostname == ""
