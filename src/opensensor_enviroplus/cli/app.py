"""
Modern CLI for opensensor-enviroplus using Typer.
Replaces bash scripts with simple Python commands.
"""

import importlib.metadata
import sys
from pathlib import Path

import typer
from rich.console import Console

from opensensor_enviroplus.collector.polars_collector import PolarsSensorCollector
from opensensor_enviroplus.config.settings import AppConfig, SensorConfig, StorageConfig
from opensensor_enviroplus.service.manager import ServiceManager
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
    # Generate or use provided station ID
    if station_id:
        # Validate provided station ID
        if not validate_station_id(station_id):
            console.print("[red]ERROR: Invalid UUID format[/red]")
            raise typer.Exit(1)
        console.print(f"Using provided station UUID: [green]{station_id}[/green]")
    else:
        # Interactive mode: ask user
        if interactive:
            use_existing = typer.confirm("Do you have an existing station UUID?", default=False)
            if use_existing:
                while True:
                    station_id = typer.prompt("Enter your station UUID")
                    if validate_station_id(station_id):
                        break
                    console.print("[red]Invalid UUID format. Please try again.[/red]")
                console.print(f"Using existing station UUID: [green]{station_id}[/green]")
            else:
                station_id = generate_station_id()
                console.print(f"Generated new station UUID v7: [green]{station_id}[/green]")
                console.print("[dim](Time-ordered UUID for better database performance)[/dim]")
        else:
            # Non-interactive: auto-generate
            station_id = generate_station_id()
            console.print(f"Generated station UUID v7: [green]{station_id}[/green]")

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

        # Create required directories
        sensor_config.output_dir.mkdir(parents=True, exist_ok=True)
        app_config.log_dir.mkdir(parents=True, exist_ok=True)

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
        console.print("[yellow]WARNING: Configuration not found.[/yellow]")
        console.print("\n[dim]To configure, either:[/dim]")
        console.print("  1. Run: [cyan]uv run opensensor setup[/cyan]")
        console.print("  2. Create a .env file manually with at minimum:")
        console.print("     [cyan]OPENSENSOR_STATION_ID=<your-uuid-v7>[/cyan]\n")
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

    # Try to get output directory from config, fallback to default
    try:
        sensor_config = SensorConfig()
        output_dir = sensor_config.output_dir
    except Exception:
        output_dir = Path("output")

    # Check output directory
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
        console.print(f"[dim]Output directory: {output_dir} (not created yet)[/dim]")

    # Try to get log path from config, fallback to default
    try:
        app_config = AppConfig()
        log_file = app_config.log_dir / "opensensor.log"
    except Exception:
        log_file = Path("logs/opensensor.log")

    if log_file.exists():
        console.print(f"\nLog file: [cyan]{log_file}[/cyan]")
        console.print(f"Log size: [green]{log_file.stat().st_size / 1024:.1f} KB[/green]")
    else:
        console.print(f"\n[dim]Log file: {log_file} (not created yet)[/dim]")

    # Check if config exists
    env_file = Path(".env")
    if not env_file.exists():
        console.print("\n[yellow]WARNING: No .env configuration found.[/yellow]")
        console.print("[dim]To configure, either:[/dim]")
        console.print("  1. Run: [cyan]uv run opensensor setup[/cyan]")
        console.print("  2. Create a .env file manually with OPENSENSOR_STATION_ID")

    console.print()


@app.command()
def logs(
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
):
    """
    View collector logs.
    """
    # Try to get log path from config, fallback to default
    try:
        app_config = AppConfig()
        log_file = app_config.log_dir / "opensensor.log"
    except Exception:
        log_file = Path("logs/opensensor.log")

    if not log_file.exists():
        # Create log directory if it doesn't exist
        log_file.parent.mkdir(parents=True, exist_ok=True)
        console.print(
            "[yellow]WARNING:  No log file found. Collector may not have run yet.[/yellow]"
        )
        console.print(f"[dim]Expected log location: {log_file}[/dim]")
        console.print("\n[dim]To start the collector, run:[/dim]")
        console.print("  [cyan]uv run opensensor start[/cyan]\n")
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
    try:
        package_version = importlib.metadata.version("opensensor-enviroplus")
    except importlib.metadata.PackageNotFoundError:
        package_version = "unknown"

    console.print(f"Version: [green]{package_version}[/green]")
    console.print("Website: [cyan]https://opensensor.space[/cyan]")
    console.print("A walkthru.earth initiative\n")


