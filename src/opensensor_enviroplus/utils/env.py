"""
Environment and configuration utilities.

Shared functions for:
- .env file parsing and writing
- User/group detection
- Virtual environment detection
- Path discovery
"""

import os
import sys
from pathlib import Path


def get_current_user() -> str:
    """Get the current user, handling sudo correctly."""
    return os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"


def get_user_home(username: str | None = None) -> Path:
    """Get home directory for a user."""
    if username is None:
        username = get_current_user()

    try:
        import pwd

        return Path(pwd.getpwnam(username).pw_dir)
    except (ImportError, KeyError):
        return Path(os.environ.get("HOME", f"/home/{username}"))


def get_user_group(username: str | None = None) -> str:
    """Get the primary group for a user."""
    if username is None:
        username = get_current_user()

    try:
        import grp
        import pwd

        user_info = pwd.getpwnam(username)
        group_info = grp.getgrgid(user_info.pw_gid)
        return group_info.gr_name
    except (ImportError, KeyError):
        return username


def detect_virtual_env() -> Path | None:
    """Detect if running in a virtual environment."""
    # Check VIRTUAL_ENV (set by venv activation and uv)
    if venv := os.environ.get("VIRTUAL_ENV"):
        return Path(venv)

    # Check if sys.prefix differs from sys.base_prefix (indicates venv)
    if sys.prefix != sys.base_prefix:
        return Path(sys.prefix)

    return None


def detect_installation_type() -> str:
    """
    Detect how the package was installed.

    Returns one of:
    - "uvx_ephemeral": Running from uvx cache (temporary)
    - "uv_tool": Installed via uv tool install
    - "venv": Traditional virtual environment
    - "editable": Development/editable install from source
    - "system": System-wide Python
    - "unknown": Could not determine
    """
    exe_str = str(Path(sys.executable).resolve())

    # Check for uvx ephemeral (cache directory)
    if ".cache/uv" in exe_str:
        return "uvx_ephemeral"

    # Check for uv tool install
    if "/uv/tools/" in exe_str or "\\uv\\tools\\" in exe_str:
        return "uv_tool"

    # Check for editable install (source directory with pyproject.toml)
    try:
        current = Path(__file__).resolve()
        for parent in current.parents:
            if (parent / "pyproject.toml").exists():
                return "editable"
    except Exception:
        pass

    # Check for virtual environment
    if os.environ.get("VIRTUAL_ENV"):
        return "venv"

    # Check for system Python
    if "/usr/" in exe_str:
        return "system"

    return "unknown"


def parse_env_file(env_path: Path) -> dict[str, str]:
    """
    Parse an existing .env file into a dictionary.

    Args:
        env_path: Path to the .env file

    Returns:
        Dictionary of KEY=VALUE pairs (comments and empty lines are skipped)
    """
    config: dict[str, str] = {}
    if not env_path.exists():
        return config

    for line in env_path.read_text().splitlines():
        line = line.strip()
        # Skip comments and empty lines
        if not line or line.startswith("#"):
            continue
        # Parse KEY=VALUE
        if "=" in line:
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()

    return config


def find_env_file(search_paths: list[Path] | None = None) -> Path | None:
    """
    Find .env file by searching common locations.

    Args:
        search_paths: Optional list of paths to search. If None, uses defaults.

    Returns:
        Path to .env file if found, None otherwise.
    """
    if search_paths is None:
        user = get_current_user()
        home = get_user_home(user)

        search_paths = [
            Path.cwd(),  # Current working directory
        ]

        # PWD environment (preserved through sudo)
        if pwd_env := os.environ.get("PWD"):
            search_paths.append(Path(pwd_env))

        # User's home directory
        search_paths.append(home)

    for search_path in search_paths:
        env_file = search_path / ".env"
        if env_file.exists():
            return env_file

    return None


