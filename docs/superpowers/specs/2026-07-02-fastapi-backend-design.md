# Docker Container Usage Dashboard — FastAPI Backend + Frontend Wiring

Date: 2026-07-02
Status: Approved for planning

## 1. Goal

Build a read-only FastAPI backend that samples the local Docker daemon and serves live
resource metrics to the existing dashboard (`Docker-Dashboard.html`), then wire that
frontend to consume the live data instead of its mock `tick()` loop.

Full field-level contract, metric formulas, and endpoint list are defined in
[`BACKEND_PROMPT-dockerd.md`](../../../BACKEND_PROMPT-dockerd.md) (§4–§6) and are
authoritative — this document covers architecture, decisions, and scope; it does not
restate every field.

## 2. Target environment

Linux only (the Docker host itself). Development happens on Windows, but the service is
never run or tested natively on Windows — no Windows-specific fallback for `/proc` reads
or socket access is in scope. A Windows-compatible variant is an explicit non-goal and
would be a separate future project.

## 3. Key decisions

| Decision | Choice | Why |
|---|---|---|
| Docker API access | Raw `httpx.AsyncClient` over the Unix socket | Spec requires a non-blocking collector; httpx gives native async without wrapping a sync SDK in a thread executor. Docker API surface needed (list/inspect/stats/version/info/events) is small enough to hand-build. |
| Event source | Docker daemon `/events` stream (not log tailing) | Cheap, one persistent connection, matches the dashboard's lifecycle-event use case (start/die/health/oom) without the noise or demuxing cost of following every container's logs. |
| Scope | Backend **and** frontend wiring (§7 of the prompt) | The deliverable is a live dashboard end-to-end, not just an API. §7 is small and well-specified: swap the data source, keep rendering/theme untouched. |
| Directory layout | `backend/app/...` (not repo-root `app/...`) | Matches the README's existing documented layout and quick-start (`cd backend`); keeps the Python service separated from the frontend HTML and docs at repo root. |
| Packaging | `uv` (`pyproject.toml` + `uv.lock`) | Project preference; replaces `requirements.txt` from the original prompt with `uv add`-managed dependencies and `uv run` execution. |
| Frontend file | `Docker-Dashboard.html` (renamed from `Docker Dashboard (standalone).html`) | File was renamed during design; the original prompt's `Docker Dashboard.dc.html` reference is stale and should not be used. |

## 4. Architecture — sample once, fan out

A single background **collector task** owns all Docker access and metric computation.
Every `SAMPLE_INTERVAL_MS` (default 1500ms) it produces one immutable snapshot and
stores it in a module-level cache. All HTTP routes and every WebSocket connection read
from that cache only — no request path ever calls into Docker directly. A separate
long-lived **events task** tails `/events` into a bounded ring buffer that the collector
folds into each snapshot.

```
                 +---------------------------------------+
  /var/run/      |  collector task (every SAMPLE_INTERVAL)|
  docker.sock <--+   list -> inspect+stats (gather)       +--> SnapshotCache
      ^          |   + /proc host metrics + disk usage    |        |
      |          +---------------------------------------+        |
      |          +---------------------------------------+        |
      +----------+  events task (persistent /events)      +--> EventRing
                 +---------------------------------------+        |
                                                                   v
        GET /api/snapshot   --+                         HTTP + WS read cache
        GET /api/host         |------------------------> (never call Docker
        GET /api/containers   |                           on the request path)
        WS  /ws/snapshot    --+                         WS pushes every tick
```

## 5. Components (`backend/app/`)

- **`config.py`** — Pydantic `BaseSettings` for `DOCKER_SOCK`, `SAMPLE_INTERVAL_MS`,
  `HOSTNAME_LABEL`, `ALLOWED_ORIGINS`, `PORT`, `DOCKER_DATA_ROOT`.
- **`docker_client.py`** — thin async wrapper over `httpx.AsyncClient` with a Unix-socket
  transport. Methods: `ping()`, `version()`, `info()`, `list_containers(all=True)`,
  `inspect(id)`, `stats(id)` (single non-streaming sample, ~2s timeout), `events()`
  (async generator over the line-delimited JSON stream).
- **`metrics.py`** — pure functions implementing the §5 formulas from the backend
  prompt (CPU %, memory excluding cache, network rate, block I/O rate) plus host reader
  functions (`/proc/stat`, `/proc/meminfo`, `shutil.disk_usage`). No Docker I/O — takes
  raw stats dicts and previous-tick state, returns numbers. Independently unit-testable.
- **`collector.py`** — the sampling loop: per-container previous byte-counter state,
  host CPU previous-tick state, `asyncio.gather` with per-container timeout guards,
  snapshot assembly, and the `SnapshotCache` / `EventRing` containers themselves.
- **`models.py`** — Pydantic models mirroring the backend prompt §4 contract exactly
  (`Snapshot`, `Host`, `Container`, `Event`), used as FastAPI `response_model`s so the
  contract is enforced at the boundary.
- **`main.py`** — FastAPI app: CORS middleware, lifespan-managed startup/shutdown of the
  collector and events tasks, and the routes below.

## 6. Endpoints

