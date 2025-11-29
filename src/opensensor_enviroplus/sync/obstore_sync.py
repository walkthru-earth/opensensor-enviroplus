"""
Cloud storage sync using obstore (modern Rust-based object store library).
Supports multiple providers: S3, GCS, Azure, and S3-compatible services.

Supported providers:
- s3: AWS S3
- r2: Cloudflare R2 (S3-compatible, no egress fees)
- gcs: Google Cloud Storage
- azure: Azure Blob Storage
- minio: MinIO (S3-compatible, self-hosted)
- wasabi: Wasabi (S3-compatible, affordable)
- backblaze: Backblaze B2 (S3-compatible)
- hetzner: Hetzner Object Storage (S3-compatible)

Features:
- Incremental sync (only uploads new/modified files)
- Offline-first (works without internet, syncs when available)
- Content-based comparison (ETag/MD5)
- Bandwidth efficient
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from obstore.store import AzureStore, GCSStore, S3Store

from opensensor_enviroplus.config.settings import StorageConfig
from opensensor_enviroplus.utils.logging import log_error, log_status

# Provider endpoint templates for S3-compatible services
S3_COMPATIBLE_ENDPOINTS = {
    "r2": "https://{account_id}.r2.cloudflarestorage.com",
    "wasabi": "https://s3.{region}.wasabisys.com",
    "backblaze": "https://s3.{region}.backblazeb2.com",
    "hetzner": "https://{region}.your-objectstorage.com",
    "minio": None,  # User provides endpoint
}


class ObstoreSync:
    """
    Efficient cloud sync using obstore.

    Benefits over boto3:
    - 50% faster for large files
    - Better memory efficiency
    - Unified API for S3, GCS, Azure
    - Built in Rust for performance

    Supported providers:
    - s3: AWS S3 (native)
    - r2: Cloudflare R2 (S3-compatible)
    - gcs: Google Cloud Storage (native)
    - azure: Azure Blob Storage (native)
    - minio: MinIO (S3-compatible)
    - wasabi: Wasabi (S3-compatible)
    - backblaze: Backblaze B2 (S3-compatible)
    - hetzner: Hetzner Object Storage (S3-compatible)
    """

    def __init__(self, config: StorageConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.store: S3Store | GCSStore | AzureStore | None = None
        self.remote_cache: dict[str, dict] = {}  # Cache of remote file metadata
        self.is_offline = False  # Track offline state

        if config.sync_enabled:
            self._init_store()

    def _init_store(self) -> None:
        """Initialize object store based on configured provider."""
        provider = self.config.storage_provider.lower()

        try:
            if provider == "gcs":
                self._init_gcs()
            elif provider == "azure":
                self._init_azure()
            else:
                # S3 and all S3-compatible providers
                self._init_s3_compatible(provider)

            if self.store:
                bucket = self.config.storage_bucket
                prefix = f"/{self.config.storage_prefix}" if self.config.storage_prefix else ""
                log_status(f"Connected to {provider}: {bucket}{prefix}", self.logger, "CLOUD")

        except Exception as e:
            log_error(e, self.logger, f"Failed to initialize {provider} store")
            self.store = None

    def _init_s3_compatible(self, provider: str) -> None:
        """Initialize S3 or S3-compatible store (R2, MinIO, Wasabi, etc.)."""
        bucket = self.config.storage_bucket
        if not bucket:
            raise ValueError("storage_bucket is required")

        # Build config dict
        config_dict: dict[str, Any] = {}

        # Region
        if self.config.storage_region:
            config_dict["aws_region"] = self.config.storage_region

        # Endpoint (custom or provider-specific)
        endpoint = self._get_endpoint(provider)
        if endpoint:
            config_dict["aws_endpoint"] = endpoint

        # For non-AWS S3-compatible services, disable virtual hosted style
        if provider in ("r2", "minio", "wasabi", "backblaze", "hetzner"):
            config_dict["aws_virtual_hosted_style_request"] = "false"

        # Allow HTTP for local MinIO
        if provider == "minio" and endpoint and endpoint.startswith("http://"):
            config_dict["aws_allow_http"] = "true"

        # Build URL with prefix
        url = f"s3://{bucket}"
        if self.config.storage_prefix:
            prefix = self.config.storage_prefix.strip("/")
            url = f"{url}/{prefix}"

        # Use credential provider to prevent IMDS fallback on non-EC2 environments.
        # When obstore doesn't find credentials in config, it falls back to the AWS
        # credential chain which includes IMDS (169.254.169.254). On Raspberry Pi
        # or other non-EC2 hosts, this causes a 15+ second timeout.
        # Using a credential provider bypasses the entire fallback chain.
        credential_provider = None
        if self.config.aws_access_key_id and self.config.aws_secret_access_key:
            # Create a credential provider closure that returns static credentials
            # obstore requires: access_key_id, secret_access_key, and optionally
            # token (for session tokens) and expires_at (datetime for expiry)
            access_key = self.config.aws_access_key_id
            secret_key = self.config.aws_secret_access_key

            def get_credentials() -> dict[str, Any]:
                return {
                    "access_key_id": access_key,
                    "secret_access_key": secret_key,
                    "token": None,  # No session token for static credentials
                    "expires_at": None,  # Static credentials don't expire
                }

            credential_provider = get_credentials
        else:
            # No credentials provided - use skip_signature for anonymous access
            # This prevents IMDS lookup for public buckets
            config_dict["skip_signature"] = "true"

        self.store = S3Store.from_url(
            url, config=config_dict, credential_provider=credential_provider
        )

    def _init_gcs(self) -> None:
        """Initialize Google Cloud Storage store."""
        bucket = self.config.storage_bucket
        if not bucket:
            raise ValueError("storage_bucket is required")

        config_dict: dict[str, Any] = {}

        # Service account credentials
        if self.config.gcs_service_account_path:
            config_dict["google_service_account_path"] = self.config.gcs_service_account_path

        # Build URL with prefix
        url = f"gs://{bucket}"
        if self.config.storage_prefix:
            prefix = self.config.storage_prefix.strip("/")
            url = f"{url}/{prefix}"

        self.store = GCSStore.from_url(url, config=config_dict)

    def _init_azure(self) -> None:
        """Initialize Azure Blob Storage store."""
        container = self.config.storage_bucket
        if not container:
            raise ValueError("storage_bucket (container name) is required")

        account = self.config.azure_storage_account
        if not account:
            raise ValueError("azure_storage_account is required")

        config_dict: dict[str, Any] = {
            "azure_storage_account_name": account,
        }

        # Credentials (account key or SAS token)
        if self.config.azure_storage_key:
            config_dict["azure_storage_account_key"] = self.config.azure_storage_key
        elif self.config.azure_sas_token:
            config_dict["azure_storage_sas_key"] = self.config.azure_sas_token

        # Build URL with prefix
        # Azure URL format: az://container or azure://account.blob.core.windows.net/container
        url = f"az://{container}"
        if self.config.storage_prefix:
            prefix = self.config.storage_prefix.strip("/")
            url = f"{url}/{prefix}"

        self.store = AzureStore.from_url(url, config=config_dict)

    def _get_endpoint(self, provider: str) -> str | None:
        """Get endpoint URL for provider."""
        # User-provided endpoint takes precedence
        if self.config.storage_endpoint:
            return self.config.storage_endpoint

        # Provider-specific default endpoints
        if provider == "s3":
            return None  # AWS S3 uses default endpoint

        if provider == "r2":
            # R2 requires account_id in endpoint - user must provide full endpoint
            # Format: https://{account_id}.r2.cloudflarestorage.com
            self.logger.warning(
                "R2 requires OPENSENSOR_STORAGE_ENDPOINT with your account ID. "
                "Format: https://<account_id>.r2.cloudflarestorage.com"
            )
            return None

        if provider == "wasabi":
            region = self.config.storage_region or "us-east-1"
            return f"https://s3.{region}.wasabisys.com"

        if provider == "backblaze":
            region = self.config.storage_region or "us-west-004"
            return f"https://s3.{region}.backblazeb2.com"

        if provider == "hetzner":
            region = self.config.storage_region or "fsn1"
            return f"https://{region}.your-objectstorage.com"

        # MinIO and others - user must provide endpoint
        return None

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
            # obstore.list() returns a stream of batches, each batch is a list of ObjectMeta
            # ObjectMeta: {'path': str, 'size': int, 'last_modified': datetime, 'e_tag': str, ...}
            self.remote_cache = {}
            for batch in self.store.list():
                for obj in batch:
                    self.remote_cache[obj["path"]] = obj

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
