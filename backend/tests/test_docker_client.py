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
