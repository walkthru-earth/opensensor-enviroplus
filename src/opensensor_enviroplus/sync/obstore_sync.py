"""
Cloud storage sync using obstore (modern Rust-based object store library).
Supports S3, GCS, Azure, and more with high performance.
"""

import logging
from pathlib import Path

from obstore.store import S3Store, from_url

from opensensor_enviroplus.config.settings import StorageConfig
from opensensor_enviroplus.utils.logging import log_error, log_status


class ObstoreSync:
    """
    Efficient cloud sync using obstore.

    Benefits over boto3:
    - 50% faster for large files
    - Better memory efficiency
    - Unified API for S3, GCS, Azure
    - Built in Rust for performance
    """

    def __init__(self, config: StorageConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.store: S3Store | None = None

        if config.sync_enabled:
            self._init_store()

    def _init_store(self) -> None:
        """Initialize object store connection using from_url with prefix support."""
        try:
            # Build S3 URL with prefix for proper IAM scoping
            # Example: s3://bucket/prefix/path -> all operations scoped to prefix
            bucket = self.config.storage_bucket
            url = f"s3://{bucket}"

            # Include prefix in URL for automatic scoping (IAM policies)
            if self.config.storage_prefix:
                # Strip leading/trailing slashes for clean URL construction
                prefix = self.config.storage_prefix.strip("/")
                url = f"{url}/{prefix}"

            # Configure credentials via environment or config
            config_dict = {}
            if self.config.aws_access_key_id:
                config_dict["aws_access_key_id"] = self.config.aws_access_key_id
            if self.config.aws_secret_access_key:
                config_dict["aws_secret_access_key"] = self.config.aws_secret_access_key
            if self.config.storage_region:
                config_dict["aws_region"] = self.config.storage_region
            if self.config.storage_endpoint:
                config_dict["aws_endpoint"] = self.config.storage_endpoint

            # Create store from URL - prefix automatically extracted from URL path
            self.store = from_url(url, config=config_dict)

            log_status(
                f"Connected to {self.config.storage_provider}: {bucket}"
                + (f"/{self.config.storage_prefix}" if self.config.storage_prefix else ""),
                self.logger,
                "CLOUD",
            )

        except Exception as e:
            log_error(e, self.logger, "Failed to initialize object store")
            self.store = None

    def sync_directory(self, local_dir: Path) -> int:
        """
        Sync local directory to cloud storage.

        The store prefix is automatically applied by obstore from the URL.
        All paths are relative to the configured prefix.

        Args:
            local_dir: Local directory to sync

        Returns:
            Number of files synced
        """
        if not self.store:
            self.logger.warning("Sync not configured")
            return 0

        local_dir = Path(local_dir)
        if not local_dir.exists():
            self.logger.warning(f"Directory not found: {local_dir}")
            return 0

        files_synced = 0

        try:
            # Find all Parquet files (Hive-partitioned structure)
            for file_path in local_dir.rglob("*.parquet"):
                # Create remote path maintaining directory structure
                # Obstore automatically applies prefix from URL
                relative_path = file_path.relative_to(local_dir)
                remote_path = str(relative_path).replace("\\", "/")

                # Upload file (prefix auto-applied by obstore)
                self._upload_file(file_path, remote_path)
                files_synced += 1

            log_status(
                f"Synced {files_synced} files to {self.config.storage_bucket}", self.logger, "SYNC"
            )

        except Exception as e:
            log_error(e, self.logger, f"Sync failed after {files_synced} files")

        return files_synced

    def _upload_file(self, local_path: Path, remote_path: str) -> None:
        """Upload single file to object store."""
        try:
            # Read file data
            data = local_path.read_bytes()

            # Upload to store using put
            self.store.put(remote_path, data)

            self.logger.debug(f"Uploaded {local_path.name} to {remote_path}")

        except Exception as e:
            log_error(e, self.logger, f"Failed to upload {local_path.name}")
            raise

    def list_remote_files(self, subpath: str = "") -> list[str]:
        """
        List files in remote storage.

        Args:
            subpath: Optional subpath within the configured prefix

        Returns:
            List of remote file paths
        """
        if not self.store:
            return []

        try:
            # List objects - store prefix already applied from URL
            objects = list(self.store.list(prefix=subpath))
            return [obj["path"] for obj in objects]

        except Exception as e:
            log_error(e, self.logger, "Failed to list remote files")
            return []
