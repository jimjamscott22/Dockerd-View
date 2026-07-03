# Docker Container Usage Dashboard

A read-only, real-time dashboard for monitoring resource usage of containers on a Docker host.

**Visual style:** terminal-hacker aesthetic with muted slate, neon green, JetBrains Mono, subtle scanlines, and glow effects.

A **FastAPI** backend queries the local Docker daemon and serves live metrics to a single-page frontend that renders host ring gauges, per-container cards with sparklines, and a `docker events` stream. Everything updates a few times per second.

> **Read-only by design.**
> No start, stop, or delete controls. This tool observes your containers; it does not control them.

---

## Features

* **Host gauges**
  CPU percentage, memory percentage, aggregate network throughput, and disk usage percentage of `/var/lib/docker`.

* **Per-container cards**
  Container name, image, status, live CPU sparkline, CPU percentage, memory used, and network receive/transmit stats.

* **Event stream**
  A live tail of recent Docker events, colorized by level.

* **Live updates**
  Uses WebSocket updates with polling fallback and a configurable refresh interval.

* **Themable UI**
  Accent color, scanlines, hostname label, and tick speed can be customized.

---

## Stack

| Layer         | Technology                             |
| ------------- | -------------------------------------- |
| Backend       | Python 3.11+, FastAPI, uvicorn         |
| Docker Access | Docker Engine API over the Unix socket |
| Frontend      | Single self-contained HTML page        |
| Build Step    | None                                   |

---

## Quick Start

### Backend

```bash
cd backend
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The service reads from:

```text
/var/run/docker.sock
```

Run it directly on the Docker host, or as a user with access to the Docker socket.

---

## Docker Compose

You can also run the dashboard with Docker Compose:

```bash
docker compose up -d
```

The Docker socket is mounted read-only:

```yaml
/var/run/docker.sock:/var/run/docker.sock:ro
```

---

## Frontend

Open the following file in a browser:

```text
Docker-Dashboard.html
```

Alternatively, serve the folder statically.

By default, the frontend connects to the backend WebSocket at:

```text
ws://localhost:8000/ws/snapshot
```

---

## Configuration

The backend can be configured with environment variables.

| Variable             | Description                         | Default                |
| -------------------- | ----------------------------------- | ---------------------- |
| `DOCKER_SOCK`        | Docker socket path                  | `/var/run/docker.sock` |
| `SAMPLE_INTERVAL_MS` | Collector sampling interval         | `1500`                 |
| `HOSTNAME_LABEL`     | Label shown in the dashboard header | Machine hostname       |
| `ALLOWED_ORIGINS`    | Comma-separated CORS origins        | `http://localhost:5173,http://localhost:8000` |
| `PORT`               | API port                            | `8000`                 |
| `DOCKER_DATA_ROOT`   | Path used for the disk-usage gauge  | `/var/lib/docker`      |

---

## API

### HTTP Endpoints

| Method | Endpoint          | Description                                                                               |
| ------ | ----------------- | ----------------------------------------------------------------------------------------- |
| `GET`  | `/api/snapshot`   | Full dashboard payload including host metrics, containers, and events. Served from cache. |
| `GET`  | `/api/host`       | Host metrics only.                                                                        |
| `GET`  | `/api/containers` | Container list only.                                                                      |
| `GET`  | `/api/health`     | Health check response containing `{ ok, docker }`.                                        |

### WebSocket Endpoint

| Type | Endpoint       | Description                                                    |
| ---- | -------------- | -------------------------------------------------------------- |
| `WS` | `/ws/snapshot` | Pushes a snapshot on connect, then every `SAMPLE_INTERVAL_MS`. |

---

## Metric Notes

All CPU values are reported as percentages from `0` to `100`.

Per-container CPU usage may exceed `100` on multi-core systems.

Memory is reported in MiB.

Network and block I/O rates are reported in kB/s.

See `BACKEND_PROMPT-dockerd.md` for the full schema and metric formulas.

---

## How Metrics Are Computed

A single background collector samples all running containers concurrently on each tick and caches one snapshot.

Every request and WebSocket frame is served from that cache, which means the backend does **not** run `docker stats` per request.

Metric calculations include:

* **CPU percentage**
  Computed using CPU usage delta, system usage delta, and online CPU count.

* **Memory usage**
  Computed by subtracting page cache from total memory usage.

* **Network throughput**
  Computed from byte deltas over the sampling interval.

* **Block I/O throughput**
  Computed from byte deltas over the sampling interval.

More details are available in `BACKEND_PROMPT-dockerd.md`.

---

## Project Layout

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

---

## Known Issues

Pre-existing bugs in `Docker-Dashboard.html`, unrelated to the live backend wiring (not yet fixed):

* **`componentDidUpdate` prevProps typo** — reads `prev.props.tickMs` instead of `prev.tickMs` (the parameter itself is `prevProps`, not an object with a nested `.props`). Throws `TypeError: Cannot read properties of undefined (reading 'tickMs')` on every re-render (harmless to functionality, but spams the console).
* **`spark()` divide-by-zero** — sparkline geometry divides by `arr.length-1`, which is `0` (→ `NaN`) when a container's history array has exactly one point. Happens briefly for newly-seen containers on their first tick; self-heals by the second tick.

To fix: the file is a self-extracting "bundler" artifact — the real app code (`class Component extends DCLogic`) is JSON-encoded inside a `<script type="__bundler/template">` tag, in a nested `<script type="text/x-dc">` block (not visible to plain grep/line-based tools since the whole template is one JSON string on a single line). To edit it:

1. Read the raw HTML file and locate the `<script type="__bundler/template">` tag's text content.
2. `JSON.parse()` (or equivalent) that text to unescape it into the real page HTML.
3. Find the `<script type="text/x-dc" data-dc-script ...>` block inside the decoded HTML — that's the actual JS to edit.
4. Make the fix there, then `JSON.stringify()` the modified HTML back into the `__bundler/template` tag's body, leaving everything else in the file (manifest scripts, surrounding markup) byte-identical.
5. Verify with `node --check` on the extracted script, and a live browser check afterward.

---

## License

MIT

