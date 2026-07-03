"""Pydantic models mirroring the snapshot JSON contract (BACKEND_PROMPT-dockerd.md §4)."""
from typing import Literal

from pydantic import BaseModel

ContainerState = Literal["running", "exited", "created", "paused", "restarting", "dead"]
EventLevel = Literal["info", "warn", "error"]


class HostMetrics(BaseModel):
    hostname: str
    engine_version: str
    cores: int
    cpu_pct: float
    mem_pct: float
    mem_used_mb: float
    mem_total_mb: float
    net_rate_kbps: float
    disk_pct: float


class ContainerMetrics(BaseModel):
    id: str
    name: str
    image: str
    state: ContainerState
    exit_code: int
    uptime_sec: int
    exited_ago_sec: int
    restarts: int
    pids: int
    cpu_pct: float
    mem_used_mb: float
    mem_limit_mb: float
    net_rx_kbps: float
    net_tx_kbps: float
    block_kbps: float


class EventEntry(BaseModel):
    ts: str
    level: EventLevel
    container: str
    message: str


class Snapshot(BaseModel):
    ts: str
    host: HostMetrics
    containers: list[ContainerMetrics]
    events: list[EventEntry]
