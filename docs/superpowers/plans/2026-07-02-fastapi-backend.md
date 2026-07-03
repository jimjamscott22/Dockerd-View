# Docker Container Usage Dashboard Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the read-only FastAPI backend for the Docker Container Usage Dashboard and wire the existing `Docker-Dashboard.html` frontend to consume it live, per `docs/superpowers/specs/2026-07-02-fastapi-backend-design.md`.

**Architecture:** A single background collector task samples the Docker daemon over a Unix socket every `SAMPLE_INTERVAL_MS`, computes derived metrics with pure functions, and caches one immutable snapshot; a separate events task tails the Docker `/events` stream into a ring buffer. All HTTP routes and the WebSocket endpoint read only from the cache — never from Docker directly. The design deliberately keeps Docker access ungraceful-failure-safe: if the daemon is unreachable, the app still boots and serves an empty-but-valid snapshot, which also lets the test suite run without a real Docker socket (including on the Windows dev machine).

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, httpx (async, Unix socket transport), pydantic / pydantic-settings, uv (packaging), pytest + pytest-asyncio.

## Global Constraints

- Python 3.11+ throughout.
- Package/dependency management via **uv** (`pyproject.toml` + `uv.lock`); all commands run as `uv run ...` / `uv add ...`. No `requirements.txt`.
- Backend lives under `backend/` (not repo root), matching the README's existing documented layout.
- Docker access is **read-only** — no endpoint may start, stop, delete, or otherwise mutate a container.
- Docker Engine API access is via `httpx.AsyncClient` over the Unix socket at `DOCKER_SOCK` (default `/var/run/docker.sock`) — no `docker-py` SDK, no blocking calls in the collector.
- Event stream source is the Docker daemon `/events` stream (not container log tailing).
- Env vars, exact names and defaults: `DOCKER_SOCK` (`/var/run/docker.sock`), `SAMPLE_INTERVAL_MS` (`1500`), `HOSTNAME_LABEL` (machine hostname), `ALLOWED_ORIGINS` (comma-separated, default `http://localhost:5173,http://localhost:8000`), `PORT` (`8000`), `DOCKER_DATA_ROOT` (`/var/lib/docker`).
- Per-container stats/inspect calls must be guarded by a ~2s timeout; a timeout skips that container for the tick without failing the whole snapshot.
- No per-request `docker stats` calls — only the background collector talks to Docker; HTTP/WS handlers read the cache.
- Never return HTTP 500 for a transient Docker error; degrade to last-known-good or an empty-but-valid snapshot instead, and report `docker: false` from `/api/health`.
- The JSON field names, types, and units in `Snapshot`/`HostMetrics`/`ContainerMetrics`/`EventEntry` must exactly match §4 of `BACKEND_PROMPT-dockerd.md` (this is enforced via `response_model`).
- The frontend file is `Docker-Dashboard.html` at the repo root (already renamed from `Docker Dashboard (standalone).html`) — do not reintroduce the old name.
- Frontend wiring changes only the data source; rendering, sparkline geometry, theme props (`accent`, `scanlines`, `hostname`, `tickMs`) are untouched. Containers must be merged by `id` across ticks, not rebuilt.

---

## Task 1: Project scaffolding with uv

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/.gitignore`

**Interfaces:**
- Produces: an installable `app` package at `backend/app/`, a `tests` package at `backend/tests/`, and a working `uv run pytest` / `uv run uvicorn` command for all later tasks.

- [ ] **Step 1: Verify uv is available**

Run: `uv --version`
Expected: prints a uv version (e.g. `uv 0.x.x`). If uv is not installed, stop and tell the user to install it (`pipx install uv` or see https://docs.astral.sh/uv/) before continuing — do not silently fall back to pip.

- [ ] **Step 2: Create the backend directory and initialize the uv project**

```bash
mkdir -p backend/app backend/tests
cd backend
uv init --name dockerd-view-backend --package --python 3.11
```

This creates `backend/pyproject.toml` and a default source layout. We will replace the generated source layout with our own `app/` package next.

- [ ] **Step 3: Fix up `pyproject.toml`**

Replace the generated `backend/pyproject.toml` contents with:

```toml
[project]
name = "dockerd-view-backend"
version = "0.1.0"
description = "Read-only FastAPI backend for the Docker Container Usage Dashboard"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "httpx>=0.27",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

- [ ] **Step 4: Remove the generated scaffold source dir if `uv init --package` created one other than `app/`**

Run: `ls backend` and if a `src/` or `dockerd_view_backend/` directory was generated, delete it (`rm -rf backend/src backend/dockerd_view_backend` as applicable) — we use `backend/app/` as the package.

- [ ] **Step 5: Create empty package markers**

Create `backend/app/__init__.py` with content:
```python
```
(empty file)

Create `backend/tests/__init__.py` with content:
```python
```
(empty file)

- [ ] **Step 6: Create `backend/.gitignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 7: Sync dependencies**

Run: `cd backend && uv sync`
Expected: creates `backend/.venv/` and `backend/uv.lock`, exits 0.

- [ ] **Step 8: Verify pytest runs (with zero tests, should report "no tests ran")**

Run: `cd backend && uv run pytest`
Expected: exits 0, output includes "no tests ran" (or similar — 0 collected).

- [ ] **Step 9: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/__init__.py backend/tests/__init__.py backend/.gitignore
git commit -m "chore: scaffold backend uv project"
```

---

## Task 2: Pydantic contract models

**Files:**
- Create: `backend/app/models.py`
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Produces: `HostMetrics`, `ContainerMetrics`, `EventEntry`, `Snapshot` Pydantic models — the exact shape every later task (`collector.py`, `main.py`) constructs and returns.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_models.py`:

```python
from app.models import ContainerMetrics, EventEntry, HostMetrics, Snapshot


