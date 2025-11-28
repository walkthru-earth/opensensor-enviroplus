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

    # Hardware configuration
    pms5003_device: str = Field(
        default="/dev/serial0", description="Serial device for PMS5003 sensor"
    )

    # Output configuration
    output_dir: Path = Field(default=Path("output"), description="Directory for output data")
    compression: str = Field(
        default="snappy", description="Compression codec for Parquet files (snappy, zstd, gzip)"
    )

    # Health monitoring
    health_enabled: bool = Field(
        default=True, description="Enable system health monitoring (CPU, memory, WiFi, NTP sync)"
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
    """Cloud storage sync configuration.

    Supported providers:
    - s3: AWS S3
    - r2: Cloudflare R2 (S3-compatible)
    - gcs: Google Cloud Storage
    - azure: Azure Blob Storage
    - minio: MinIO (S3-compatible, self-hosted)
    - wasabi: Wasabi (S3-compatible)
    - backblaze: Backblaze B2 (S3-compatible)
    - hetzner: Hetzner Object Storage (S3-compatible)
    """

    # Sync settings
    sync_enabled: bool = Field(default=False, description="Enable automatic cloud sync")
    sync_interval_minutes: int = Field(
        default=15, description="Minutes between sync operations", ge=1
    )

    # Provider selection (s3, r2, gcs, azure, minio, wasabi, backblaze, hetzner)
    storage_provider: str = Field(
        default="s3",
        description="Storage provider: s3, r2, gcs, azure, minio, wasabi, backblaze, hetzner",
    )

    # Common settings (all providers)
    storage_bucket: str | None = Field(default=None, description="Bucket/container name")
    storage_prefix: str | None = Field(default=None, description="Prefix/path within bucket")
    storage_region: str = Field(default="us-west-2", description="Storage region")
    storage_endpoint: str | None = Field(default=None, description="Custom endpoint URL")

    # S3-compatible credentials (AWS, R2, MinIO, Wasabi, Backblaze, Hetzner)
    aws_access_key_id: str | None = Field(default=None, description="Access key ID")
    aws_secret_access_key: str | None = Field(default=None, description="Secret access key")

    # Google Cloud Storage credentials
    gcs_service_account_path: str | None = Field(
        default=None, description="Path to GCS service account JSON file"
    )

    # Azure credentials
    azure_storage_account: str | None = Field(default=None, description="Azure storage account")
    azure_storage_key: str | None = Field(default=None, description="Azure storage account key")
    azure_sas_token: str | None = Field(default=None, description="Azure SAS token")

    model_config = SettingsConfigDict(
        env_prefix="OPENSENSOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("storage_provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        """Validate storage provider is supported."""
        valid_providers = {"s3", "r2", "gcs", "azure", "minio", "wasabi", "backblaze", "hetzner"}
        v_lower = v.lower()
        if v_lower not in valid_providers:
            raise ValueError(
                f"Invalid provider '{v}'. Must be one of: {', '.join(valid_providers)}"
            )
        return v_lower


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
