# Claude Code — Build the FastAPI backend for the Docker Container Usage Dashboard

Paste this whole file to Claude Code (or keep it in the repo as `BACKEND_PROMPT.md`). It describes the exact
JSON contract the existing frontend expects, how to compute every metric from the Docker Engine API, and the
acceptance criteria. Build to this spec so the frontend drops in with almost no changes.

---

## 1. Goal

Build a small, read-only **FastAPI** service that runs on the Docker host, queries the local Docker daemon,
and serves live resource metrics to a single-page dashboard. The dashboard already exists (a terminal-hacker
themed UI); your job is only the backend + a tiny wiring change to the frontend.

The dashboard shows, updating roughly every 1.5s:
- **Host gauges**: CPU %, Memory %, aggregate Network throughput, Disk % (of `/var/lib/docker`).
- **Per-container cards**: name, image, status, a CPU sparkline, CPU %, memory used, and net rx/tx.
- **Event stream**: a tail of recent Docker events / container log lines.

## 2. Tech + constraints

- Python 3.11+, **FastAPI** + **uvicorn**.
- Talk to Docker via the **Docker Engine API over the unix socket** `/var/run/docker.sock`. Use the official
  `docker` SDK (`docker-py`) **or** raw `httpx`/`aiohttp` over the socket — your call, but the collector must be
  **async and non-blocking** (don't block the event loop on `stats(stream=True)`).
- **Read-only.** No start/stop/delete endpoints. Never mutate containers.
- Add **CORS** allowing the frontend origin (default `http://localhost:5173` and `http://localhost:8000`; make it
  configurable via env `ALLOWED_ORIGINS`, comma-separated).
- Config via env vars: `DOCKER_SOCK` (default `/var/run/docker.sock`), `SAMPLE_INTERVAL_MS` (default `1500`),
  `HOSTNAME_LABEL` (default the machine hostname), `PORT` (default `8000`).
- Provide a `Dockerfile` and a `docker-compose.yml` that mounts the socket read-only
  (`/var/run/docker.sock:/var/run/docker.sock:ro`) so the dashboard can itself run as a container.

## 3. Architecture — sample once, fan out

`docker stats` is expensive and rate-limited, so **do not** call it per HTTP request. Instead:

1. On startup, launch a single **background collector task** (asyncio) that every `SAMPLE_INTERVAL_MS`:
   - lists all containers (`all=True`),
   - for each **running** container, fetches one non-streaming stats sample concurrently (`asyncio.gather`),
   - computes derived metrics (formulas in §5),
   - computes host aggregates,
   - stores the result as the latest **snapshot** in memory (a module-level object / small class),
   - keeps a bounded deque (~60 points) of history per metric **server-side is optional** — the frontend keeps
     its own rolling history, so you only need to serve current values. Serving history is a nice-to-have.
2. HTTP `GET /api/snapshot` returns the cached snapshot instantly.
3. `WS /ws/snapshot` pushes the cached snapshot every interval (preferred by the frontend for smoothness).

Guard against slow/hanging stats calls with a per-container timeout (~2s); skip a container for that tick if it
times out rather than stalling the whole snapshot.

## 4. The data contract (MATCH THESE FIELD NAMES AND UNITS EXACTLY)

Both `GET /api/snapshot` and each `WS /ws/snapshot` message return this object. **Units matter** — the frontend
formats them directly.

```jsonc
{
  "ts": "2026-07-02T18:04:11Z",          // ISO-8601 UTC, snapshot time
  "host": {
    "hostname": "homelab",
    "engine_version": "27.1.1",
    "cores": 8,                          // logical CPUs
    "cpu_pct": 28.4,                     // 0..100, host CPU utilization
    "mem_pct": 52.1,                     // 0..100
    "mem_used_mb": 8123.0,               // MiB
    "mem_total_mb": 16000.0,             // MiB
    "net_rate_kbps": 412.5,              // kB/s, aggregate rx+tx across all containers this interval
    "disk_pct": 17.0                     // 0..100, usage of the filesystem holding /var/lib/docker
  },
  "containers": [
    {
      "id": "b5ee15b979fe",             // 12-char short id
      "name": "traefik",
      "image": "traefik:v3.0",
      "state": "running",               // "running" | "exited" | "created" | "paused" | "restarting" | "dead"
      "exit_code": 0,                    // last exit code (0 when running)
      "uptime_sec": 15420,               // seconds since started (0 if not running)
      "exited_ago_sec": 0,               // seconds since it exited (0 if running)
      "restarts": 0,
      "pids": 14,                        // 0 if not running
      "cpu_pct": 6.2,                    // 0..N*100 (docker per-container %, may exceed 100 on multi-core)
      "mem_used_mb": 48.0,               // MiB (usage minus cache, see §5)
      "mem_limit_mb": 512.0,             // MiB
      "net_rx_kbps": 120.0,              // kB/s this interval
      "net_tx_kbps": 80.0,               // kB/s this interval
      "block_kbps": 12.0                 // kB/s block I/O (read+write) this interval
    }
    // ... one entry per container, running and stopped
  ],
  "events": [
    {
      "ts": "2026-07-02T18:04:11Z",
      "level": "info",                   // "info" | "warn" | "error"
      "container": "postgres",           // container name
      "message": "checkpoint complete: wrote 214 buffers"
    }
    // newest last; return the most recent ~50
  ]
}
```

Notes:
- Stopped containers: include them with `state:"exited"`, zeros for cpu/mem/net/pids, and a real `exit_code` +
  `exited_ago_sec` so the card can show `exited (1) 3m ago`.
- `net_rate_kbps` (host) = sum of every running container's `net_rx_kbps + net_tx_kbps` this interval.
- All rates are **per second, in kilobytes** (bytes-delta / interval_seconds / 1024).

### Endpoints summary
- `GET  /api/snapshot` → the object above (from cache).
- `GET  /api/containers` → just the `containers` array (convenience).
- `GET  /api/host` → just the `host` object (convenience).
- `WS   /ws/snapshot` → pushes the full object every `SAMPLE_INTERVAL_MS`; also send one immediately on connect.
- `GET  /api/health` → `{"ok": true, "docker": true|false}` (checks daemon ping).

## 5. Metric formulas (Docker Engine API `/containers/{id}/stats?stream=false`)

**Per-container CPU %** — needs the delta between the sample and its `precpu_stats`:
```
cpu_delta    = cpu_stats.cpu_usage.total_usage      - precpu_stats.cpu_usage.total_usage
system_delta = cpu_stats.system_cpu_usage           - precpu_stats.system_cpu_usage
online_cpus  = cpu_stats.online_cpus  (fallback: len(cpu_stats.cpu_usage.percpu_usage))
cpu_pct = (cpu_delta / system_delta) * online_cpus * 100.0   # guard system_delta > 0
```
A single non-streaming sample already contains both `cpu_stats` and `precpu_stats`, so one call per tick is enough.

**Memory (MiB), excluding page cache** (Docker's own convention):
```
cache     = memory_stats.stats.get("total_inactive_file", memory_stats.stats.get("inactive_file", 0))
used_bytes = memory_stats.usage - cache
mem_used_mb  = used_bytes / 1048576
mem_limit_mb = memory_stats.limit / 1048576
```

**Network rx/tx (kB/s)** — sum all interfaces in `networks`, then rate against the previous sample:
```
rx_bytes = sum(iface.rx_bytes for iface in networks.values())
tx_bytes = sum(iface.tx_bytes for iface in networks.values())
net_rx_kbps = (rx_bytes - prev_rx_bytes) / interval_sec / 1024
net_tx_kbps = (tx_bytes - prev_tx_bytes) / interval_sec / 1024
```
Keep `prev_rx_bytes/prev_tx_bytes/prev_block_bytes` per container id between ticks (in the collector state).

**Block I/O (kB/s)** from `blkio_stats.io_service_bytes_recursive` (sum Read + Write), rated the same way.

**PIDs**: `pids_stats.current`.

**Host CPU %**: simplest correct approach — read `/proc/stat` deltas between ticks
(`1 - idle_delta/total_delta`) × 100. (Summing container cpu_pct is a fallback but undercounts host daemon/other
processes.)

**Host memory**: from `/proc/meminfo` → `mem_used = MemTotal - MemAvailable`, `mem_pct = used/total*100`.

**Host disk %**: `shutil.disk_usage(path_of(/var/lib/docker))` → `used/total*100`. Make the path configurable
(`DOCKER_DATA_ROOT`, default from `docker info` `DockerRootDir`, fallback `/var/lib/docker`).

**Engine version / cores**: from `docker version` / `docker info` (`NCPU`).

**uptime_sec**: `now - dateparse(State.StartedAt)`. **exited_ago_sec**: `now - dateparse(State.FinishedAt)`.
**restarts**: `RestartCount` from container inspect.

## 6. Events / log stream

Two acceptable options — pick one and document it:
- **Docker events** (recommended, cheap): subscribe to the daemon event stream (`/events`), map container
  lifecycle events (start/die/health_status/oom, etc.) into `{ts, level, container, message}`. `die` with
  non-zero exit → `error`; `health_status: unhealthy`/`oom` → `warn`/`error`; others → `info`. Keep a ring buffer
  of the last ~200 and expose the newest ~50 in the snapshot.
- **Container logs tail**: multiplex `logs(follow=True, tail=…)` for running containers; heuristically classify
  lines (`error`/`err`/`fatal` → error, `warn` → warn, else info). Heavier; only if you want real app logs.

## 7. Wiring the frontend (do this after the API works)

The frontend is a single file: **`Docker Dashboard.dc.html`**. It currently synthesizes mock data in a
`tick()` method and keeps rolling history client-side. To make it live, change only the data source — keep the
rolling-history + rendering logic:

1. In the component, replace the `setInterval(this.tick)` mock loop with a **WebSocket** to `ws://<host>:8000/ws/snapshot`
   (fall back to polling `GET /api/snapshot` every `tickMs` if the socket drops).
2. On each incoming snapshot, map fields into the existing state shape and push into the history arrays
   (`cpuH`, `memH`, `rxH`, `txH`, `blkH`, and host `cpuH/ramH/netH/diskH`) exactly like `tick()` does now:
   - container: `cpu ← cpu_pct`, `mem ← mem_used_mb`, `memLimit ← mem_limit_mb`, `rx ← net_rx_kbps`,
     `tx ← net_tx_kbps`, `block ← block_kbps`, `pids`, `uptimeSec ← uptime_sec`, `restarts`, `exitCode`,
     `running ← state==="running"`.
   - host: `cpuH←cpu_pct`, `ramH←mem_pct`, `netH←net_rate_kbps`, `diskH←disk_pct`.
   - logs: map `events[]` → `{time: HH:MM:SS from ts, level, name: container, msg: message}`.
3. Match containers across ticks by `id` so a container's history is continuous (don't rebuild the array from
   scratch — merge by id, keep the existing history, append the new point).
4. Nothing about the visuals/theme needs to change. The `accent`, `scanlines`, `hostname`, and `tickMs` tweak
   props stay; make the WS URL configurable (env/inline const) and let `tickMs` drive the poll fallback interval.

The frontend already computes sparkline geometry, threshold colors, and formatting from those raw numbers, so as
long as the units in §4 are honored, it "just works."

## 8. Deliverables

- `app/main.py` (FastAPI app, routes, CORS, startup/shutdown of the collector).
- `app/collector.py` (async sampling loop + metric math + snapshot cache).
- `app/docker_client.py` (thin async wrapper over the socket / docker-py).
- `app/models.py` (Pydantic models mirroring §4 — use these as response_model so the contract is enforced).
- `requirements.txt`, `Dockerfile`, `docker-compose.yml` (socket mounted `:ro`), `README.md` (run instructions).
- Graceful behavior when the daemon is unreachable: `/api/health` reports `docker:false`, snapshot returns last
  known data or an empty-but-valid object; never 500 on a transient stats error.

## 9. Acceptance criteria

- `uvicorn app.main:app` boots; `GET /api/health` → `{"ok":true,"docker":true}` on a host with Docker.
- `GET /api/snapshot` returns a payload validating against the §4 Pydantic models, with real values for the
  running containers on the host.
- `WS /ws/snapshot` emits a snapshot immediately on connect and then every `SAMPLE_INTERVAL_MS`.
- CPU %, mem, net, block numbers are sane and match `docker stats` within rounding.
- No per-request `docker stats` calls; a single background collector feeds all responses.
- Pointing the dashboard at the WS shows live-moving gauges, sparklines, and event lines with the existing theme.

Keep it lean and well-typed. Prefer clarity over cleverness. Add brief docstrings on the metric math.