# Service management subcommand group
service_app = typer.Typer(
    name="service",
    help="Manage opensensor as a systemd service",
)
app.add_typer(service_app, name="service")


@service_app.command("install")
def service_install():
    """
    Install opensensor as a systemd service.
    Creates the service file at /etc/systemd/system/opensensor.service
    """
    console.print("\n[bold blue]Installing systemd service...[/bold blue]\n")

    try:
        manager = ServiceManager()

        # Show what will be installed
        console.print("[dim]Auto-detected configuration:[/dim]")
        console.print(f"  User: [cyan]{manager.user}[/cyan]")
        console.print(f"  Group: [cyan]{manager.group}[/cyan]")
        console.print(f"  Project: [cyan]{manager.project_root}[/cyan]")
        console.print(f"  Python: [cyan]{manager.python_path}[/cyan]")
        console.print(f"  Venv: [cyan]{manager.venv_path}[/cyan]\n")

        # Install
        manager.install()

        console.print("[green]Service installed successfully![/green]")
        console.print(f"Service file: [cyan]{manager.service_file}[/cyan]\n")
        console.print("[dim]Next steps:[/dim]")
        console.print("  Enable on boot: [cyan]sudo opensensor service enable[/cyan]")
        console.print("  Start service: [cyan]sudo opensensor service start[/cyan]")
        console.print("  Or do both: [cyan]sudo opensensor service setup[/cyan]\n")

    except PermissionError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: Installation failed: {e}[/red]")
        sys.exit(1)


@service_app.command("uninstall")
def service_uninstall():
    """
    Uninstall the systemd service.
    Removes the service file from /etc/systemd/system/
    """
    console.print("\n[bold blue]Uninstalling systemd service...[/bold blue]\n")

    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print("[yellow]Service is not installed[/yellow]")
            sys.exit(0)

        # Stop if running
        if manager.is_active():
            console.print("Stopping service...")
            manager.stop()

        # Disable if enabled
        if manager.is_enabled():
            console.print("Disabling service...")
            manager.disable()

        # Uninstall
        manager.uninstall()

        console.print("[green]Service uninstalled successfully![/green]\n")

    except PermissionError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: Uninstallation failed: {e}[/red]")
        sys.exit(1)


@service_app.command("enable")
def service_enable():
    """
    Enable the service to start automatically on boot.
    """
    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print(
                "[red]ERROR: Service is not installed. Run 'sudo opensensor service install' first.[/red]"
            )
            sys.exit(1)

        manager.enable()
        console.print("[green]Service enabled successfully![/green]")
        console.print("[dim]The service will now start automatically on boot[/dim]\n")

    except PermissionError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@service_app.command("disable")
def service_disable():
    """
    Disable the service from starting automatically on boot.
    """
    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print("[yellow]Service is not installed[/yellow]")
            sys.exit(0)

        manager.disable()
        console.print("[green]Service disabled successfully![/green]")
        console.print("[dim]The service will no longer start automatically on boot[/dim]\n")

    except PermissionError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@service_app.command("start")
def service_start():
    """
    Start the opensensor service.
    """
    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print(
                "[red]ERROR: Service is not installed. Run 'sudo opensensor service install' first.[/red]"
            )
            sys.exit(1)

        manager.start()
        console.print("[green]Service started successfully![/green]\n")

    except PermissionError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@service_app.command("stop")
def service_stop():
    """
    Stop the opensensor service.
    """
    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print("[yellow]Service is not installed[/yellow]")
            sys.exit(0)

        manager.stop()
        console.print("[green]Service stopped successfully![/green]\n")

    except PermissionError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@service_app.command("restart")
def service_restart():
    """
    Restart the opensensor service.
    """
    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print(
                "[red]ERROR: Service is not installed. Run 'sudo opensensor service install' first.[/red]"
            )
            sys.exit(1)

        manager.restart()
        console.print("[green]Service restarted successfully![/green]\n")

    except PermissionError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@service_app.command("status")
def service_status():
    """
    Show the current status of the opensensor service.
    """
    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print("[yellow]Service is not installed[/yellow]")
            console.print(
                "\nRun [cyan]sudo opensensor service install[/cyan] to install the service\n"
            )
            sys.exit(0)

        # Get status
        status_output, is_active = manager.status()

        console.print("\n[bold blue]Service Status[/bold blue]\n")

        # Show status indicator
        if is_active:
            console.print(" [green]ACTIVE[/green] - Service is running")
        else:
            console.print(" [red]INACTIVE[/red] - Service is stopped")

        console.print(f"Enabled: [cyan]{'Yes' if manager.is_enabled() else 'No'}[/cyan]")
        console.print(f"Installed: [cyan]{'Yes' if manager.is_installed() else 'No'}[/cyan]\n")

        # Show systemctl status output
        console.print("[dim]Detailed status:[/dim]")
        console.print(status_output)

    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@service_app.command("logs")
