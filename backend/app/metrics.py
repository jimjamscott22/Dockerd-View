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
