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
