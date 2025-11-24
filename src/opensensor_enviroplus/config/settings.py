"""
Configuration management using Pydantic Settings.
Supports environment variables, .env files, and CLI overrides.
"""

from pathlib import Path
from uuid import UUID

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SensorConfig(BaseSettings):
    """Sensor collection configuration."""

    # Station identification
    station_id: UUID = Field(description="Unique UUID for this sensor station")

    # Reading configuration
    read_interval: int = Field(default=5, description="Seconds between sensor reads", ge=1, le=60)
    batch_duration: int = Field(
        default=900, description="Seconds per batch (900 = 15 minutes)", ge=60, le=3600
    )

    # Temperature compensation
    temp_compensation_enabled: bool = Field(
        default=True, description="Enable CPU temperature compensation"
    )
    temp_compensation_factor: float = Field(
        default=2.25, description="Temperature compensation factor", gt=0
    )

    # Output configuration
    output_dir: Path = Field(default=Path("output"), description="Directory for output data")
    compression: str = Field(
        default="snappy", description="Compression codec for Parquet files (snappy, zstd, gzip)"
    )

    model_config = SettingsConfigDict(
        env_prefix="OPENSENSOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("output_dir", mode="before")
    @classmethod
    def expand_path(cls, v: Path | str) -> Path:
        """Expand ~ and environment variables in paths."""
        return Path(v).expanduser().resolve()


class StorageConfig(BaseSettings):
    """Cloud storage sync configuration."""

    # Sync settings
    sync_enabled: bool = Field(default=False, description="Enable automatic cloud sync")
    sync_interval_minutes: int = Field(
        default=15, description="Minutes between sync operations", ge=1
    )

    # S3-compatible storage
    storage_provider: str = Field(default="s3", description="Storage provider type")
    storage_endpoint: str | None = Field(default=None, description="S3 endpoint URL (optional)")
    storage_region: str = Field(default="us-west-2", description="Storage region")
    storage_bucket: str | None = Field(default=None, description="Storage bucket name")
    storage_prefix: str | None = Field(default=None, description="Prefix/path within bucket")

    # Credentials
    aws_access_key_id: str | None = Field(default=None, description="AWS access key ID")
    aws_secret_access_key: str | None = Field(default=None, description="AWS secret access key")

    model_config = SettingsConfigDict(
        env_prefix="OPENSENSOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


class AppConfig(BaseSettings):
    """Application-wide configuration."""

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")
    log_dir: Path = Field(default=Path("logs"), description="Directory for log files")
    log_json: bool = Field(default=False, description="Use JSON log format")

    model_config = SettingsConfigDict(
        env_prefix="OPENSENSOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("log_dir", mode="before")
    @classmethod
    def expand_path(cls, v: Path | str) -> Path:
        """Expand ~ and environment variables in paths."""
        return Path(v).expanduser().resolve()
