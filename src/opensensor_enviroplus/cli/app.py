"""
Modern CLI for opensensor-enviroplus using Typer.
Replaces bash scripts with simple Python commands.
"""

import sys
from pathlib import Path

import typer
from rich.console import Console

from opensensor_enviroplus.collector.polars_collector import PolarsSensorCollector
from opensensor_enviroplus.config.settings import AppConfig, SensorConfig, StorageConfig
from opensensor_enviroplus.sync.obstore_sync import ObstoreSync
from opensensor_enviroplus.utils.logging import setup_logging
from opensensor_enviroplus.utils.uuid_gen import generate_station_id, validate_station_id

# Create Typer app
app = typer.Typer(
    name="opensensor",
    help="OpenSensor.Space - Environmental sensor data collector for Enviro+",
    add_completion=False,
)

console = Console()


def print_banner():
    """Print opensensor.space branded banner."""
    console.print("\n[bold cyan]OpenSensor.Space[/bold cyan] | Enviro+ Data Collector\n")


@app.command()
def setup(
    station_id: str | None = typer.Option(
        None, help="Station UUID (auto-generated if not provided)"
    ),
    output_dir: Path = typer.Option(Path("output"), help="Data output directory"),
    interactive: bool = typer.Option(True, help="Interactive configuration"),
):
    """
    Setup and configure opensensor-enviroplus.

    This will:
    - Generate station UUID
    - Configure sensor settings
    - Set up cloud sync (optional)
    - Create systemd service (optional)
    """
    print_banner()
    console.print("[bold]Setup Configuration[/bold]\n")

    # Generate or use provided station ID
    if not station_id:
        station_id = generate_station_id()
        console.print(f"Generated station UUID v7: [green]{station_id}[/green]")
        console.print("[dim](Time-ordered UUID for better database performance)[/dim]")
    else:
        # Validate provided station ID
        if not validate_station_id(station_id):
            console.print("[red]ERROR: Invalid UUID format[/red]")
            raise typer.Exit(1)
        console.print(f"Using station UUID: [green]{station_id}[/green]")

    # Create .env file
    env_file = Path(".env")

    config_lines = [
        "# OpenSensor.Space Configuration",
        "# https://opensensor.space",
        "",
        "# Station Configuration",
        f"OPENSENSOR_STATION_ID={station_id}",
        "",
        "# Data Collection",
        "OPENSENSOR_READ_INTERVAL=5",
        "OPENSENSOR_BATCH_DURATION=900",
        "",
        "# Output Settings",
        f"OPENSENSOR_OUTPUT_DIR={output_dir}",
        "OPENSENSOR_COMPRESSION=snappy",
        "",
        "# Logging",
        "OPENSENSOR_LOG_LEVEL=INFO",
    ]

    # Cloud sync configuration (optional)
    enable_sync = False
    if interactive:
        enable_sync = typer.confirm("\nEnable cloud storage sync?", default=False)

    if enable_sync:
        console.print("\n[bold]Cloud Storage Configuration[/bold]")
        bucket = typer.prompt("Bucket name")
        prefix = typer.prompt("Prefix/path in bucket", default="sensor-data")
        region = typer.prompt("Region", default="us-west-2")
        endpoint = typer.prompt("Endpoint URL (optional, for MinIO/custom S3)", default="")
        access_key = typer.prompt("Access Key ID")
        secret_key = typer.prompt("Secret Access Key", hide_input=True)

        config_lines.extend(
            [
                "",
                "# Cloud Sync (uncomment and configure to enable)",
                "OPENSENSOR_SYNC_ENABLED=true",
                "OPENSENSOR_SYNC_INTERVAL_MINUTES=15",
                "",
                "# S3/MinIO Storage Settings",
                f"OPENSENSOR_STORAGE_BUCKET={bucket}",
                f"OPENSENSOR_STORAGE_PREFIX={prefix}",
                f"OPENSENSOR_STORAGE_REGION={region}",
                "",
                "# AWS Credentials (for AWS S3)",
                f"OPENSENSOR_AWS_ACCESS_KEY_ID={access_key}",
                f"OPENSENSOR_AWS_SECRET_ACCESS_KEY={secret_key}",
                "",
                "# MinIO/Custom S3 Endpoint (optional - only needed for non-AWS S3)",
                f"OPENSENSOR_STORAGE_ENDPOINT={endpoint}",
            ]
        )
    else:
        # Add commented sync template for easy enabling later
        config_lines.extend(
            [
                "",
                "# Cloud Sync (uncomment and configure to enable)",
                "# OPENSENSOR_SYNC_ENABLED=true",
                "# OPENSENSOR_SYNC_INTERVAL_MINUTES=15",
                "",
                "# S3/MinIO Storage Settings",
                "# OPENSENSOR_STORAGE_BUCKET=my-sensor-bucket",
                f"# OPENSENSOR_STORAGE_PREFIX=sensors/station-{station_id[:8]}",
                "# OPENSENSOR_STORAGE_REGION=us-west-2",
                "",
                "# AWS Credentials (for AWS S3)",
                "# IMPORTANT: The prefix is used for IAM policy scoping!",
                "# Example IAM policy limits this station to only write to its prefix:",
                f'# "Resource": "arn:aws:s3:::my-sensor-bucket/sensors/station-{station_id[:8]}/*"',
                "# OPENSENSOR_AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
                "# OPENSENSOR_AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                "",
                "# MinIO/Custom S3 Endpoint (optional - only needed for non-AWS S3)",
                "# OPENSENSOR_STORAGE_ENDPOINT=https://minio.example.com:9000",
            ]
        )

    # Write .env file
    env_file.write_text("\n".join(config_lines))
    console.print(f"\nConfiguration saved to [green]{env_file}[/green]")

    # Create directories
    output_dir.mkdir(parents=True, exist_ok=True)
    Path("logs").mkdir(parents=True, exist_ok=True)

    console.print("\n[bold green]Setup complete![/bold green]")
    console.print("\nNext steps:")
    console.print("  1. Review config: [cyan]cat .env[/cyan]")
    console.print("  2. Start collector: [cyan]opensensor start[/cyan]")
    console.print("  3. View logs: [cyan]opensensor logs[/cyan]\n")