def service_logs(
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
):
    """
    View service logs from journalctl.
    """
    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print("[yellow]Service is not installed[/yellow]")
            sys.exit(0)

        if follow:
            console.print("[dim]Following service logs... (Ctrl+C to stop)[/dim]\n")

        manager.get_logs(lines=lines, follow=follow)

    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@service_app.command("info")
def service_info():
    """
    Show service configuration and auto-detected paths.
    """
    try:
        manager = ServiceManager()
        info = manager.get_info()

        console.print("\n[bold blue]Service Configuration[/bold blue]\n")

        console.print("[bold]Auto-detected paths:[/bold]")
        console.print(f"  User: [cyan]{info['user']}[/cyan]")
        console.print(f"  Group: [cyan]{info['group']}[/cyan]")
        console.print(f"  Project root: [cyan]{info['project_root']}[/cyan]")
        console.print(f"  Virtual env: [cyan]{info['venv_path']}[/cyan]")
        console.print(f"  Python: [cyan]{info['python_path']}[/cyan]")
        console.print(f"  Config file: [cyan]{info['env_file']}[/cyan]")

        console.print("\n[bold]Service status:[/bold]")
        console.print(f"  Service name: [cyan]{info['service_name']}[/cyan]")
        console.print(f"  Service file: [cyan]{info['service_file']}[/cyan]")
        console.print(f"  Installed: [cyan]{'Yes' if info['installed'] else 'No'}[/cyan]")

        if info["installed"]:
            console.print(f"  Enabled: [cyan]{'Yes' if info['enabled'] else 'No'}[/cyan]")
            console.print(f"  Active: [cyan]{'Yes' if info['active'] else 'No'}[/cyan]")

        console.print()

    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@service_app.command("setup")
def service_setup():
    """
    Quick setup: install + enable + start the service.
    Convenience command that combines install, enable, and start.
    """
    console.print("\n[bold blue]Setting up opensensor service...[/bold blue]\n")

    try:
        manager = ServiceManager()

        # Install
        console.print("Step 1/3: Installing service...")
        console.print(f"  User: [cyan]{manager.user}[/cyan]")
        console.print(f"  Project: [cyan]{manager.project_root}[/cyan]")
        manager.install()
        console.print("[green]Installed![/green]\n")

        # Enable
        console.print("Step 2/3: Enabling service...")
        manager.enable()
        console.print("[green]Enabled![/green]\n")

        # Start
        console.print("Step 3/3: Starting service...")
        manager.start()
        console.print("[green]Started![/green]\n")

        console.print("[bold green]Service setup complete![/bold green]\n")
        console.print("The opensensor service is now:")
        console.print(" Running in the background")
        console.print(" Enabled to start on boot")
        console.print(" Collecting sensor data\n")

        console.print("Useful commands:")
        console.print("  View status: [cyan]sudo opensensor service status[/cyan]")
        console.print("  View logs: [cyan]sudo opensensor service logs -f[/cyan]")
        console.print("  Stop service: [cyan]sudo opensensor service stop[/cyan]\n")

    except PermissionError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: Setup failed: {e}[/red]")
        sys.exit(1)


@service_app.command("remove")
def service_remove():
    """
    Complete removal: stop + disable + uninstall the service.
    Convenience command that completely removes the service.
    """
    console.print("\n[bold blue]Removing opensensor service...[/bold blue]\n")

    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print("[yellow]Service is not installed[/yellow]")
            sys.exit(0)

        # Stop
        if manager.is_active():
            console.print("Step 1/3: Stopping service...")
            manager.stop()
            console.print("[green]Stopped![/green]\n")
        else:
            console.print("Step 1/3: Service already stopped\n")

        # Disable
        if manager.is_enabled():
            console.print("Step 2/3: Disabling service...")
            manager.disable()
            console.print("[green]Disabled![/green]\n")
        else:
            console.print("Step 2/3: Service already disabled\n")

        # Uninstall
        console.print("Step 3/3: Uninstalling service...")
        manager.uninstall()
        console.print("[green]Uninstalled![/green]\n")

        console.print("[bold green]Service removed completely![/bold green]\n")

    except PermissionError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: Removal failed: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    app()
