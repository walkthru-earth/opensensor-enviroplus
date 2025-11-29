"""
System health monitoring utilities.

Captures device health metrics as "virtual sensor channels" for diagnostics:
- CPU temperature and load
- Memory and disk usage
- Network/WiFi status
- Power/UPS state (if available)
- Clock sync status (NTP)
- Uptime

These metrics help with remote monitoring and debugging field deployments.
Inspired by real-world IIoT experience where monitoring saved many deployments.
"""

import os
import socket
import struct
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class HealthMetrics:
    """System health metrics snapshot."""

    timestamp: datetime
    # CPU
    cpu_temp_c: float | None
    cpu_load_1min: float | None
    cpu_load_5min: float | None
    cpu_load_15min: float | None
    # Memory
    memory_total_mb: float | None
    memory_available_mb: float | None
    memory_percent_used: float | None
    # Disk
    disk_total_gb: float | None
    disk_free_gb: float | None
    disk_percent_used: float | None
    # Network
    wifi_ssid: str | None
    wifi_signal_dbm: int | None
    wifi_quality_percent: float | None
    ip_address: str | None
    # Clock
    clock_synced: bool | None
    ntp_offset_ms: float | None
    uptime_seconds: float | None
    # Power (for UPS-equipped deployments)
    power_source: str | None  # "mains", "battery", "unknown"
    battery_percent: float | None
    # Raspberry Pi Power/Voltage
    cpu_voltage_v: float | None
    throttled_hex: str | None


def get_cpu_temperature() -> float | None:
    """Get CPU temperature in Celsius."""
    try:
        with Path("/sys/class/thermal/thermal_zone0/temp").open() as f:
            return float(f.read()) / 1000.0
    except (OSError, ValueError):
        return None


def get_cpu_load() -> tuple[float | None, float | None, float | None]:
    """Get 1, 5, 15 minute load averages."""
    try:
        load1, load5, load15 = os.getloadavg()
        return load1, load5, load15
    except (OSError, AttributeError):
        return None, None, None


def get_memory_info() -> tuple[float | None, float | None, float | None]:
    """Get memory total, available, and percent used."""
    try:
        with Path("/proc/meminfo").open() as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    value = int(parts[1])  # in kB
                    meminfo[key] = value

            total_kb = meminfo.get("MemTotal", 0)
            available_kb = meminfo.get("MemAvailable", 0)

            if total_kb > 0:
                total_mb = total_kb / 1024
                available_mb = available_kb / 1024
                percent_used = ((total_kb - available_kb) / total_kb) * 100
                return total_mb, available_mb, percent_used
    except (OSError, ValueError, KeyError):
        pass
    return None, None, None


def get_disk_info(path: str = "/") -> tuple[float | None, float | None, float | None]:
    """Get disk total, free, and percent used for a path."""
    try:
        stat = os.statvfs(path)
        total_bytes = stat.f_blocks * stat.f_frsize
        free_bytes = stat.f_bavail * stat.f_frsize
        used_bytes = total_bytes - free_bytes

        total_gb = total_bytes / (1024**3)
        free_gb = free_bytes / (1024**3)
        percent_used = (used_bytes / total_bytes) * 100 if total_bytes > 0 else 0

        return total_gb, free_gb, percent_used
    except (OSError, ZeroDivisionError):
        return None, None, None


def get_wifi_info() -> tuple[str | None, int | None, float | None]:
    """Get WiFi SSID, signal strength (dBm), and quality percent."""
    ssid = None
    signal_dbm = None
    quality_percent = None

    # Try iwgetid for SSID
    try:
        result = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            ssid = result.stdout.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Try /proc/net/wireless for signal info
    try:
        with Path("/proc/net/wireless").open() as f:
            lines = f.readlines()
            for line in lines[2:]:  # Skip headers
                parts = line.split()
                if len(parts) >= 4:
                    # Quality is in format "XX." - remove trailing dot
                    quality_str = parts[2].rstrip(".")
                    quality = float(quality_str)
                    # Signal level in dBm (can be negative or need conversion)
                    signal_str = parts[3].rstrip(".")
                    signal = float(signal_str)

                    # Convert to dBm if positive (old format was 0-100)
                    if signal > 0:
                        signal_dbm = int(signal - 256) if signal > 100 else int(signal - 100)
                    else:
                        signal_dbm = int(signal)

                    # Quality as percentage (typically out of 70)
                    quality_percent = min(100, (quality / 70) * 100)
                    break
    except (OSError, ValueError, IndexError):
        pass

    return ssid, signal_dbm, quality_percent