@app.command()
def start(
    foreground: bool = typer.Option(
        False, "--foreground", "-f", help="Run in foreground (default: background)"
    ),
):
    """
    Start the sensor data collector.
    """
    print_banner()
    console.print("[bold]Starting data collector...[/bold]\n")

    try:
        # Load configuration
        sensor_config = SensorConfig()
        storage_config = StorageConfig()
        app_config = AppConfig()

        # Setup logging
        log_file = app_config.log_dir / "opensensor.log"
        logger = setup_logging(level=app_config.log_level, log_file=log_file)

        # Create collector with auto-sync
        collector = PolarsSensorCollector(
            config=sensor_config, logger=logger, storage_config=storage_config
        )

        # Run collector
        console.print(" Collector started\n")
        console.print(f" Output: [cyan]{sensor_config.output_dir}[/cyan]")
        console.print(f"Logs: [cyan]{log_file}[/cyan]")
        console.print("\nPress Ctrl+C to stop\n")

        collector.run()

    except FileNotFoundError:
        console.print("[red]ERROR: Configuration not found. Run 'opensensor setup' first.[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: Error: {e}[/red]")
        sys.exit(1)


@app.command()
def sync(
    directory: Path | None = typer.Option(None, help="Directory to sync (default: from config)"),
):
    """
    Manually sync data to cloud storage.
    """
    console.print("[bold blue]Syncing to cloud storage...[/bold blue]\n")

    try:
        # Load configuration
        sensor_config = SensorConfig()
        storage_config = StorageConfig()
        app_config = AppConfig()

        if not storage_config.sync_enabled:
            console.print("[yellow]WARNING:  Cloud sync is not enabled in configuration[/yellow]")
            sys.exit(1)

        # Setup logging
        logger = setup_logging(level=app_config.log_level)

        # Create sync client
        sync_client = ObstoreSync(config=storage_config, logger=logger)

        # Sync directory
        sync_dir = directory or sensor_config.output_dir
        files_synced = sync_client.sync_directory(sync_dir)

        if files_synced > 0:
            console.print(f"\n[green] Synced {files_synced} files[/green]")
        else:
            console.print("\n[yellow]No files to sync[/yellow]")

    except Exception as e:
        console.print(f"[red]ERROR: Sync failed: {e}[/red]")
        sys.exit(1)


