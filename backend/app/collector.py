"""Background sampling loop: one tick = one Docker query pass + metric math + cache write."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Protocol

from app.config import Settings
from app.events import EventRing, map_docker_event
from app.metrics import (
    container_block_kbps,
    container_cpu_pct,
    container_memory_mb,
    container_network_kbps,
)
from app.models import ContainerMetrics, HostMetrics, Snapshot

logger = logging.getLogger(__name__)


class DockerClientProtocol(Protocol):
    async def ping(self) -> bool: ...
    async def version(self) -> dict: ...
    async def info(self) -> dict: ...
    async def list_containers(self, all: bool = True) -> list[dict]: ...
    async def inspect(self, container_id: str) -> dict: ...
    async def stats(self, container_id: str, timeout: float = 2.0) -> dict: ...
    def events(self): ...


def _empty_snapshot() -> Snapshot:
    return Snapshot(
        ts=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        host=HostMetrics(
            hostname="",
            engine_version="",
            cores=0,
            cpu_pct=0.0,
            mem_pct=0.0,
            mem_used_mb=0.0,
            mem_total_mb=0.0,
            net_rate_kbps=0.0,
            disk_pct=0.0,
        ),
        containers=[],
        events=[],
    )


class SnapshotCache:
    def __init__(self):
        self._snapshot = _empty_snapshot()

    def get(self) -> Snapshot:
        return self._snapshot

    def set(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot


def _parse_docker_ts(value: str) -> datetime | None:
    if not value or value.startswith("0001-01-01"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class Collector:
    def __init__(
        self,
        docker: DockerClientProtocol,
        settings: Settings,
        cache: SnapshotCache,
        event_ring: EventRing,
    ):
        self._docker = docker
        self._settings = settings
        self._cache = cache
        self._event_ring = event_ring
        self._prev_net: dict[str, tuple[float, float]] = {}
        self._prev_block: dict[str, float] = {}
        self._prev_proc_stat: dict[str, int] | None = None

    async def _sample_container(self, summary: dict, interval_sec: float, now: datetime) -> ContainerMetrics | None:
        try:
            container_id = summary["Id"][:12]
            full_id = summary["Id"]
            name = summary.get("Names", ["/" + full_id[:12]])[0].lstrip("/")
            image = summary.get("Image", "")

            try:
                info = await self._docker.inspect(full_id)
            except Exception:
                logger.warning("inspect failed for %s", container_id, exc_info=True)
                return None

            state = info["State"]["Status"]
            restarts = info.get("RestartCount", 0)
            exit_code = info["State"].get("ExitCode", 0)

            started_at = _parse_docker_ts(info["State"].get("StartedAt", ""))
            finished_at = _parse_docker_ts(info["State"].get("FinishedAt", ""))
            uptime_sec = int((now - started_at).total_seconds()) if state == "running" and started_at else 0
            exited_ago_sec = int((now - finished_at).total_seconds()) if state != "running" and finished_at else 0

            if state != "running":
                return ContainerMetrics(
                    id=container_id,
                    name=name,
                    image=image,
                    state=state,
                    exit_code=exit_code,
                    uptime_sec=0,
                    exited_ago_sec=max(0, exited_ago_sec),
                    restarts=restarts,
                    pids=0,
                    cpu_pct=0.0,
                    mem_used_mb=0.0,
                    mem_limit_mb=0.0,
                    net_rx_kbps=0.0,
                    net_tx_kbps=0.0,
                    block_kbps=0.0,
                )

            try:
                stats = await asyncio.wait_for(self._docker.stats(full_id), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                logger.warning("stats timed out/failed for %s", container_id, exc_info=True)
                return None

            cpu_pct = container_cpu_pct(stats)
            mem_used_mb, mem_limit_mb = container_memory_mb(stats)

            prev_rx, prev_tx = self._prev_net.get(container_id, (0.0, 0.0))
            net_rx_kbps, net_tx_kbps, rx_bytes, tx_bytes = container_network_kbps(
                stats, prev_rx, prev_tx, interval_sec
            )
            self._prev_net[container_id] = (rx_bytes, tx_bytes)

            prev_block = self._prev_block.get(container_id, 0.0)
            block_kbps, block_total = container_block_kbps(stats, prev_block, interval_sec)
            self._prev_block[container_id] = block_total

            pids = stats.get("pids_stats", {}).get("current", 0)

            return ContainerMetrics(
                id=container_id,
                name=name,
                image=image,
                state=state,
                exit_code=exit_code,
                uptime_sec=max(0, uptime_sec),
                exited_ago_sec=0,
                restarts=restarts,
                pids=pids,
                cpu_pct=round(cpu_pct, 2),
                mem_used_mb=round(mem_used_mb, 2),
                mem_limit_mb=round(mem_limit_mb, 2),
                net_rx_kbps=round(net_rx_kbps, 2),
                net_tx_kbps=round(net_tx_kbps, 2),
                block_kbps=round(block_kbps, 2),
            )
        except Exception:
            logger.warning(
                "unexpected error sampling container %s", summary.get("Id", "?"), exc_info=True
            )
            return None

    async def _host_metrics(self, engine_version: str, cores: int) -> HostMetrics:
        cpu_pct = 0.0
        try:
            with open("/proc/stat") as f:
                curr_line = f.readline()
            from app.metrics import host_cpu_pct, parse_proc_stat_line

            curr = parse_proc_stat_line(curr_line)
            if self._prev_proc_stat is not None:
                cpu_pct = host_cpu_pct(self._prev_proc_stat, curr)
            self._prev_proc_stat = curr
        except FileNotFoundError:
            pass

        mem_used_mb = mem_total_mb = mem_pct = 0.0
        try:
            with open("/proc/meminfo") as f:
                text = f.read()
            from app.metrics import host_memory_mb, parse_meminfo

            mem_used_mb, mem_total_mb, mem_pct = host_memory_mb(parse_meminfo(text))
        except FileNotFoundError:
            pass

        from app.metrics import host_disk_pct

        try:
            disk_pct = host_disk_pct(self._settings.docker_data_root)
        except OSError:
            disk_pct = 0.0

        return HostMetrics(
            hostname=self._settings.hostname_label,
            engine_version=engine_version,
            cores=cores,
            cpu_pct=round(cpu_pct, 2),
            mem_pct=round(mem_pct, 2),
            mem_used_mb=round(mem_used_mb, 2),
            mem_total_mb=round(mem_total_mb, 2),
            net_rate_kbps=0.0,
            disk_pct=round(disk_pct, 2),
        )

    async def tick(self, now: datetime) -> Snapshot:
        interval_sec = self._settings.sample_interval_sec

        try:
            summaries = await self._docker.list_containers(all=True)
        except Exception:
            logger.warning("list_containers failed", exc_info=True)
            summaries = []

        results = await asyncio.gather(
            *(self._sample_container(summary, interval_sec, now) for summary in summaries)
        )
        containers = [c for c in results if c is not None]

        engine_version = "unknown"
        cores = 0
        try:
            version_info = await self._docker.version()
            engine_version = version_info.get("Version", "unknown")
            info = await self._docker.info()
            cores = info.get("NCPU", 0)
        except Exception:
            logger.warning("version/info fetch failed", exc_info=True)

        host = await self._host_metrics(engine_version, cores)
        host.net_rate_kbps = round(
            sum(c.net_rx_kbps + c.net_tx_kbps for c in containers if c.state == "running"), 2
        )

        snapshot = Snapshot(
            ts=now.isoformat().replace("+00:00", "Z"),
            host=host,
            containers=containers,
            events=self._event_ring.latest(count=50),
        )
        self._cache.set(snapshot)
        return snapshot

    async def run_forever(self) -> None:
        interval_sec = self._settings.sample_interval_sec
        while True:
            try:
                await self.tick(now=datetime.now(timezone.utc))
            except Exception:
                logger.exception("collector tick failed; keeping last known snapshot")
            await asyncio.sleep(interval_sec)

    async def run_events_forever(self) -> None:
        while True:
            try:
                async for raw_event in self._docker.events():
                    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    entry = map_docker_event(raw_event, now_iso)
                    if entry is not None:
                        self._event_ring.add(entry)
            except Exception:
                logger.warning("events stream dropped; reconnecting", exc_info=True)
            await asyncio.sleep(2.0)
