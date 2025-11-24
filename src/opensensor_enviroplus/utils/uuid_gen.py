"""
UUID generation utilities.
Prefers UUID v7 (time-ordered) with fallback to UUID v4.
"""

from uuid import UUID, uuid4


def generate_station_id() -> str:
    """
    Generate a station ID using UUID v7 (time-ordered) with fallback to UUID v4.

    UUID v7 benefits:
    - Time-ordered: sortable by creation time
    - Better database performance: sequential IDs improve index performance
    - Better partitioning: related data groups together
    - Globally unique across all stations

    Uses uuid6 package for Python 3.10-3.13, native uuid.uuid7() in Python 3.14+.
    UUID v7 format: 48-bit timestamp + 12-bit random + 2-bit variant + 62-bit random.

    Returns:
        UUID string (lowercase with hyphens)
    """
    try:
        # Use uuid6 package which provides RFC 9562 UUID v7
        from uuid6 import uuid7

        return str(uuid7())
    except ImportError:
        # Fallback to UUID v4 if uuid6 package not available
        return str(uuid4())


def validate_station_id(station_id: str) -> bool:
    """
    Validate that a string is a valid UUID.

    Args:
        station_id: Station ID to validate

    Returns:
        True if valid UUID, False otherwise
    """
    try:
        UUID(station_id)
        return True
    except (ValueError, AttributeError):
        return False