@app.command()
def config():
    """
    View current configuration.
    """
    env_file = Path(".env")

    if not env_file.exists():
        console.print("[red]ERROR: Configuration not found. Run 'opensensor setup' first.[/red]")
        sys.exit(1)

    console.print("\n[bold blue]Current Configuration[/bold blue]\n")

    # Read and display config
    config_text = env_file.read_text()
    for line in config_text.split("\n"):
        if line.startswith("#") or not line.strip():
            console.print(f"[dim]{line}[/dim]")
        elif "SECRET" in line or "PASSWORD" in line:
            key, _ = line.split("=", 1)
            console.print(f"{key}=[dim]********[/dim]")
        else:
            console.print(line)

    console.print(f"\n[dim]Config file: {env_file.absolute()}[/dim]\n")


@app.command()
def status():
    """
    Check collector status and statistics.
    """
    console.print("\n[bold blue]OpenSensor Status[/bold blue]\n")

    try:
        sensor_config = SensorConfig()

        # Check output directory
        output_dir = sensor_config.output_dir
        if output_dir.exists():
            # Count files
            parquet_files = list(output_dir.rglob("*.parquet"))
            console.print(f"Output directory: [cyan]{output_dir}[/cyan]")
            console.print(f"Parquet files: [green]{len(parquet_files)}[/green]")

            # Calculate total size
            total_size = sum(f.stat().st_size for f in parquet_files)
            size_mb = total_size / (1024 * 1024)
            console.print(f"Total size: [green]{size_mb:.2f} MB[/green]")
        else:
            console.print(f"[yellow]WARNING:  Output directory not found: {output_dir}[/yellow]")

        # Check logs
        log_file = Path("logs/opensensor.log")
        if log_file.exists():
            console.print(f"\nLog file: [cyan]{log_file}[/cyan]")
            console.print(f"Log size: [green]{log_file.stat().st_size / 1024:.1f} KB[/green]")

    except FileNotFoundError:
        console.print("[red]ERROR: Configuration not found. Run 'opensensor setup' first.[/red]")
        sys.exit(1)

    console.print()


@app.command()
def logs(
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
):
    """
    View collector logs.
    """
    log_file = Path("logs/opensensor.log")

    if not log_file.exists():
        console.print(
            "[yellow]WARNING:  No log file found. Collector may not have run yet.[/yellow]"
        )
        sys.exit(1)

    if follow:
        console.print(f"[dim]Following {log_file}... (Ctrl+C to stop)[/dim]\n")
        # Use tail -f equivalent
        import contextlib
        import subprocess

        with contextlib.suppress(KeyboardInterrupt):
            subprocess.run(["tail", "-f", str(log_file)])
    else:
        # Show last N lines
        log_lines = log_file.read_text().split("\n")
        for line in log_lines[-lines:]:
            console.print(line)


@app.command()
def version():
    """
    Show version information.
    """
    print_banner()
    console.print("Version: [green]0.1.0[/green]")
    console.print("Stack: Polars, PyArrow, Delta Lake, obstore")
    console.print("Website: [cyan]https://opensensor.space[/cyan]\n")


if __name__ == "__main__":
    app()