def test_snapshot_round_trip():
    snapshot = Snapshot(
        ts="2026-07-02T18:04:11Z",
        host=HostMetrics(
            hostname="homelab",
            engine_version="27.1.1",
            cores=8,
            cpu_pct=28.4,
            mem_pct=52.1,
            mem_used_mb=8123.0,
            mem_total_mb=16000.0,
            net_rate_kbps=412.5,
            disk_pct=17.0,
        ),
        containers=[
            ContainerMetrics(
                id="b5ee15b979fe",
                name="traefik",
                image="traefik:v3.0",
                state="running",
                exit_code=0,
                uptime_sec=15420,
                exited_ago_sec=0,
                restarts=0,
                pids=14,
                cpu_pct=6.2,
                mem_used_mb=48.0,
                mem_limit_mb=512.0,
                net_rx_kbps=120.0,
                net_tx_kbps=80.0,
                block_kbps=12.0,
            )
        ],
        events=[
            EventEntry(
                ts="2026-07-02T18:04:11Z",
                level="info",
                container="postgres",
                message="checkpoint complete: wrote 214 buffers",
            )
        ],
    )

    payload = snapshot.model_dump()
    assert payload["host"]["cores"] == 8
    assert payload["containers"][0]["state"] == "running"
    assert payload["events"][0]["level"] == "info"