def get_ip_address() -> str | None:
    """Get primary IP address."""
    try:
        # Try hostname -I first (common on Linux)
        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            ips = result.stdout.strip().split()
            if ips:
                return ips[0]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return None


def _get_ntp_offset_socket(server: str = "pool.ntp.org") -> float | None:
    """
    Get NTP offset using a direct socket connection (no external deps).

    Based on: https://github.com/python/cpython/blob/main/Lib/ntplib.py (simplified)
    """
    NTP_PACKET_FORMAT = "!12I"
    NTP_DELTA = 2208988800  # 1970-01-01 00:00:00
    NTP_MODE_CLIENT = 3
    NTP_VERSION = 3

    packet = bytearray(48)
    packet[0] = (NTP_VERSION << 3) | NTP_MODE_CLIENT

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2.0)
            s.sendto(packet, (server, 123))
            data, address = s.recvfrom(48)

            # Get receive time as soon as possible
            dest_timestamp = time.time() + NTP_DELTA

            unpacked = struct.unpack(NTP_PACKET_FORMAT, data)

            # Extract timestamps (seconds since 1900)
            # Reference timestamp (time packet left server)
            orig_timestamp = float(unpacked[6]) + float(unpacked[7]) / 2**32
            # Receive timestamp (time packet arrived at server)
            recv_timestamp = float(unpacked[8]) + float(unpacked[9]) / 2**32
            # Transmit timestamp (time packet left server)
            tx_timestamp = float(unpacked[10]) + float(unpacked[11]) / 2**32

            # Calculate offset
            # offset = ((recv - orig) + (tx - dest)) / 2
            offset = ((recv_timestamp - orig_timestamp) + (tx_timestamp - dest_timestamp)) / 2

            return offset * 1000  # Convert to ms

    except Exception:
        return None