def write_env_file(
    env_path: Path,
    config: dict[str, str],
    station_id: str | None = None,
) -> None:
    """
    Write configuration to .env file with proper formatting.

    Args:
        env_path: Path to write the .env file
        config: Dictionary of configuration values
        station_id: Station ID (used for generating prefix in comments)
    """
    # Use station_id from config if not provided
    if station_id is None:
        station_id = config.get("OPENSENSOR_STATION_ID", "")

    lines = [
        "# OpenSensor.Space Configuration",
        "# https://opensensor.space",
        "",
        "# Station Configuration",
        f"OPENSENSOR_STATION_ID={config.get('OPENSENSOR_STATION_ID', station_id)}",
        "",
        "# Data Collection",
        f"OPENSENSOR_READ_INTERVAL={config.get('OPENSENSOR_READ_INTERVAL', '5')}",
        f"OPENSENSOR_BATCH_DURATION={config.get('OPENSENSOR_BATCH_DURATION', '900')}",
        "",
        "# Output Settings",
        f"OPENSENSOR_OUTPUT_DIR={config.get('OPENSENSOR_OUTPUT_DIR', 'output')}",
        f"OPENSENSOR_COMPRESSION={config.get('OPENSENSOR_COMPRESSION', 'zstd')}",
        "",
        "# Logging",
        f"OPENSENSOR_LOG_LEVEL={config.get('OPENSENSOR_LOG_LEVEL', 'INFO')}",
        "",
        "# Health Monitoring (CPU, memory, disk, WiFi, NTP sync)",
        f"OPENSENSOR_HEALTH_ENABLED={config.get('OPENSENSOR_HEALTH_ENABLED', 'true')}",
    ]

    # Cloud sync section
    sync_enabled = config.get("OPENSENSOR_SYNC_ENABLED", "").lower() == "true"

    if sync_enabled:
        lines.extend(
            [
                "",
                "# Cloud Sync",
                f"OPENSENSOR_SYNC_ENABLED={config.get('OPENSENSOR_SYNC_ENABLED', 'false')}",
                f"OPENSENSOR_SYNC_INTERVAL_MINUTES={config.get('OPENSENSOR_SYNC_INTERVAL_MINUTES', '15')}",
                "",
                "# S3/MinIO Storage Settings",
                f"OPENSENSOR_STORAGE_BUCKET={config.get('OPENSENSOR_STORAGE_BUCKET', '')}",
                f"OPENSENSOR_STORAGE_PREFIX={config.get('OPENSENSOR_STORAGE_PREFIX', '')}",
                f"OPENSENSOR_STORAGE_REGION={config.get('OPENSENSOR_STORAGE_REGION', 'us-west-2')}",
                "",
                "# AWS Credentials",
                f"OPENSENSOR_AWS_ACCESS_KEY_ID={config.get('OPENSENSOR_AWS_ACCESS_KEY_ID', '')}",
                f"OPENSENSOR_AWS_SECRET_ACCESS_KEY={config.get('OPENSENSOR_AWS_SECRET_ACCESS_KEY', '')}",
            ]
        )
        if endpoint := config.get("OPENSENSOR_STORAGE_ENDPOINT"):
            lines.append(f"OPENSENSOR_STORAGE_ENDPOINT={endpoint}")
    else:
        # Add commented template
        short_id = station_id[:8] if station_id else "xxxxxxxx"
        lines.extend(
            [
                "",
                "# Cloud Sync (uncomment and configure to enable)",
                "# OPENSENSOR_SYNC_ENABLED=true",
                "# OPENSENSOR_SYNC_INTERVAL_MINUTES=15",
                "",
                "# S3/MinIO Storage Settings",
                "# OPENSENSOR_STORAGE_BUCKET=my-sensor-bucket",
                f"# OPENSENSOR_STORAGE_PREFIX=sensors/station-{short_id}",
                "# OPENSENSOR_STORAGE_REGION=us-west-2",
                "",
                "# AWS Credentials",
                "# OPENSENSOR_AWS_ACCESS_KEY_ID=your-access-key-id",
                "# OPENSENSOR_AWS_SECRET_ACCESS_KEY=your-secret-access-key",
                "",
                "# MinIO/Custom S3 Endpoint (optional)",
                "# OPENSENSOR_STORAGE_ENDPOINT=https://minio.example.com:9000",
            ]
        )

    env_path.write_text("\n".join(lines) + "\n")


def ensure_directories(*paths: str | Path) -> None:
    """Create directories if they don't exist."""
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)
