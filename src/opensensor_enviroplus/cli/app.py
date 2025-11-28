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
from opensensor_enviroplus.utils.env import (
    ensure_directories,
    parse_env_file,
    write_env_file,
)
from opensensor_enviroplus.utils.logging import setup_logging
from opensensor_enviroplus.utils.uuid_gen import generate_station_id, validate_station_id

# Create Typer app with rich markup support
app = typer.Typer(
    name="opensensor",
    help="OpenSensor.Space - Environmental sensor data collector for Enviro+",
    add_completion=False,
    rich_markup_mode="rich",
)

console = Console()


def print_banner():
    """Print opensensor.space branded banner."""
    console.print("\n[bold cyan]OpenSensor.Space[/bold cyan] | Enviro+ Data Collector\n")


@app.command()
def setup(
    station_id: str | None = typer.Option(
        None, "--station-id", "-s", help="Station UUID (auto-generated if not provided)"
    ),
    output_dir: Path | None = typer.Option(
        None, "--output-dir", "-o", help="Data output directory"
    ),
    interactive: bool = typer.Option(
        True, "--interactive/--no-interactive", "-i", help="Interactive configuration"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing configuration"),
):
    """
    Setup and configure opensensor-enviroplus.

    If a .env file already exists, it will be preserved and only missing
    values will be added. Use --force to overwrite completely.
    """
    print_banner()
    console.print("[bold]Setup Configuration[/bold]\n")

    env_file = Path(".env")
    existing_config = parse_env_file(env_file)

    # Handle existing configuration
    if existing_config and not force:
        console.print(f"[yellow]Found existing configuration:[/yellow] {env_file.absolute()}")
        existing_station = existing_config.get("OPENSENSOR_STATION_ID")
        if existing_station:
            console.print(f"  Station ID: [cyan]{existing_station}[/cyan]")

        if interactive:
            action = typer.prompt(
                "\nWhat would you like to do?",
                type=typer.Choice(["keep", "update", "replace"]),
                default="keep",
                show_choices=True,
            )
            if action == "keep":
                console.print("\n[green]Keeping existing configuration.[/green]")
                ensure_directories(existing_config.get("OPENSENSOR_OUTPUT_DIR", "output"), "logs")
                console.print("\nRun [cyan]opensensor config[/cyan] to view current settings.\n")
                return
            elif action == "replace":
                existing_config = {}  # Start fresh
                console.print("\n[yellow]Starting fresh configuration...[/yellow]\n")
        else:
            # Non-interactive: keep existing and just ensure directories
            console.print("[dim]Non-interactive mode: keeping existing configuration[/dim]")
            ensure_directories(existing_config.get("OPENSENSOR_OUTPUT_DIR", "output"), "logs")
            return

    # Determine station ID
    final_station_id: str
    if station_id:
        if not validate_station_id(station_id):
            console.print("[red]ERROR: Invalid UUID format[/red]")
            raise typer.Exit(1)
        final_station_id = station_id
        console.print(f"Using provided station UUID: [green]{final_station_id}[/green]")
    elif existing_config.get("OPENSENSOR_STATION_ID"):
        final_station_id = existing_config["OPENSENSOR_STATION_ID"]
        console.print(f"Using existing station UUID: [green]{final_station_id}[/green]")
    elif interactive:
        use_existing = typer.confirm("Do you have an existing station UUID?", default=False)
        if use_existing:
            while True:
                final_station_id = typer.prompt("Enter your station UUID")
                if validate_station_id(final_station_id):
                    break
                console.print("[red]Invalid UUID format. Please try again.[/red]")
            console.print(f"Using station UUID: [green]{final_station_id}[/green]")
        else:
            final_station_id = generate_station_id()
            console.print(f"Generated new station UUID v7: [green]{final_station_id}[/green]")
            console.print("[dim](Time-ordered UUID for better database performance)[/dim]")
    else:
        final_station_id = generate_station_id()
        console.print(f"Generated station UUID v7: [green]{final_station_id}[/green]")

    # Update config with station ID
    existing_config["OPENSENSOR_STATION_ID"] = final_station_id

    # Handle output directory
    if output_dir:
        existing_config["OPENSENSOR_OUTPUT_DIR"] = str(output_dir)
    elif "OPENSENSOR_OUTPUT_DIR" not in existing_config:
        existing_config["OPENSENSOR_OUTPUT_DIR"] = "output"

    # Cloud sync configuration (only in interactive mode if not already configured)
    if (
        interactive
        and not existing_config.get("OPENSENSOR_SYNC_ENABLED")
        and typer.confirm("\nEnable cloud storage sync?", default=False)
    ):
        console.print("\n[bold]Cloud Storage Configuration[/bold]")
        existing_config["OPENSENSOR_SYNC_ENABLED"] = "true"
        existing_config["OPENSENSOR_STORAGE_BUCKET"] = typer.prompt("Bucket name")
        existing_config["OPENSENSOR_STORAGE_PREFIX"] = typer.prompt(
            "Prefix/path in bucket", default="sensor-data"
        )
        existing_config["OPENSENSOR_STORAGE_REGION"] = typer.prompt("Region", default="us-west-2")
        endpoint = typer.prompt("Endpoint URL (optional, for MinIO)", default="")
        if endpoint:
            existing_config["OPENSENSOR_STORAGE_ENDPOINT"] = endpoint
        existing_config["OPENSENSOR_AWS_ACCESS_KEY_ID"] = typer.prompt("Access Key ID")
        existing_config["OPENSENSOR_AWS_SECRET_ACCESS_KEY"] = typer.prompt(
            "Secret Access Key", hide_input=True
        )

    # Write configuration
    write_env_file(env_file, existing_config, final_station_id)
    console.print(f"\nConfiguration saved to [green]{env_file}[/green]")

    # Create directories
    ensure_directories(existing_config.get("OPENSENSOR_OUTPUT_DIR", "output"), "logs")

    console.print("\n[bold green]Setup complete![/bold green]")
    console.print("\nNext steps:")
    console.print("  1. Review config: [cyan]opensensor config[/cyan]")
    console.print("  2. Start collector: [cyan]opensensor start[/cyan]")
    console.print("  3. Or setup service: [cyan]opensensor service setup[/cyan]\n")


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


@app.command("fix-permissions")
def fix_permissions():
    """
    Fix serial port permissions for PMS5003 sensor.

    This command requires sudo and will:
    - Add current user to dialout, i2c, and gpio groups
    - Create udev rule for /dev/ttyAMA0 serial port access
    - Reload udev rules

    A reboot is required after running this command.
    """
    import grp
    import os
    import subprocess

    print_banner()
    console.print("[bold]Fixing sensor permissions...[/bold]\n")

    # Check if running as root
    if os.geteuid() != 0:
        console.print("[red]ERROR: This command requires sudo.[/red]")
        console.print("\nRun: [cyan]sudo $(which opensensor) fix-permissions[/cyan]\n")
        sys.exit(1)

    # Get the actual user (not root)
    user = os.environ.get("SUDO_USER") or os.environ.get("USER")
    if not user or user == "root":
        console.print("[red]ERROR: Could not determine actual user.[/red]")
        console.print("Please run with sudo from a regular user account.\n")
        sys.exit(1)

    console.print(f"User: [cyan]{user}[/cyan]\n")

    # Add user to required groups
    groups = ["dialout", "i2c", "gpio"]
    for group in groups:
        try:
            # Check if group exists
            grp.getgrnam(group)
            result = subprocess.run(
                ["usermod", "-aG", group, user],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                console.print(f"[green]Added {user} to {group} group[/green]")
            else:
                console.print(
                    f"[yellow]Warning: Could not add to {group}: {result.stderr}[/yellow]"
                )
        except KeyError:
            console.print(f"[dim]Skipping {group} (group does not exist)[/dim]")

    # Create udev rule for PMS5003 serial port
    udev_rule = 'KERNEL=="ttyAMA0", GROUP="dialout", MODE="0660"'
    udev_file = Path("/etc/udev/rules.d/99-pms5003.rules")

    try:
        udev_file.write_text(udev_rule + "\n")
        console.print(f"\n[green]Created udev rule:[/green] {udev_file}")
        console.print(f"  [dim]{udev_rule}[/dim]")
    except OSError as e:
        console.print(f"[red]ERROR: Could not create udev rule: {e}[/red]")
        sys.exit(1)

    # Reload udev rules
    console.print("\n[dim]Reloading udev rules...[/dim]")
    result = subprocess.run(
        ["udevadm", "control", "--reload-rules"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        console.print("[green]Udev rules reloaded[/green]")
    else:
        console.print(f"[yellow]Warning: {result.stderr}[/yellow]")

    # Trigger udev for immediate effect (if device exists)
    subprocess.run(
        ["udevadm", "trigger", "--subsystem-match=tty"],
        capture_output=True,
    )

    console.print("\n[bold green]Permissions fixed![/bold green]")
    console.print("\n[yellow]IMPORTANT: You must reboot for group changes to take effect.[/yellow]")
    console.print("\nRun: [cyan]sudo reboot[/cyan]\n")


@app.command("test-sensors")
def test_sensors(
    warmup: int = typer.Option(3, "--warmup", "-w", help="Warm-up time in seconds before reading"),
    readings: int = typer.Option(3, "--readings", "-r", help="Number of readings to take"),
    interval: float = typer.Option(
        2.0, "--interval", "-i", help="Interval between readings in seconds"
    ),
):
    """
    Test sensors with warm-up delay and display readings in a table.

    Initializes all sensors, waits for warm-up, then displays
    a formatted table with sensor readings.
    """
    import time

    from rich.table import Table

    print_banner()
    console.print("[bold]Testing Sensors[/bold]\n")

    # Try to import sensor libraries
    try:
        import ads1015
        import gpiod
        import gpiodevice
        from bme280 import BME280
        from gpiod.line import Direction, Value
        from ltr559 import LTR559
        from pms5003 import PMS5003, ReadTimeoutError
        from smbus2 import SMBus
    except ImportError as e:
        console.print(f"[red]ERROR: Sensor libraries not available: {e}[/red]")
        console.print(
            "\n[dim]This command must be run on a Raspberry Pi with sensors connected.[/dim]\n"
        )
        sys.exit(1)

    # Constants for gas sensor
    MICS6814_GAIN = 6.144
    MICS6814_I2C_ADDR = 0x49

    def voltage_to_resistance(voltage: float) -> float:
        try:
            return (voltage * 56000) / (3.3 - voltage)
        except ZeroDivisionError:
            return 0.0

    # Initialize sensors
    sensors_status = {}

    # BME280
    bme280 = None
    try:
        bme280 = BME280(i2c_dev=SMBus(1))
        sensors_status["BME280"] = "[green]OK[/green]"
    except Exception as e:
        sensors_status["BME280"] = f"[red]Failed: {e}[/red]"

    # Gas sensor (ADS1015/ADS1115)
    gas_adc = None
    adc_type = "N/A"
    try:
        ads1015.I2C_ADDRESS_DEFAULT = MICS6814_I2C_ADDR
        gas_adc = ads1015.ADS1015(i2c_addr=MICS6814_I2C_ADDR)
        adc_type = gas_adc.detect_chip_type()
        gas_adc.set_mode("single")
        gas_adc.set_programmable_gain(MICS6814_GAIN)
        if adc_type == "ADS1115":
            gas_adc.set_sample_rate(128)
        else:
            gas_adc.set_sample_rate(1600)
        # Enable heater
        outh = gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE)
        gpiodevice.get_pin("GPIO24", "EnviroPlus", outh)
        sensors_status["MICS6814"] = f"[green]OK ({adc_type})[/green]"
    except Exception as e:
        sensors_status["MICS6814"] = f"[red]Failed: {e}[/red]"

    # LTR559
    ltr559 = None
    try:
        ltr559 = LTR559()
        sensors_status["LTR559"] = "[green]OK[/green]"
    except Exception as e:
        sensors_status["LTR559"] = f"[red]Failed: {e}[/red]"

    # PMS5003
    pms5003 = None
    try:
        pms5003 = PMS5003()
        sensors_status["PMS5003"] = "[green]OK[/green]"
    except Exception as e:
        sensors_status["PMS5003"] = f"[red]Failed: {e}[/red]"

    # Show initialization status
    console.print("[bold]Sensor Initialization:[/bold]")
    for sensor, status in sensors_status.items():
        console.print(f"  {sensor}: {status}")

    # Check if any sensors available
    if not any([bme280, gas_adc, ltr559, pms5003]):
        console.print("\n[red]ERROR: No sensors could be initialized.[/red]")
        console.print("[dim]Check I2C/SPI interfaces and wiring.[/dim]\n")
        sys.exit(1)

    # Warm-up countdown
    console.print(f"\n[yellow]Warming up sensors ({warmup}s)...[/yellow]")
    for i in range(warmup, 0, -1):
        console.print(f"  [dim]{i}...[/dim]", end="\r")
        time.sleep(1)
    console.print("  [green]Ready![/green]   ")

    # Take readings
    console.print(f"\n[bold]Taking {readings} readings (every {interval}s):[/bold]\n")

    all_readings = []

    for reading_num in range(1, readings + 1):
        reading = {"#": reading_num}

        # BME280 readings
        if bme280:
            try:
                reading["Temp (°C)"] = f"{bme280.get_temperature():.1f}"
                reading["Humidity (%)"] = f"{bme280.get_humidity():.1f}"
                reading["Pressure (hPa)"] = f"{bme280.get_pressure():.1f}"
            except Exception:
                reading["Temp (°C)"] = "ERR"
                reading["Humidity (%)"] = "ERR"
                reading["Pressure (hPa)"] = "ERR"

        # Gas sensor readings
        if gas_adc:
            try:
                ox = gas_adc.get_voltage("in0/gnd")
                red = gas_adc.get_voltage("in1/gnd")
                nh3 = gas_adc.get_voltage("in2/gnd")
                reading["Oxidising (kΩ)"] = f"{voltage_to_resistance(ox) / 1000:.1f}"
                reading["Reducing (kΩ)"] = f"{voltage_to_resistance(red) / 1000:.1f}"
                reading["NH3 (kΩ)"] = f"{voltage_to_resistance(nh3) / 1000:.1f}"
            except Exception:
                reading["Oxidising (kΩ)"] = "ERR"
                reading["Reducing (kΩ)"] = "ERR"
                reading["NH3 (kΩ)"] = "ERR"

        # Light sensor readings
        if ltr559:
            try:
                reading["Lux"] = f"{ltr559.get_lux():.1f}"
                reading["Proximity"] = f"{ltr559.get_proximity()}"
            except Exception:
                reading["Lux"] = "ERR"
                reading["Proximity"] = "ERR"

        # Particulate sensor readings
        if pms5003:
            try:
                pm = pms5003.read()
                reading["PM1.0"] = f"{pm.pm_ug_per_m3(1.0)}"
                reading["PM2.5"] = f"{pm.pm_ug_per_m3(2.5)}"
                reading["PM10"] = f"{pm.pm_ug_per_m3(10)}"
            except ReadTimeoutError:
                reading["PM1.0"] = "TIMEOUT"
                reading["PM2.5"] = "TIMEOUT"
                reading["PM10"] = "TIMEOUT"
            except Exception:
                reading["PM1.0"] = "ERR"
                reading["PM2.5"] = "ERR"
                reading["PM10"] = "ERR"

        all_readings.append(reading)

        # Build and display table
        table = Table(title=f"Sensor Readings ({reading_num}/{readings})")

        # Add columns based on what sensors we have
        if all_readings:
            for col in all_readings[0]:
                table.add_column(col, justify="right" if col != "#" else "center")

            for r in all_readings:
                table.add_row(*[str(v) for v in r.values()])

        console.print(table)

        if reading_num < readings:
            time.sleep(interval)
            # Clear previous table for next iteration (move cursor up)
            # This creates a nice "live" effect
            console.print()

    console.print("\n[bold green]Test complete![/bold green]")
    console.print(
        "\nTo start continuous collection, run: [cyan]opensensor start --foreground[/cyan]\n"
    )


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

        # User & System
        console.print("[bold]User & System:[/bold]")
        console.print(f"  User: [cyan]{info['user']}[/cyan]")
        console.print(f"  Group: [cyan]{info['group']}[/cyan]")
        console.print(f"  Home: [cyan]{info['home']}[/cyan]")

        # Python Environment
        console.print("\n[bold]Python Environment:[/bold]")
        console.print(f"  Python: [cyan]{info['python_executable']}[/cyan]")
        console.print(f"  Virtual env: [cyan]{info['virtual_env'] or 'None'}[/cyan]")
        console.print(f"  Is venv: [cyan]{'Yes' if info['is_venv'] else 'No'}[/cyan]")
        console.print(f"  Installation type: [cyan]{info['installation_type']}[/cyan]")

        # CLI Executable
        console.print("\n[bold]CLI Executable:[/bold]")
        if info["cli_executable"]:
            exists_color = "green" if info["cli_exists"] else "red"
            exists_text = "Yes" if info["cli_exists"] else "NO - MISSING!"
            console.print(f"  Path: [cyan]{info['cli_executable']}[/cyan]")
            console.print(f"  Exists: [{exists_color}]{exists_text}[/{exists_color}]")
            console.print(f"  Found via: [dim]{info['cli_discovery_method']}[/dim]")
        else:
            console.print("  [red]NOT FOUND[/red]")
            console.print("  [dim]Searched: PATH, uv tool dir, VIRTUAL_ENV, XDG_BIN_HOME[/dim]")

        # Working Directory & Config
        console.print("\n[bold]Configuration:[/bold]")
        console.print(f"  Working directory: [cyan]{info['working_directory']}[/cyan]")
        if info["env_file"]:
            env_color = "green" if info["env_file_exists"] else "yellow"
            env_status = "" if info["env_file_exists"] else " (not created yet)"
            console.print(
                f"  Config file: [{env_color}]{info['env_file']}{env_status}[/{env_color}]"
            )
        else:
            console.print("  Config file: [yellow]None[/yellow]")

        # Service Status
        console.print("\n[bold]Service Status:[/bold]")
        console.print(f"  Service name: [cyan]{info['service_name']}[/cyan]")
        console.print(f"  Service file: [cyan]{info['service_file']}[/cyan]")
        console.print(f"  Installed: [cyan]{'Yes' if info['installed'] else 'No'}[/cyan]")

        if info["installed"]:
            console.print(f"  Enabled: [cyan]{'Yes' if info['enabled'] else 'No'}[/cyan]")
            active_color = "green" if info["active"] else "yellow"
            console.print(
                f"  Active: [{active_color}]{'Yes' if info['active'] else 'No'}[/{active_color}]"
            )

        # PATH (collapsed)
        console.print("\n[bold]PATH for service:[/bold]")
        console.print(f"  [dim]{info['path_env'][:80]}...[/dim]")

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