def get_clock_sync_status() -> tuple[bool | None, float | None]:
    """
    Check if system clock is synchronized via NTP.

    Returns (is_synced, offset_ms).
    This is critical for time-series data integrity.
    """
    is_synced = None
    offset_ms = None

    # 1. Try timedatectl timesync-status (systemd-timesyncd default)
    try:
        result = subprocess.run(
            ["timedatectl", "timesync-status"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            is_synced = True  # If this command works, we are likely synced or trying
            for line in result.stdout.splitlines():
                if "Offset:" in line:
                    # Format: "       Offset: +1.234ms" or "+1s"
                    parts = line.split(":", 1)[1].strip().split()
                    if parts:
                        val_str = parts[0]
                        # Parse unit
                        if val_str.endswith("ms"):
                            offset_ms = float(val_str[:-2])
                        elif val_str.endswith("s"):
                            offset_ms = float(val_str[:-1]) * 1000
                        elif val_str.endswith("us"):
                            offset_ms = float(val_str[:-2]) / 1000
                        break
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        pass

    # 2. Try timedatectl show (generic systemd check)
    if is_synced is None:
        try:
            result = subprocess.run(
                ["timedatectl", "show", "--property=NTPSynchronized", "--value"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                is_synced = result.stdout.strip().lower() == "yes"
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    # 3. Try chronyc for offset (if installed)
    if offset_ms is None:
        try:
            result = subprocess.run(
                ["chronyc", "tracking"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "System time" in line:
                        parts = line.split()
                        if len(parts) >= 4:
                            offset_sec = float(parts[3])
                            offset_ms = offset_sec * 1000
                            break
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
            pass

    # 4. Try ntpq (if installed)
    if offset_ms is None:
        try:
            result = subprocess.run(["ntpq", "-c", "rv"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for part in result.stdout.split(","):
                    if "offset=" in part:
                        offset_str = part.split("=")[1].strip()
                        offset_ms = float(offset_str)
                        break
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
            pass

    # 5. Fallback: Direct NTP query (no external tools needed)
    if offset_ms is None:
        offset_ms = _get_ntp_offset_socket()

    return is_synced, offset_ms


def get_uptime() -> float | None:
    """Get system uptime in seconds."""
    try:
        with Path("/proc/uptime").open() as f:
            uptime_str = f.read().split()[0]
            return float(uptime_str)
    except (OSError, ValueError, IndexError):
        return None


def get_power_status() -> tuple[str | None, float | None]:
    """
    Get power source and battery status if available.

    Works with common UPS HATs for Raspberry Pi.
    """
    power_source = None
    battery_percent = None

    # Check for common power supply paths
    power_supply_path = Path("/sys/class/power_supply")

    if power_supply_path.exists():
        for supply in power_supply_path.iterdir():
            try:
                supply_type_file = supply / "type"
                if supply_type_file.exists():
                    supply_type = supply_type_file.read_text().strip().lower()

                    if supply_type == "battery":
                        # Read battery capacity
                        capacity_file = supply / "capacity"
                        if capacity_file.exists():
                            battery_percent = float(capacity_file.read_text().strip())

                        # Read status (Charging, Discharging, Full, etc.)
                        status_file = supply / "status"
                        if status_file.exists():
                            status = status_file.read_text().strip().lower()
                            if status == "discharging":
                                power_source = "battery"
                            elif status in ("charging", "full"):
                                power_source = "mains"

                    elif supply_type == "mains":
                        online_file = supply / "online"
                        if online_file.exists() and online_file.read_text().strip() == "1":
                            power_source = "mains"
            except (OSError, ValueError):
                continue

    return power_source, battery_percent


def get_vcgencmd_metrics() -> tuple[float | None, str | None]:
    """
    Get CPU voltage and throttling status using vcgencmd.

    Returns (cpu_voltage_v, throttled_hex).
    """
    voltage = None
    throttled = None

    # Get Core Voltage
    # Output: volt=0.8312V
    try:
        result = subprocess.run(
            ["vcgencmd", "measure_volts", "core"], capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            if output.startswith("volt=") and output.endswith("V"):
                voltage = float(output[5:-1])
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        pass

    # Get Throttled Status
    # Output: throttled=0x0
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            if output.startswith("throttled="):
                throttled = output.split("=")[1]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, IndexError):
        pass

    return voltage, throttled


def collect_health_metrics() -> HealthMetrics:
    """Collect all system health metrics."""
    cpu_load_1, cpu_load_5, cpu_load_15 = get_cpu_load()
    mem_total, mem_available, mem_percent = get_memory_info()
    disk_total, disk_free, disk_percent = get_disk_info()
    wifi_ssid, wifi_signal, wifi_quality = get_wifi_info()
    clock_synced, ntp_offset = get_clock_sync_status()
    power_source, battery_percent = get_power_status()
    cpu_voltage, throttled_hex = get_vcgencmd_metrics()

    # If we detect under-voltage via vcgencmd, update power_source
    if throttled_hex and throttled_hex != "0x0":
        try:
            throttled_val = int(throttled_hex, 16)
            # Bit 0: Under-voltage detected
            if throttled_val & 0x1:
                power_source = "under-voltage"
        except ValueError:
            pass

    return HealthMetrics(
        timestamp=datetime.now(timezone.utc),
        cpu_temp_c=get_cpu_temperature(),
        cpu_load_1min=cpu_load_1,
        cpu_load_5min=cpu_load_5,
        cpu_load_15min=cpu_load_15,
        memory_total_mb=mem_total,
        memory_available_mb=mem_available,
        memory_percent_used=mem_percent,
        disk_total_gb=disk_total,
        disk_free_gb=disk_free,
        disk_percent_used=disk_percent,
        wifi_ssid=wifi_ssid,
        wifi_signal_dbm=wifi_signal,
        wifi_quality_percent=wifi_quality,
        ip_address=get_ip_address(),
        clock_synced=clock_synced,
        ntp_offset_ms=ntp_offset,
        uptime_seconds=get_uptime(),
        power_source=power_source,
        battery_percent=battery_percent,
        cpu_voltage_v=cpu_voltage,
        throttled_hex=throttled_hex,
    )


def health_to_dict(metrics: HealthMetrics) -> dict[str, Any]:
    """Convert HealthMetrics to dictionary for Parquet storage."""
    return {
        "timestamp": metrics.timestamp,
        "cpu_temp_c": metrics.cpu_temp_c,
        "cpu_load_1min": metrics.cpu_load_1min,
        "cpu_load_5min": metrics.cpu_load_5min,
        "cpu_load_15min": metrics.cpu_load_15min,
        "memory_total_mb": metrics.memory_total_mb,
        "memory_available_mb": metrics.memory_available_mb,
        "memory_percent_used": metrics.memory_percent_used,
        "disk_total_gb": metrics.disk_total_gb,
        "disk_free_gb": metrics.disk_free_gb,
        "disk_percent_used": metrics.disk_percent_used,
        "wifi_ssid": metrics.wifi_ssid,
        "wifi_signal_dbm": metrics.wifi_signal_dbm,
        "wifi_quality_percent": metrics.wifi_quality_percent,
        "ip_address": metrics.ip_address,
        "clock_synced": metrics.clock_synced,
        "ntp_offset_ms": metrics.ntp_offset_ms,
        "uptime_seconds": metrics.uptime_seconds,
        "power_source": metrics.power_source,
        "battery_percent": metrics.battery_percent,
        "cpu_voltage_v": metrics.cpu_voltage_v,
        "throttled_hex": metrics.throttled_hex,
    }
