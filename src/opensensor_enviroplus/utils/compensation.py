"""
Temperature and humidity compensation utilities.
"""

from pathlib import Path


def get_cpu_temperature() -> float:
    """Get CPU temperature for compensation."""
    try:
        with Path("/sys/class/thermal/thermal_zone0/temp").open() as f:
            return float(f.read()) / 1000.0
    except (OSError, ValueError):
        return 40.0


def compensate_temperature(
    raw_temp: float,
    cpu_temp: float,
    factor: float = 2.25,
    avg_cpu_temp: float | None = None,
) -> float:
    """
    Compensate temperature for CPU heat.

    Args:
        raw_temp: Raw temperature from sensor
        cpu_temp: Current CPU temperature
        factor: Compensation factor (default 2.25)
        avg_cpu_temp: Optional average CPU temperature (if smoothing is used)

    Returns:
        Compensated temperature
    """
    ref_temp = avg_cpu_temp if avg_cpu_temp is not None else cpu_temp
    return raw_temp - ((ref_temp - raw_temp) / factor)


def compensate_humidity(raw_humidity: float, raw_temp: float, compensated_temp: float) -> float:
    """
    Compensate humidity based on corrected temperature using dewpoint.

    Args:
        raw_humidity: Raw humidity from sensor
        raw_temp: Raw temperature from sensor
        compensated_temp: Compensated temperature

    Returns:
        Compensated humidity
    """
    # Calculate dewpoint from raw (incorrect) readings
    dewpoint = raw_temp - ((100 - raw_humidity) / 5)

    # Recalculate humidity using compensated temperature
    compensated_humidity = 100 - (5 * (compensated_temp - dewpoint))

    # Clamp to valid range (0-100%)
    return max(0.0, min(100.0, compensated_humidity))
