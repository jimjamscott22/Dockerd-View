"""FastAPI app: routes read only from the snapshot cache; a background collector
and events task are the only code paths that ever talk to Docker."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.collector import Collector, SnapshotCache
from app.config import get_settings
from app.docker_client import DockerClient
from app.events import EventRing
from app.models import HostMetrics, Snapshot

logger = logging.getLogger(__name__)

settings = get_settings()


async def _broadcast_loop(app: FastAPI) -> None:
    interval_sec = settings.sample_interval_sec
    last_ts = None
    while True:
        snapshot = app.state.snapshot_cache.get()
        if snapshot.ts != last_ts:
            last_ts = snapshot.ts
            dead: list[WebSocket] = []
            for ws in list(app.state.ws_clients):
                try:
                    await ws.send_json(snapshot.model_dump())
                except Exception:
                    dead.append(ws)
            for ws in dead:
                app.state.ws_clients.discard(ws)
        await asyncio.sleep(interval_sec / 2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    docker_client = DockerClient(sock_path=settings.docker_sock)
    event_ring = EventRing()
    snapshot_cache = SnapshotCache()
    collector = Collector(docker=docker_client, settings=settings, cache=snapshot_cache, event_ring=event_ring)

    app.state.docker_client = docker_client
    app.state.event_ring = event_ring
    app.state.snapshot_cache = snapshot_cache
    app.state.collector = collector
    app.state.ws_clients = set()

    tasks = [
        asyncio.create_task(collector.run_forever()),
        asyncio.create_task(collector.run_events_forever()),
        asyncio.create_task(_broadcast_loop(app)),
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
async def get_snapshot(request: Request) -> Snapshot:
    return request.app.state.snapshot_cache.get()


@app.get("/api/containers")
async def get_containers(request: Request) -> list:
    return request.app.state.snapshot_cache.get().containers


@app.get("/api/host", response_model=HostMetrics)
async def get_host(request: Request) -> HostMetrics:
    return request.app.state.snapshot_cache.get().host


@app.get("/api/health")
async def health(request: Request) -> dict:
    docker_ok = await request.app.state.docker_client.ping()
    return {"ok": True, "docker": docker_ok}


@app.websocket("/ws/snapshot")
async def ws_snapshot(websocket: WebSocket) -> None:
    await websocket.accept()
    ws_clients = websocket.app.state.ws_clients
    ws_clients.add(websocket)
    try:
        await websocket.send_json(websocket.app.state.snapshot_cache.get().model_dump())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(websocket)
