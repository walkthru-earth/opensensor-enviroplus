"""
Configuration management using Pydantic Settings.
Supports environment variables, .env files, and CLI overrides.
"""

from pathlib import Path
from uuid import UUID

from pydantic import Field, field_validator, model_validator
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
        default="zstd", description="Compression codec for Parquet files (snappy, zstd, gzip)"
    )
    health_dir: Path | None = Field(default=None, description="Directory for health data")

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

    @model_validator(mode="after")
    def compute_health_dir(self) -> "SensorConfig":
        """Default health_dir to output_dir + '-health' if not set or empty."""
        # Handle both None and empty Path("") cases
        if self.health_dir is None or str(self.health_dir) == "":
            self.health_dir = Path(str(self.output_dir) + "-health")
        return self


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


class HealthStorageConfig(StorageConfig):
    """
    Health data storage configuration.
    Inherits all fields from StorageConfig but uses OPENSENSOR_HEALTH_ prefix.
    """

    model_config = SettingsConfigDict(
        env_prefix="OPENSENSOR_HEALTH_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def with_fallback(cls, main_config: StorageConfig | None) -> "HealthStorageConfig":
        """
        Create HealthStorageConfig with fallback to main StorageConfig.

        This factory method handles the common case where health data should use
        the same storage credentials as the main data, but with a different prefix.

        Inheritance rules:
        1. If OPENSENSOR_HEALTH_* env vars are set, they take precedence
        2. If not set, inherit from main_config
        3. If main sync is enabled but health sync isn't explicitly configured,
           enable health sync automatically with inherited settings
        4. Default health prefix is "{main_prefix}-health" if not specified

        Args:
            main_config: The main StorageConfig to inherit from (can be None)

        Returns:
            HealthStorageConfig with proper fallback values
        """
        # First, load from environment (OPENSENSOR_HEALTH_* prefix)
        health_config = cls()

        if main_config is None:
            return health_config

        # Inherit sync_enabled if not explicitly set and main is enabled
        if not health_config.sync_enabled and main_config.sync_enabled:
            health_config = health_config.model_copy(update={"sync_enabled": True})

        # If health sync is enabled but no bucket configured, inherit everything
        if health_config.sync_enabled and not health_config.storage_bucket:
            updates = {
                "storage_provider": main_config.storage_provider,
                "storage_bucket": main_config.storage_bucket,
                "storage_region": main_config.storage_region,
                "storage_endpoint": main_config.storage_endpoint,
                "aws_access_key_id": main_config.aws_access_key_id,
                "aws_secret_access_key": main_config.aws_secret_access_key,
                "gcs_service_account_path": main_config.gcs_service_account_path,
                "azure_storage_account": main_config.azure_storage_account,
                "azure_storage_key": main_config.azure_storage_key,
                "azure_sas_token": main_config.azure_sas_token,
            }
            # Set default health prefix
            if not health_config.storage_prefix and main_config.storage_prefix:
                updates["storage_prefix"] = f"{main_config.storage_prefix}-health"

            health_config = health_config.model_copy(update=updates)

        # If bucket is set but credentials are missing, inherit credentials only
        # (user may have set a custom prefix but wants to reuse main credentials)
        elif (
            health_config.sync_enabled
            and health_config.storage_bucket
            and health_config.storage_provider == main_config.storage_provider
        ):
            updates = {}

            # Inherit S3-compatible credentials
            if not health_config.aws_access_key_id and main_config.aws_access_key_id:
                updates["aws_access_key_id"] = main_config.aws_access_key_id
            if not health_config.aws_secret_access_key and main_config.aws_secret_access_key:
                updates["aws_secret_access_key"] = main_config.aws_secret_access_key

            # Inherit GCS credentials
            if not health_config.gcs_service_account_path and main_config.gcs_service_account_path:
                updates["gcs_service_account_path"] = main_config.gcs_service_account_path

            # Inherit Azure credentials
            if not health_config.azure_storage_account and main_config.azure_storage_account:
                updates["azure_storage_account"] = main_config.azure_storage_account
            if not health_config.azure_storage_key and main_config.azure_storage_key:
                updates["azure_storage_key"] = main_config.azure_storage_key
            if not health_config.azure_sas_token and main_config.azure_sas_token:
                updates["azure_sas_token"] = main_config.azure_sas_token

            # Inherit region/endpoint if using defaults
            if health_config.storage_region == "us-west-2":  # Default value
                updates["storage_region"] = main_config.storage_region
            if not health_config.storage_endpoint and main_config.storage_endpoint:
                updates["storage_endpoint"] = main_config.storage_endpoint

            if updates:
                health_config = health_config.model_copy(update=updates)

        return health_config


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
