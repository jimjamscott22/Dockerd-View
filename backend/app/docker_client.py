"""Thin async wrapper over the Docker Engine API, reached over a Unix socket."""
import json
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
            async with self._client.stream("GET", "/events", timeout=None) as response:
                if response.status_code >= 400:
                    raise DockerUnavailableError(f"/events returned HTTP {response.status_code}")
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    yield json.loads(line)
        except httpx.HTTPError as exc:
            raise DockerUnavailableError(str(exc)) from exc

    async def aclose(self) -> None:
        await self._client.aclose()
