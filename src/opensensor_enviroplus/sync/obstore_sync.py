"""
Cloud storage sync using obstore (modern Rust-based object store library).
Supports S3, GCS, Azure, and more with high performance.

Features:
- Incremental sync (only uploads new/modified files)
- Offline-first (works without internet, syncs when available)
- Metadata comparison (size + last_modified)
- Bandwidth efficient
"""

import logging
from datetime import datetime, timezone
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
        self.remote_cache: dict[str, dict] = {}  # Cache of remote file metadata
        self.is_offline = False  # Track offline state

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
        Incrementally sync local directory to cloud storage.

        Only uploads new or modified files based on metadata comparison.
        Works offline - continues local collection, syncs when network available.

        The store prefix is automatically applied by obstore from the URL.
        All paths are relative to the configured prefix.

        Args:
            local_dir: Local directory to sync

        Returns:
            Number of files synced (0 if offline or no new files)
        """
        if not self.store:
            self.logger.warning("Sync not configured")
            return 0

        local_dir = Path(local_dir)
        if not local_dir.exists():
            self.logger.warning(f"Directory not found: {local_dir}")
            return 0

        files_synced = 0
        files_skipped = 0

        try:
            # Refresh remote file cache
            self._refresh_remote_cache()

            # If offline, skip sync but don't fail
            if self.is_offline:
                self.logger.info("Offline - skipping sync, will retry next interval")
                return 0

            # Find all Parquet files (Hive-partitioned structure)
            for file_path in local_dir.rglob("*.parquet"):
                # Create remote path maintaining directory structure
                relative_path = file_path.relative_to(local_dir)
                remote_path = str(relative_path).replace("\\", "/")

                # Check if file needs upload
                if self._should_upload(file_path, remote_path):
                    self._upload_file(file_path, remote_path)
                    files_synced += 1
                else:
                    files_skipped += 1
                    self.logger.debug(f"Skipping {file_path.name} (already synced)")

            if files_synced > 0:
                log_status(
                    f"Synced {files_synced} new files to {self.config.storage_bucket} "
                    f"({files_skipped} already synced)",
                    self.logger,
                    "SYNC",
                )
            elif files_skipped > 0:
                self.logger.info(f"All {files_skipped} files already synced")

        except Exception as e:
            # Network error - mark as offline, continue collecting
            if "connection" in str(e).lower() or "network" in str(e).lower():
                self.is_offline = True
                self.logger.warning(f"Network error - going offline: {e}")
            else:
                log_error(e, self.logger, f"Sync failed after {files_synced} files")

        return files_synced

    def _refresh_remote_cache(self) -> None:
        """
        Refresh cache of remote file metadata.

        Uses obstore.list() to get metadata (path, size, last_modified, etag).
        This enables incremental sync by comparing local vs remote state.
        """
        if not self.store:
            return

        try:
            # List all remote files with metadata
            # obstore returns: {'path': str, 'size': int, 'last_modified': datetime, ...}
            remote_objects = list(self.store.list())

            # Build cache: {path: metadata}
            self.remote_cache = {obj["path"]: obj for obj in remote_objects}

            # Back online if we were offline
            if self.is_offline:
                self.logger.info("Network restored - back online")
                self.is_offline = False

            self.logger.debug(f"Remote cache refreshed: {len(self.remote_cache)} files")

        except Exception as e:
            # Network error - mark offline
            if "connection" in str(e).lower() or "network" in str(e).lower():
                if not self.is_offline:
                    self.logger.warning("Network unavailable - working offline")
                self.is_offline = True
            else:
                log_error(e, self.logger, "Failed to refresh remote cache")

    def _calculate_etag(self, file_path: Path) -> str:
        """
        Calculate ETag (MD5 hash) for local file.

        For single-part uploads (<5MB), S3 ETag = MD5 hash of content.
        Our parquet files are ~50KB, so always single-part.

        Args:
            file_path: Path to local file

        Returns:
            ETag string (MD5 hash in quotes, matching S3 format)
        """
        import hashlib

        md5 = hashlib.md5()
        with file_path.open("rb") as f:
            # Read in chunks to handle larger files efficiently
            for chunk in iter(lambda: f.read(8192), b""):
                md5.update(chunk)

        # S3 returns ETag with quotes, match that format
        return f'"{md5.hexdigest()}"'

    def _should_upload(self, local_path: Path, remote_path: str) -> bool:
        """
        Determine if file should be uploaded based on content hash (ETag).

        Uses ETag (MD5 checksum) comparison - most reliable method:
        1. File existence (upload if not exists remotely)
        2. File size (quick check, upload if different)
        3. ETag/MD5 hash (content-based, upload if different)

        This is the same method AWS CLI uses with --checksum flag.

        Args:
            local_path: Local file path
            remote_path: Remote file path

        Returns:
            True if file should be uploaded, False if content matches
        """
        # If not in cache, definitely upload
        if remote_path not in self.remote_cache:
            return True

        remote_meta = self.remote_cache[remote_path]

        # Quick size check first (avoid MD5 calculation if size differs)
        local_size = local_path.stat().st_size
        if local_size != remote_meta["size"]:
            self.logger.debug(
                f"{local_path.name}: size mismatch "
                f"(local={local_size}, remote={remote_meta['size']})"
            )
            return True

        # Content-based comparison using ETag (MD5 hash)
        local_etag = self._calculate_etag(local_path)
        remote_etag = remote_meta.get("e_tag", "")

        if local_etag != remote_etag:
            self.logger.debug(
                f"{local_path.name}: content changed "
                f"(local_etag={local_etag[:16]}..., remote_etag={remote_etag[:16]}...)"
            )
            return True

        # Content matches - skip upload
        self.logger.debug(f"{local_path.name}: content matches (ETag: {local_etag[:16]}...)")
        return False

    def _upload_file(self, local_path: Path, remote_path: str) -> None:
        """
        Upload single file to object store with checksum validation.

        S3 automatically validates MD5 checksum on upload.
        """
        try:
            # Read file data
            data = local_path.read_bytes()

            # Upload to store using put
            # Note: S3 automatically validates Content-MD5 on upload
            self.store.put(remote_path, data)

            # Update cache with new file metadata
            local_stat = local_path.stat()
            local_etag = self._calculate_etag(local_path)

            self.remote_cache[remote_path] = {
                "path": remote_path,
                "size": local_stat.st_size,
                "last_modified": datetime.fromtimestamp(local_stat.st_mtime, tz=timezone.utc),
                "e_tag": local_etag,
            }

            self.logger.debug(f"Uploaded {local_path.name} (ETag: {local_etag[:16]}...)")

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