Exactly as specified in the backend prompt §4:

- `GET /api/snapshot` — full cached snapshot.
- `GET /api/containers` — cached `containers` array only.
- `GET /api/host` — cached `host` object only.
- `WS /ws/snapshot` — pushes the cached snapshot immediately on connect, then every
  `SAMPLE_INTERVAL_MS`.
- `GET /api/health` — `{"ok": true, "docker": true|false}`, backed by a daemon ping.

## 7. Data flow per tick

1. `list_containers(all=True)` → partition into running vs. stopped.
2. For each running container, concurrently (`asyncio.gather`) fetch one `inspect` and
   one `stats` sample, each wrapped in `asyncio.wait_for(~2s)`. A timeout or error skips
   that container for this tick only; its previous byte counters are retained unchanged
   for the next tick's rate calculation.
3. `metrics.py` computes per-container CPU%, memory, net rx/tx, block I/O using this
   tick's raw stats against last tick's stored byte counters. Stopped containers get
   zeroed metrics plus real `exit_code` / `exited_ago_sec` / `restarts` from inspect.
4. Host metrics: `/proc/stat` and `/proc/meminfo` deltas since the previous tick,
   `shutil.disk_usage(DOCKER_DATA_ROOT)`, and `net_rate_kbps` as the sum of every running
   container's `net_rx_kbps + net_tx_kbps` this tick.
5. Drain the newest ~50 entries from `EventRing`, assemble the `Snapshot` model, swap it
   into the cache atomically, and broadcast it to all connected WebSocket clients.

## 8. Error handling

Never return a 500 for a transient Docker problem:

- **Daemon unreachable at startup** — background tasks still start; the cache holds an
  empty-but-valid snapshot; `/api/health` reports `docker: false`.
- **Daemon drops mid-run** — collector catches the failure, keeps serving the last known
  good snapshot, retries next tick; the events task reconnects with backoff.
- **Single container stats call hangs or errors** — that container is skipped for the
  tick; the rest of the snapshot is unaffected.
- **WebSocket client disconnects** — removed from the broadcast set silently.

## 9. Frontend wiring (`Docker-Dashboard.html`)

Per backend prompt §7, change only the data source — the rendering, history-tracking,
and theme logic stay as-is:

1. Replace the mock `setInterval(this.tick)` loop with a WebSocket connection to
   `ws://<host>:8000/ws/snapshot`; on socket close/error, fall back to polling
   `GET /api/snapshot` every `tickMs`.
2. On each incoming snapshot, map fields into the existing state shape exactly as listed
   in the backend prompt (container fields → `cpu`/`mem`/`memLimit`/`rx`/`tx`/`block`/
   `pids`/`uptimeSec`/`restarts`/`exitCode`/`running`; host fields → `cpuH`/`ramH`/
   `netH`/`diskH`; `events[]` → log-line shape).
3. **Match containers across ticks by `id`** — merge into the existing array rather than
   rebuilding it, so each container's rolling history stays continuous.
4. No visual or theme changes. `accent`, `scanlines`, `hostname`, `tickMs` props are
   unchanged; the WS URL becomes a configurable inline const, and `tickMs` continues to
   drive the polling-fallback interval.

## 10. Testing / verification

- **Unit** — `metrics.py` functions tested against captured sample stats JSON (known
  byte deltas → known CPU/mem/net numbers). No Docker daemon required; runs via
  `uv run pytest` on the Windows dev machine.
- **Contract** — a live `/api/snapshot` response validated against the `models.py`
  Pydantic models (this is largely automatic since they're used as `response_model`).
- **Integration** (requires a Linux Docker host) — boot with
  `uv run uvicorn app.main:app`, confirm `/api/health` reports `docker: true`, compare
  a few containers' numbers against `docker stats` for sanity, then open
  `Docker-Dashboard.html` and confirm gauges/sparklines/events move live.

## 11. Deliverables

- `backend/pyproject.toml`, `backend/uv.lock`
- `backend/app/{main,config,docker_client,collector,metrics,models}.py`
- `backend/Dockerfile` (uv-based build)
- `docker-compose.yml` at repo root, socket mounted `:ro`
- Updated `README.md` (drop stale `Docker Dashboard.dc.html` / `requirements.txt`
  references)
- Wired `Docker-Dashboard.html`

## 12. Acceptance criteria

Carried over from the backend prompt §9, plus the frontend wiring check:

- `uv run uvicorn app.main:app` boots; `GET /api/health` → `{"ok":true,"docker":true}`
  on a host with Docker running.
- `GET /api/snapshot` returns a payload validating against the `models.py` models, with
  real values for running containers on the host.
- `WS /ws/snapshot` emits a snapshot immediately on connect, then every
  `SAMPLE_INTERVAL_MS`.
- CPU%, memory, network, and block I/O numbers are sane and match `docker stats` within
  rounding.
- No per-request `docker stats` calls — a single background collector feeds all
  responses.
- Opening `Docker-Dashboard.html` against a running backend shows live-moving gauges,
  sparklines, and event lines with the existing theme, with per-container history
  continuous across ticks (merged by `id`, not rebuilt).
