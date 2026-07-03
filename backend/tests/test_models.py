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
