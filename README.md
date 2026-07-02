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
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
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
Docker Dashboard.dc.html
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
| `ALLOWED_ORIGINS`    | Comma-separated CORS origins        | Not set                |
| `PORT`               | API port                            | `8000`                 |

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

See `BACKEND_PROMPT.md` for the full schema and metric formulas.

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

More details are available in `BACKEND_PROMPT.md`.

---

## Project Layout

```text
.
├── Docker Dashboard.dc.html   # Dashboard UI
├── BACKEND_PROMPT.md          # Full backend spec and build instructions
├── backend/                   # FastAPI service
└── docker-compose.yml
```

---

## License

MIT