def test_container_state_rejects_invalid_value():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ContainerMetrics(
            id="abc123",
            name="x",
            image="x",
            state="not-a-real-state",
            exit_code=0,
            uptime_sec=0,
            exited_ago_sec=0,
            restarts=0,
            pids=0,
            cpu_pct=0,
            mem_used_mb=0,
            mem_limit_mb=0,
            net_rx_kbps=0,
            net_tx_kbps=0,
            block_kbps=0,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models'`.

- [ ] **Step 3: Write `backend/app/models.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_models.py -v`
Expected: PASS, 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/tests/test_models.py
git commit -m "feat: add snapshot contract models"
```

---

## Task 3: Settings

**Files:**
- Create: `backend/app/config.py`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings` class (pydantic-settings) with fields `docker_sock: str`, `sample_interval_ms: int`, `hostname_label: str`, `allowed_origins: str`, `port: int`, `docker_data_root: str`, and a `allowed_origins_list` property returning `list[str]`. `get_settings() -> Settings` factory used by `main.py`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_config.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 3: Write `backend/app/config.py`**

```python
"""Environment-driven settings for the collector and API."""
import socket

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False)

    docker_sock: str = "/var/run/docker.sock"
    sample_interval_ms: int = 1500
    hostname_label: str = socket.gethostname()
    allowed_origins: str = "http://localhost:5173,http://localhost:8000"
    port: int = 8000
    docker_data_root: str = "/var/lib/docker"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def sample_interval_sec(self) -> float:
        return self.sample_interval_ms / 1000.0


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_config.py -v`
Expected: PASS, 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/test_config.py
git commit -m "feat: add env-driven settings"
```

---

## Task 4: Metric math (pure functions)

**Files:**
- Create: `backend/app/metrics.py`
- Test: `backend/tests/test_metrics.py`

**Interfaces:**
- Consumes: nothing (pure functions operating on plain dicts/strings/numbers).
- Produces, all consumed by `collector.py` in Task 7:
  - `container_cpu_pct(stats: dict) -> float`
  - `container_memory_mb(stats: dict) -> tuple[float, float]` → `(mem_used_mb, mem_limit_mb)`
  - `container_network_kbps(stats: dict, prev_rx_bytes: float, prev_tx_bytes: float, interval_sec: float) -> tuple[float, float, float, float]` → `(net_rx_kbps, net_tx_kbps, rx_bytes, tx_bytes)`
  - `container_block_kbps(stats: dict, prev_bytes: float, interval_sec: float) -> tuple[float, float]` → `(block_kbps, total_bytes)`
  - `parse_proc_stat_line(line: str) -> dict[str, int]`
  - `host_cpu_pct(prev: dict[str, int], curr: dict[str, int]) -> float`
  - `parse_meminfo(text: str) -> dict[str, int]`
  - `host_memory_mb(meminfo: dict[str, int]) -> tuple[float, float, float]` → `(mem_used_mb, mem_total_mb, mem_pct)`
  - `host_disk_pct(path: str) -> float`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_metrics.py`:

```python
import shutil

from app.metrics import (
    container_block_kbps,
    container_cpu_pct,
    container_memory_mb,
    container_network_kbps,
    host_cpu_pct,
    host_disk_pct,
    host_memory_mb,
    parse_meminfo,
    parse_proc_stat_line,
)


def test_container_cpu_pct_basic():
    stats = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 2_000_000_000, "percpu_usage": [1, 2, 3, 4]},
            "system_cpu_usage": 100_000_000_000,
            "online_cpus": 4,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 1_000_000_000},
            "system_cpu_usage": 90_000_000_000,
        },
    }
    # cpu_delta = 1_000_000_000, system_delta = 10_000_000_000, online_cpus = 4
    # pct = (1e9 / 10e9) * 4 * 100 = 40.0
    assert container_cpu_pct(stats) == 40.0


def test_container_cpu_pct_zero_system_delta_is_safe():
    stats = {
        "cpu_stats": {"cpu_usage": {"total_usage": 5}, "system_cpu_usage": 100},
        "precpu_stats": {"cpu_usage": {"total_usage": 1}, "system_cpu_usage": 100},
    }
    assert container_cpu_pct(stats) == 0.0


def test_container_cpu_pct_falls_back_to_percpu_length():
    stats = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 2000, "percpu_usage": [1, 2]},
            "system_cpu_usage": 2000,
        },
        "precpu_stats": {"cpu_usage": {"total_usage": 1000}, "system_cpu_usage": 1000},
    }
    # cpu_delta=1000, system_delta=1000, online_cpus falls back to len(percpu_usage)=2
    assert container_cpu_pct(stats) == 200.0


def test_container_memory_mb_excludes_cache():
    stats = {
        "memory_stats": {
            "usage": 100 * 1048576,
            "limit": 512 * 1048576,
            "stats": {"total_inactive_file": 20 * 1048576},
        }
    }
    used_mb, limit_mb = container_memory_mb(stats)
    assert used_mb == 80.0
    assert limit_mb == 512.0


def test_container_memory_mb_falls_back_to_inactive_file():
    stats = {
        "memory_stats": {
            "usage": 100 * 1048576,
            "limit": 512 * 1048576,
            "stats": {"inactive_file": 10 * 1048576},
        }
    }
    used_mb, _ = container_memory_mb(stats)
    assert used_mb == 90.0


def test_container_network_kbps_rates_against_previous_sample():
    stats = {
        "networks": {
            "eth0": {"rx_bytes": 2048, "tx_bytes": 1024},
            "eth1": {"rx_bytes": 1024, "tx_bytes": 1024},
        }
    }
    rx_kbps, tx_kbps, rx_bytes, tx_bytes = container_network_kbps(
        stats, prev_rx_bytes=1024, prev_tx_bytes=0, interval_sec=1.0
    )
    # total rx=3072, total tx=2048
    assert rx_bytes == 3072
    assert tx_bytes == 2048
    assert rx_kbps == (3072 - 1024) / 1.0 / 1024
    assert tx_kbps == (2048 - 0) / 1.0 / 1024


def test_container_network_kbps_never_negative():
    stats = {"networks": {"eth0": {"rx_bytes": 10, "tx_bytes": 10}}}
    rx_kbps, tx_kbps, _, _ = container_network_kbps(
        stats, prev_rx_bytes=1000, prev_tx_bytes=1000, interval_sec=1.0
    )
    assert rx_kbps == 0.0
    assert tx_kbps == 0.0


def test_container_block_kbps_sums_read_and_write():
    stats = {
        "blkio_stats": {
            "io_service_bytes_recursive": [
                {"op": "Read", "value": 4096},
                {"op": "Write", "value": 2048},
                {"op": "Total", "value": 999999},
            ]
        }
    }
    rate, total = container_block_kbps(stats, prev_bytes=1024, interval_sec=1.0)
    assert total == 6144
    assert rate == (6144 - 1024) / 1.0 / 1024


def test_container_block_kbps_handles_missing_key():
    rate, total = container_block_kbps({"blkio_stats": {}}, prev_bytes=0, interval_sec=1.0)
    assert rate == 0.0
    assert total == 0.0


def test_parse_proc_stat_line():
    line = "cpu  1000 0 500 8000 100 0 0 0 0 0"
    fields = parse_proc_stat_line(line)
    assert fields["user"] == 1000
    assert fields["idle"] == 8000
    assert fields["iowait"] == 100


def test_host_cpu_pct_from_deltas():
    prev = parse_proc_stat_line("cpu  1000 0 500 8000 100 0 0 0")
    curr = parse_proc_stat_line("cpu  1100 0 600 8100 100 0 0 0")
    # idle+iowait prev=8100, curr=8200, idle_delta=100
    # total prev=9600, total curr=9900, total_delta=300
    # pct = (1 - 100/300) * 100 = 66.666...
    assert round(host_cpu_pct(prev, curr), 2) == 66.67


def test_host_cpu_pct_zero_delta_is_safe():
    prev = parse_proc_stat_line("cpu  0 0 0 0 0 0 0 0")
    assert host_cpu_pct(prev, prev) == 0.0


def test_parse_meminfo():
    text = "MemTotal:       16384000 kB\nMemAvailable:    8192000 kB\nMemFree:  100 kB\n"
    parsed = parse_meminfo(text)
    assert parsed["MemTotal"] == 16384000
    assert parsed["MemAvailable"] == 8192000


def test_host_memory_mb():
    meminfo = {"MemTotal": 16384000, "MemAvailable": 8192000}
    used_mb, total_mb, pct = host_memory_mb(meminfo)
    assert total_mb == 16000.0
    assert used_mb == 8000.0
    assert pct == 50.0


def test_host_disk_pct_reads_real_path(tmp_path):
    pct = host_disk_pct(str(tmp_path))
    total, used, free = shutil.disk_usage(str(tmp_path))
    assert pct == (used / total) * 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.metrics'`.

- [ ] **Step 3: Write `backend/app/metrics.py`**

```python
"""Pure metric math from BACKEND_PROMPT-dockerd.md §5. No I/O — callers supply raw
stats dicts / proc-file text and previous-tick state; these functions only compute."""
import shutil


def container_cpu_pct(stats: dict) -> float:
    cpu_stats = stats["cpu_stats"]
    precpu_stats = stats["precpu_stats"]

    cpu_delta = cpu_stats["cpu_usage"]["total_usage"] - precpu_stats["cpu_usage"]["total_usage"]
    system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)

    online_cpus = cpu_stats.get("online_cpus")
    if not online_cpus:
        online_cpus = len(cpu_stats["cpu_usage"].get("percpu_usage") or [1]) or 1

    if system_delta <= 0:
        return 0.0
    return (cpu_delta / system_delta) * online_cpus * 100.0


def container_memory_mb(stats: dict) -> tuple[float, float]:
    memory_stats = stats["memory_stats"]
    mstats = memory_stats.get("stats", {})
    cache = mstats.get("total_inactive_file", mstats.get("inactive_file", 0))
    used_bytes = memory_stats["usage"] - cache
    mem_used_mb = used_bytes / 1_048_576
    mem_limit_mb = memory_stats["limit"] / 1_048_576
    return mem_used_mb, mem_limit_mb


def container_network_kbps(
    stats: dict, prev_rx_bytes: float, prev_tx_bytes: float, interval_sec: float
) -> tuple[float, float, float, float]:
    networks = stats.get("networks") or {}
    rx_bytes = sum(iface.get("rx_bytes", 0) for iface in networks.values())
    tx_bytes = sum(iface.get("tx_bytes", 0) for iface in networks.values())
    net_rx_kbps = max(0.0, (rx_bytes - prev_rx_bytes) / interval_sec / 1024)
    net_tx_kbps = max(0.0, (tx_bytes - prev_tx_bytes) / interval_sec / 1024)
    return net_rx_kbps, net_tx_kbps, rx_bytes, tx_bytes


def container_block_kbps(stats: dict, prev_bytes: float, interval_sec: float) -> tuple[float, float]:
    entries = (stats.get("blkio_stats") or {}).get("io_service_bytes_recursive") or []
    total = sum(e["value"] for e in entries if e.get("op") in ("Read", "Write"))
    rate = max(0.0, (total - prev_bytes) / interval_sec / 1024)
    return rate, total


_PROC_STAT_FIELDS = ["user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"]


def parse_proc_stat_line(line: str) -> dict[str, int]:
    parts = line.split()
    values = [int(x) for x in parts[1 : 1 + len(_PROC_STAT_FIELDS)]]
    return dict(zip(_PROC_STAT_FIELDS, values))


def host_cpu_pct(prev: dict[str, int], curr: dict[str, int]) -> float:
    prev_idle = prev.get("idle", 0) + prev.get("iowait", 0)
    curr_idle = curr.get("idle", 0) + curr.get("iowait", 0)
    prev_total = sum(prev.values())
    curr_total = sum(curr.values())

    idle_delta = curr_idle - prev_idle
    total_delta = curr_total - prev_total
    if total_delta <= 0:
        return 0.0
    return (1 - idle_delta / total_delta) * 100.0


def parse_meminfo(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        result[key.strip()] = int(val.strip().split()[0])
    return result


def host_memory_mb(meminfo: dict[str, int]) -> tuple[float, float, float]:
    total_kb = meminfo.get("MemTotal", 0)
    avail_kb = meminfo.get("MemAvailable", 0)
    used_kb = max(0, total_kb - avail_kb)
    mem_total_mb = total_kb / 1024
    mem_used_mb = used_kb / 1024
    mem_pct = (used_kb / total_kb * 100.0) if total_kb else 0.0
    return mem_used_mb, mem_total_mb, mem_pct


def host_disk_pct(path: str) -> float:
    usage = shutil.disk_usage(path)
    if usage.total == 0:
        return 0.0
    return usage.used / usage.total * 100.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_metrics.py -v`
Expected: PASS, all tests passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/metrics.py backend/tests/test_metrics.py
git commit -m "feat: add pure metric math functions"
```

---

## Task 5: Async Docker Engine API client

**Files:**
- Create: `backend/app/docker_client.py`
- Test: `backend/tests/test_docker_client.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (standalone httpx wrapper).
- Produces, consumed by `collector.py` (Task 7) and `main.py` (Task 8):
  - `class DockerClient`
    - `__init__(self, sock_path: str)`
    - `async def ping(self) -> bool`
    - `async def version(self) -> dict`
    - `async def info(self) -> dict`
    - `async def list_containers(self, all: bool = True) -> list[dict]`
    - `async def inspect(self, container_id: str) -> dict`
    - `async def stats(self, container_id: str, timeout: float = 2.0) -> dict`
    - `async def events(self) -> AsyncIterator[dict]`
    - `async def aclose(self) -> None`
  - `class DockerUnavailableError(Exception)` — raised by any method on connection failure.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_docker_client.py`:

```python
import json

import httpx
import pytest

from app.docker_client import DockerClient, DockerUnavailableError


def make_client(handler) -> DockerClient:
    transport = httpx.MockTransport(handler)
    client = DockerClient(sock_path="/var/run/docker.sock")
    client._client = httpx.AsyncClient(transport=transport, base_url="http://docker")
    return client


async def test_ping_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/_ping"
        return httpx.Response(200, text="OK")

    client = make_client(handler)
    assert await client.ping() is True


async def test_ping_failure_returns_false():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no socket", request=request)

    client = make_client(handler)
    assert await client.ping() is False


async def test_version_returns_parsed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/version"
        return httpx.Response(200, json={"Version": "27.1.1"})

    client = make_client(handler)
    result = await client.version()
    assert result["Version"] == "27.1.1"


async def test_list_containers_passes_all_flag():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/containers/json"
        assert request.url.params["all"] == "true"
        return httpx.Response(200, json=[{"Id": "abc123"}])

    client = make_client(handler)
    result = await client.list_containers(all=True)
    assert result == [{"Id": "abc123"}]


async def test_inspect_uses_container_id_in_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/containers/abc123/json"
        return httpx.Response(200, json={"Id": "abc123", "State": {}})

    client = make_client(handler)
    result = await client.inspect("abc123")
    assert result["Id"] == "abc123"


async def test_stats_requests_non_streaming_sample():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/containers/abc123/stats"
        assert request.url.params["stream"] == "false"
        return httpx.Response(200, json={"cpu_stats": {}})

    client = make_client(handler)
    result = await client.stats("abc123")
    assert result == {"cpu_stats": {}}


async def test_non_2xx_response_raises_docker_unavailable_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = make_client(handler)
    with pytest.raises(DockerUnavailableError):
        await client.version()


async def test_connect_error_raises_docker_unavailable_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no socket", request=request)

    client = make_client(handler)
    with pytest.raises(DockerUnavailableError):
        await client.list_containers()


async def test_events_yields_parsed_json_lines():
    body = json.dumps({"Type": "container", "Action": "start"}) + "\n" + json.dumps(
        {"Type": "container", "Action": "die"}
    ) + "\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/events"
        return httpx.Response(200, content=body.encode())

    client = make_client(handler)
    events = [event async for event in client.events()]
    assert events == [
        {"Type": "container", "Action": "start"},
        {"Type": "container", "Action": "die"},
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_docker_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.docker_client'`.

- [ ] **Step 3: Write `backend/app/docker_client.py`**

```python
"""Thin async wrapper over the Docker Engine API, reached over a Unix socket."""
from typing import AsyncIterator

import httpx


class DockerUnavailableError(Exception):
    """Raised when the Docker daemon cannot be reached or returns an error."""


class DockerClient:
    def __init__(self, sock_path: str):
        self._sock_path = sock_path
        transport = httpx.AsyncHTTPTransport(uds=sock_path)
        self._client = httpx.AsyncClient(transport=transport, base_url="http://docker")

    async def _get(self, path: str, params: dict | None = None, timeout: float = 5.0) -> httpx.Response:
        try:
            response = await self._client.get(path, params=params, timeout=timeout)
        except httpx.HTTPError as exc:
            raise DockerUnavailableError(str(exc)) from exc
        if response.status_code >= 400:
            raise DockerUnavailableError(f"{path} returned HTTP {response.status_code}")
        return response

    async def ping(self) -> bool:
        try:
            await self._get("/_ping", timeout=2.0)
            return True
        except DockerUnavailableError:
            return False

    async def version(self) -> dict:
        response = await self._get("/version")
        return response.json()

    async def info(self) -> dict:
        response = await self._get("/info")
        return response.json()

    async def list_containers(self, all: bool = True) -> list[dict]:
        response = await self._get("/containers/json", params={"all": str(all).lower()})
        return response.json()

    async def inspect(self, container_id: str) -> dict:
        response = await self._get(f"/containers/{container_id}/json")
        return response.json()

    async def stats(self, container_id: str, timeout: float = 2.0) -> dict:
        response = await self._get(
            f"/containers/{container_id}/stats", params={"stream": "false"}, timeout=timeout
        )
        return response.json()

    async def events(self) -> AsyncIterator[dict]:
        try:
            response = await self._client.get("/events", timeout=None)
        except httpx.HTTPError as exc:
            raise DockerUnavailableError(str(exc)) from exc
        if response.status_code >= 400:
            raise DockerUnavailableError(f"/events returned HTTP {response.status_code}")
        for line in response.text.splitlines():
            line = line.strip()
            if not line:
                continue
            import json

            yield json.loads(line)

    async def aclose(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_docker_client.py -v`
Expected: PASS, all tests passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/docker_client.py backend/tests/test_docker_client.py
git commit -m "feat: add async Docker Engine API client over Unix socket"
```

---

## Task 6: Event mapping and ring buffer

**Files:**
- Create: `backend/app/events.py`
- Test: `backend/tests/test_events.py`

**Interfaces:**
- Consumes: `EventEntry` from `app.models` (Task 2).
- Produces, consumed by `collector.py` (Task 7):
  - `map_docker_event(raw_event: dict, now_iso: str) -> EventEntry | None`
  - `class EventRing` with `__init__(self, maxlen: int = 200)`, `add(self, entry: EventEntry) -> None`, `latest(self, count: int = 50) -> list[EventEntry]`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_events.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.events'`.

- [ ] **Step 3: Write `backend/app/events.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_events.py -v`
Expected: PASS, all tests passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/events.py backend/tests/test_events.py
git commit -m "feat: add Docker event mapping and ring buffer"
```

---

## Task 7: Collector (sampling loop + snapshot cache)

**Files:**
- Create: `backend/app/collector.py`
- Test: `backend/tests/test_collector.py`

**Interfaces:**
- Consumes:
  - `DockerClient` (Task 5): `list_containers`, `inspect`, `stats`, `events`, `ping`, `version`, `info` — used via a `Protocol` so tests supply a fake.
  - `Snapshot`, `HostMetrics`, `ContainerMetrics`, `EventEntry` (Task 2).
  - `map_docker_event`, `EventRing` (Task 6).
  - `metrics.py` functions (Task 4).
  - `Settings` (Task 3): `docker_data_root`, `hostname_label`, `sample_interval_sec`.
- Produces, consumed by `main.py` (Task 8):
  - `class SnapshotCache` with `get(self) -> Snapshot`, `set(self, snapshot: Snapshot) -> None`, seeded with an empty-but-valid `Snapshot` at construction.
  - `class Collector`
    - `__init__(self, docker: "DockerClientProtocol", settings: Settings, cache: SnapshotCache, event_ring: EventRing)`
    - `async def tick(self, now: datetime) -> Snapshot` — runs one sampling cycle, updates `cache`, returns the new snapshot.
    - `async def run_forever(self) -> None` — loops `tick()` every `settings.sample_interval_sec`, sleeping between ticks, never raises.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_collector.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_collector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.collector'`.

- [ ] **Step 3: Write `backend/app/collector.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_collector.py -v`
Expected: PASS, all tests passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/collector.py backend/tests/test_collector.py
git commit -m "feat: add collector sampling loop and snapshot cache"
```

---

## Task 8: FastAPI app (routes, CORS, lifespan)

**Files:**
- Create: `backend/app/main.py`
- Test: `backend/tests/test_main.py`

**Interfaces:**
- Consumes: `Settings`/`get_settings` (Task 3), `DockerClient` (Task 5), `EventRing` (Task 6), `Collector`/`SnapshotCache` (Task 7), `Snapshot` (Task 2).
- Produces: `app` (FastAPI instance) importable as `app.main:app`, used by `uvicorn` and by later Dockerfile/compose (Task 9).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_main.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 3: Write `backend/app/main.py`**

```python
"""FastAPI app: routes read only from the snapshot cache; a background collector
and events task are the only code paths that ever talk to Docker."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.collector import Collector, SnapshotCache
from app.config import get_settings
from app.docker_client import DockerClient
from app.events import EventRing
from app.models import HostMetrics, Snapshot

logger = logging.getLogger(__name__)

settings = get_settings()
docker_client = DockerClient(sock_path=settings.docker_sock)
event_ring = EventRing()
snapshot_cache = SnapshotCache()
collector = Collector(docker=docker_client, settings=settings, cache=snapshot_cache, event_ring=event_ring)

_ws_clients: set[WebSocket] = set()


async def _broadcast_loop() -> None:
    interval_sec = settings.sample_interval_sec
    last_ts = None
    while True:
        snapshot = snapshot_cache.get()
        if snapshot.ts != last_ts:
            last_ts = snapshot.ts
            dead: list[WebSocket] = []
            for ws in list(_ws_clients):
                try:
                    await ws.send_json(snapshot.model_dump())
                except Exception:
                    dead.append(ws)
            for ws in dead:
                _ws_clients.discard(ws)
        await asyncio.sleep(interval_sec / 2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = [
        asyncio.create_task(collector.run_forever()),
        asyncio.create_task(collector.run_events_forever()),
        asyncio.create_task(_broadcast_loop()),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await docker_client.aclose()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/snapshot", response_model=Snapshot)
async def get_snapshot() -> Snapshot:
    return snapshot_cache.get()


@app.get("/api/containers")
async def get_containers() -> list:
    return snapshot_cache.get().containers


@app.get("/api/host", response_model=HostMetrics)
async def get_host() -> HostMetrics:
    return snapshot_cache.get().host


@app.get("/api/health")
async def health() -> dict:
    docker_ok = await docker_client.ping()
    return {"ok": True, "docker": docker_ok}


@app.websocket("/ws/snapshot")
async def ws_snapshot(websocket: WebSocket) -> None:
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        await websocket.send_json(snapshot_cache.get().model_dump())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_main.py -v`
Expected: PASS, all tests passed. (The `/api/health` test passes because there is no real Docker socket on the dev/test machine, so `ping()` returns `False` — this is the intended graceful-degradation behavior, not a bug.)

- [ ] **Step 5: Run the full test suite**

Run: `cd backend && uv run pytest -v`
Expected: all tests across every task pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_main.py
git commit -m "feat: add FastAPI app with snapshot routes, health check, and websocket"
```

---

## Task 9: Dockerfile and docker-compose.yml

**Files:**
- Create: `backend/Dockerfile`
- Create: `docker-compose.yml` (repo root)

**Interfaces:**
- Consumes: `backend/pyproject.toml` + `backend/uv.lock` (Task 1), `app.main:app` (Task 8).
- Produces: a buildable container image and a compose file mounting the Docker socket read-only, per Global Constraints.

- [ ] **Step 1: Write `backend/Dockerfile`**

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Write `docker-compose.yml` at the repo root**

```yaml
services:
  dockerd-view-backend:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      DOCKER_SOCK: /var/run/docker.sock
      SAMPLE_INTERVAL_MS: "1500"
      ALLOWED_ORIGINS: "http://localhost:5173,http://localhost:8000"
      PORT: "8000"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    restart: unless-stopped
```

- [ ] **Step 3: Validate compose file syntax**

Run: `docker compose -f docker-compose.yml config`
Expected: exits 0 and prints the resolved config. (This validates YAML/schema only — it does not require actually building the image or having Docker running on the dev machine, though `docker compose` itself must be installed.) If `docker compose` is unavailable in this environment, skip execution but note in the commit message that this step was not run locally and must be verified on a Linux Docker host before first deployment.

- [ ] **Step 4: Commit**

```bash
git add backend/Dockerfile docker-compose.yml
git commit -m "chore: add Dockerfile and docker-compose for the backend"
```

---

## Task 10: README update

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing (documentation only).
- Produces: accurate quick-start instructions matching the actual repo layout and tooling.

- [ ] **Step 1: Update the Quick Start / Backend section**

In `README.md`, replace the "### Backend" section (currently using `python -m venv` / `pip install -r requirements.txt`) with:

```markdown
### Backend

```bash
cd backend
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```
```

- [ ] **Step 2: Fix the frontend filename reference**

Replace every occurrence of `Docker Dashboard.dc.html` in `README.md` with `Docker-Dashboard.html` (this covers the "Frontend" section and the "Project Layout" tree).

- [ ] **Step 3: Fix the "See BACKEND_PROMPT.md" reference**

Replace `BACKEND_PROMPT.md` with the actual filename `BACKEND_PROMPT-dockerd.md` in the "Metric Notes" and "How Metrics Are Computed" sections.

- [ ] **Step 4: Update the Project Layout tree**

Replace the `## Project Layout` code block with:

```text
.
├── Docker-Dashboard.html          # Dashboard UI
├── BACKEND_PROMPT-dockerd.md      # Full backend spec and build instructions
├── backend/                       # FastAPI service (uv-managed)
│   ├── app/
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── docs/superpowers/               # Specs and implementation plans
└── docker-compose.yml
```

- [ ] **Step 5: Read the file back and confirm no stale references remain**

Run a search for the old strings to confirm they're gone:

Run: `grep -n "Docker Dashboard.dc.html\|requirements.txt\|BACKEND_PROMPT.md" README.md`
Expected: no output (no matches).

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: update README for uv-based backend and actual filenames"
```

---

## Task 11: Frontend wiring — live WebSocket with polling fallback

**Files:**
- Modify: `Docker-Dashboard.html`

**Interfaces:**
- Consumes: the `Snapshot` JSON shape served by `WS /ws/snapshot` / `GET /api/snapshot` (Task 8) — field names exactly as in `backend/app/models.py`.
- Produces: no new files; the existing dashboard's rendering functions and rolling-history arrays (`cpuH`, `memH`, `rxH`, `txH`, `blkH`, host `cpuH`/`ramH`/`netH`/`diskH`) keep working, now fed by live data.

- [ ] **Step 1: Locate the mock data loop**

Run: `grep -n "tick\|setInterval" "Docker-Dashboard.html" | head -50`

Read the surrounding component code in `Docker-Dashboard.html` to find:
- the `tick()` method (or equivalently named mock-data generator),
- where `setInterval(this.tick, ...)` (or similar) is wired up,
- the exact shape of the per-container state objects and host history arrays it currently populates.

Do not guess at names — use what's actually in the file. The steps below describe the transformation to apply; adapt exact property names to match what Step 1 finds, keeping the semantics identical to what's documented in the design spec §9.

- [ ] **Step 2: Add a `connectLive()` method that opens the WebSocket**

Inside the same component/object that currently defines `tick()`, add a sibling method:

```javascript
connectLive() {
  const wsUrl = this.wsUrl || `ws://${location.hostname}:8000/ws/snapshot`;
  let socket;
  let pollTimer = null;

  const startPolling = () => {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      try {
        const res = await fetch(`http://${location.hostname}:8000/api/snapshot`);
        if (res.ok) this.applySnapshot(await res.json());
      } catch (err) {
        // stay silent; next poll will retry
      }
    }, this.tickMs);
  };

  const stopPolling = () => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const connect = () => {
    socket = new WebSocket(wsUrl);
    socket.onopen = () => stopPolling();
    socket.onmessage = (event) => {
      this.applySnapshot(JSON.parse(event.data));
    };
    socket.onclose = () => {
      startPolling();
      setTimeout(connect, this.tickMs);
    };
    socket.onerror = () => socket.close();
  };

  connect();
},
```

- [ ] **Step 3: Add an `applySnapshot()` method that maps the wire format into existing state**

Add this method alongside `connectLive()`. It must merge containers by `id` (not replace the array) and push into whatever history arrays Step 1 identified — the field names below (`cpuH`, `memH`, `rxH`, `txH`, `blkH` per-container, `cpuH`/`ramH`/`netH`/`diskH` on host) are the names used in the design spec; align them to the actual property names found in Step 1 if they differ:

```javascript
applySnapshot(snapshot) {
  // --- host ---
  this.host = this.host || {};
  this.host.cpuH = this.host.cpuH || [];
  this.host.ramH = this.host.ramH || [];
  this.host.netH = this.host.netH || [];
  this.host.diskH = this.host.diskH || [];
  this.host.cpuH.push(snapshot.host.cpu_pct);
  this.host.ramH.push(snapshot.host.mem_pct);
  this.host.netH.push(snapshot.host.net_rate_kbps);
  this.host.diskH.push(snapshot.host.disk_pct);

  // --- containers: merge by id, keep existing history ---
  const byId = new Map((this.containers || []).map((c) => [c.id, c]));
  const nextContainers = [];
  for (const c of snapshot.containers) {
    const existing = byId.get(c.id) || {
      id: c.id,
      cpuH: [],
      memH: [],
      rxH: [],
      txH: [],
      blkH: [],
    };
    existing.name = c.name;
    existing.image = c.image;
    existing.running = c.state === "running";
    existing.exitCode = c.exit_code;
    existing.uptimeSec = c.uptime_sec;
    existing.exitedAgoSec = c.exited_ago_sec;
    existing.restarts = c.restarts;
    existing.pids = c.pids;
    existing.cpu = c.cpu_pct;
    existing.mem = c.mem_used_mb;
    existing.memLimit = c.mem_limit_mb;
    existing.rx = c.net_rx_kbps;
    existing.tx = c.net_tx_kbps;
    existing.block = c.block_kbps;
    existing.cpuH.push(c.cpu_pct);
    existing.memH.push(c.mem_used_mb);
    existing.rxH.push(c.net_rx_kbps);
    existing.txH.push(c.net_tx_kbps);
    existing.blkH.push(c.block_kbps);
    nextContainers.push(existing);
  }
  this.containers = nextContainers;

  // --- events -> log lines ---
  this.logs = snapshot.events.map((e) => ({
    time: e.ts.slice(11, 19),
    level: e.level,
    name: e.container,
    msg: e.message,
  }));
},
```

- [ ] **Step 4: Replace the mock loop invocation**

Find where the component previously called `setInterval(this.tick, this.tickMs)` (or the mock initializer) and replace that call with `this.connectLive()`. Do not delete the `tick()` method itself yet — leave it in place but unused, in case Task 12's manual verification needs to fall back to it for comparison. (If a later cleanup pass wants it removed, that's a separate, explicit follow-up — not part of this task.)

- [ ] **Step 5: Verify the file still parses as valid HTML/JS**

Run: `node --check <(grep -A100000 "<script" "Docker-Dashboard.html" | sed '1d;$d')` — if `node` isn't available, instead open the file in a browser (see Step 6) and check the console for syntax errors.

- [ ] **Step 6: Manually verify against a fake WebSocket server**

Since there's no live Docker daemon on the Windows dev machine, verify the wiring with a throwaway local server that mimics `/ws/snapshot`. Create a temporary script (not committed) at `backend/tests/manual_fake_server.py`:

```python
import asyncio
import json

import uvicorn
from fastapi import FastAPI, WebSocket

app = FastAPI()

SAMPLE = {
    "ts": "2026-07-02T18:04:11Z",
    "host": {
        "hostname": "fake-host",
        "engine_version": "27.1.1",
        "cores": 8,
        "cpu_pct": 28.4,
        "mem_pct": 52.1,
        "mem_used_mb": 8123.0,
        "mem_total_mb": 16000.0,
        "net_rate_kbps": 412.5,
        "disk_pct": 17.0,
    },
    "containers": [
        {
            "id": "b5ee15b979fe",
            "name": "traefik",
            "image": "traefik:v3.0",
            "state": "running",
            "exit_code": 0,
            "uptime_sec": 15420,
            "exited_ago_sec": 0,
            "restarts": 0,
            "pids": 14,
            "cpu_pct": 6.2,
            "mem_used_mb": 48.0,
            "mem_limit_mb": 512.0,
            "net_rx_kbps": 120.0,
            "net_tx_kbps": 80.0,
            "block_kbps": 12.0,
        }
    ],
    "events": [
        {"ts": "2026-07-02T18:04:11Z", "level": "info", "container": "postgres", "message": "checkpoint complete"}
    ],
}


@app.websocket("/ws/snapshot")
async def ws(websocket: WebSocket):
    await websocket.accept()
    while True:
        await websocket.send_text(json.dumps(SAMPLE))
        await asyncio.sleep(1.5)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

Run: `cd backend && uv run python tests/manual_fake_server.py` in one terminal, open `Docker-Dashboard.html` in a browser, and confirm the host gauges and the `traefik` container card populate and update. Then delete this throwaway script (`rm backend/tests/manual_fake_server.py`) — it must not be committed.

- [ ] **Step 7: Commit**

```bash
git add "Docker-Dashboard.html"
git commit -m "feat: wire dashboard to live snapshot websocket with polling fallback"
```

---

## Task 12: End-to-end verification on a Linux Docker host

This task has no automated steps runnable from the Windows dev machine — it is the manual acceptance pass required before calling the feature done, per the design spec §10 and §12. Perform it on the actual Linux Docker host (or a Linux VM/container with Docker installed) once Tasks 1–11 are merged.

**Files:** none (verification only).

- [ ] **Step 1: Boot the backend directly**

```bash
cd backend
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Expected: starts without error.

- [ ] **Step 2: Health check**

Run: `curl -s http://localhost:8000/api/health`
Expected: `{"ok":true,"docker":true}`.

- [ ] **Step 3: Snapshot contract check**

Run: `curl -s http://localhost:8000/api/snapshot | python3 -m json.tool | head -40`
Expected: valid JSON matching the §4 shape, with non-empty `containers` if any containers are running on the host.

- [ ] **Step 4: Cross-check numbers against `docker stats`**

Run `docker stats --no-stream` alongside the snapshot for the same container(s) and confirm CPU%, memory, and I/O are in the same ballpark (rounding differences are expected, large discrepancies are not).

- [ ] **Step 5: Open the dashboard against the live backend**

Serve or open `Docker-Dashboard.html` from a machine that can reach the Docker host on port 8000, confirm the WS URL resolves correctly, and watch gauges/sparklines/event lines update live for at least one full sampling interval, with a container's history remaining continuous (not resetting) across ticks.

- [ ] **Step 6: Docker Compose boot**

```bash
docker compose up -d --build
curl -s http://localhost:8000/api/health
docker compose down
```

Expected: same health check result as Step 2, confirming the socket mount and uv-based image build both work.

- [ ] **Step 7: Record the result**

No commit for this task (verification only) — report the outcome back in the plan-execution summary (pass/fail per step above).

---

## Self-review notes

- **Spec coverage:** §1 (goal) — Tasks 8/11. §2 (tech/constraints) — Task 1 (uv/FastAPI), Task 8 (CORS, config-driven), Task 9 (Dockerfile+compose w/ `:ro` mount). §3 (architecture) — Task 7 (collector), Task 8 (cache-only routes, WS). §4 (contract) — Task 2 (models), Task 8 (response_model enforcement), Task 12 Step 3. §5 (formulas) — Task 4. §6 (events) — Task 6. §7 (frontend wiring) — Task 11. §8 (deliverables) — Tasks 1–2, 4–9 collectively produce every listed file. §9 (acceptance criteria) — Task 12 exercises all of them on a real Linux host; Task 8 tests cover the parts checkable without Docker.
- **Placeholder scan:** no TBD/TODO markers; every code step has complete code; Task 12 is explicitly marked manual-only rather than glossed over.
- **Type consistency:** `Snapshot`/`HostMetrics`/`ContainerMetrics`/`EventEntry` (Task 2) are used with identical field names in Task 7 (`collector.py`), Task 8 (`main.py`), and Task 11 (frontend field mapping). `DockerClient` methods introduced in Task 5 (`list_containers`, `inspect`, `stats`, `events`, `ping`, `version`, `info`) match the `DockerClientProtocol` consumed in Task 7. `EventRing`/`map_docker_event` (Task 6) signatures match their use in Task 7.
