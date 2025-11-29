"""
Modern CLI for opensensor-enviroplus using Typer.
Replaces bash scripts with simple Python commands.

Commands:
  setup           Initial configuration
  start           Start collector (foreground, for debugging)
  test            Test sensors with live table
  info            Show config, sensors, and data stats
  sync            Manual cloud sync
  fix-permissions Fix sensor permissions (requires sudo)
  service         Manage systemd service (setup, status, logs, etc.)
"""

import importlib.metadata
import sys
import time
from pathlib import Path
from uuid import UUID

import click
import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from opensensor_enviroplus.collector.polars_collector import PolarsSensorCollector
from opensensor_enviroplus.config.settings import AppConfig, SensorConfig, StorageConfig
from opensensor_enviroplus.service.manager import ServiceManager
from opensensor_enviroplus.sync.obstore_sync import ObstoreSync
from opensensor_enviroplus.utils.compensation import (
    compensate_humidity,
    compensate_temperature,
    get_cpu_temperature,
)
from opensensor_enviroplus.utils.env import (
    ensure_directories,
    parse_env_file,
    write_env_file,
)
from opensensor_enviroplus.utils.health import collect_health_metrics, health_to_dict
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
    console.print("\n[bold cyan]OpenSensor.Space[/bold cyan] | Enviro+ Data Collector")
    console.print("[dim]A walkthru.earth Initiative[/dim]\n")


def _check_sensor_availability() -> dict[str, str]:
    """
    Check which sensors are available and return their status.
    Returns a dict mapping sensor name to status string (with Rich markup).
    """
    sensors_status = {}

    # Try to import sensor libraries
    try:
        import ads1015
        import gpiod
        import gpiodevice
        from bme280 import BME280
        from gpiod.line import Direction, Value
        from ltr559 import LTR559
        from pms5003 import PMS5003
        from smbus2 import SMBus
    except ImportError:
        return {
            "BME280": "[yellow]N/A[/yellow] (not on Pi)",
            "MICS6814": "[yellow]N/A[/yellow] (not on Pi)",
            "LTR559": "[yellow]N/A[/yellow] (not on Pi)",
            "PMS5003": "[yellow]N/A[/yellow] (not on Pi)",
        }

    # Constants for gas sensor
    MICS6814_GAIN = 6.144
    MICS6814_I2C_ADDR = 0x49

    # BME280
    try:
        BME280(i2c_dev=SMBus(1))
        sensors_status["BME280"] = "[green]OK[/green]"
    except Exception as e:
        sensors_status["BME280"] = f"[red]FAIL[/red] ({e})"

    # Gas sensor (ADS1015/ADS1115)
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
        sensors_status["MICS6814"] = f"[green]OK[/green] ({adc_type})"
    except Exception as e:
        sensors_status["MICS6814"] = f"[red]FAIL[/red] ({e})"

    # LTR559
    try:
        LTR559()
        sensors_status["LTR559"] = "[green]OK[/green]"
    except Exception as e:
        sensors_status["LTR559"] = f"[red]FAIL[/red] ({e})"

    # PMS5003
    try:
        # Load config to get device path
        try:
            config = SensorConfig()
        except ValidationError:
            # Fallback for "on the fly" testing without setup
            config = SensorConfig(station_id=UUID(int=0))

        PMS5003(device=config.pms5003_device)
        sensors_status["PMS5003"] = f"[green]OK[/green] ({config.pms5003_device})"
    except Exception as e:
        sensors_status["PMS5003"] = f"[red]FAIL[/red] ({e})"

    return sensors_status


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

    Creates a .env configuration file with station ID and settings.
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
                console.print("\nRun [cyan]opensensor info[/cyan] to view current settings.\n")
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

    # Cloud storage configuration
    enable_sync = False
    if interactive:
        enable_sync = typer.confirm("\nEnable cloud storage sync?", default=False)
    elif existing_config.get("OPENSENSOR_SYNC_ENABLED") == "true":
        enable_sync = True

    # Health monitoring configuration
    enable_health = False
    if interactive:
        enable_health = typer.confirm(
            "Enable system health monitoring (CPU, RAM, WiFi)?", default=True
        )
    elif existing_config.get("OPENSENSOR_HEALTH_ENABLED") == "true":
        enable_health = True

    config = {
        "OPENSENSOR_STATION_ID": str(final_station_id),
        "OPENSENSOR_OUTPUT_DIR": str(output_dir)
        if output_dir
        else existing_config.get("OPENSENSOR_OUTPUT_DIR", "output"),
        "OPENSENSOR_HEALTH_ENABLED": str(enable_health).lower(),
        "OPENSENSOR_SYNC_ENABLED": str(enable_sync).lower(),
    }

    if enable_sync:
        console.print("\n[bold]Cloud Storage Configuration[/bold]")

        # Provider selection
        console.print("\n[dim]Supported providers:[/dim]")
        console.print("  s3       - AWS S3")
        console.print("  r2       - Cloudflare R2 (no egress fees)")
        console.print("  gcs      - Google Cloud Storage")
        console.print("  azure    - Azure Blob Storage")
        console.print("  minio    - MinIO (self-hosted)")
        console.print("  wasabi   - Wasabi")
        console.print("  backblaze - Backblaze B2")
        console.print("  hetzner  - Hetzner Object Storage")

        provider = typer.prompt(
            "\nStorage provider",
            default=existing_config.get("OPENSENSOR_STORAGE_PROVIDER", "s3"),
            type=click.Choice(
                ["s3", "r2", "gcs", "azure", "minio", "wasabi", "backblaze", "hetzner"],
                case_sensitive=False,
            ),
        ).lower()
        config["OPENSENSOR_STORAGE_PROVIDER"] = provider

        # Common settings
        config["OPENSENSOR_STORAGE_BUCKET"] = typer.prompt(
            "Bucket/container name", default=existing_config.get("OPENSENSOR_STORAGE_BUCKET", "")
        )
        config["OPENSENSOR_STORAGE_PREFIX"] = typer.prompt(
            "Prefix/path in bucket",
            default=existing_config.get("OPENSENSOR_STORAGE_PREFIX", "sensor-data"),
        )

        # Provider-specific configuration
        if provider == "gcs":
            # Google Cloud Storage
            sa_path = typer.prompt(
                "Service account JSON path (or press Enter for ADC)",
                default=existing_config.get("OPENSENSOR_GCS_SERVICE_ACCOUNT_PATH", ""),
            )
            if sa_path:
                config["OPENSENSOR_GCS_SERVICE_ACCOUNT_PATH"] = sa_path
            else:
                console.print("[dim]Using Application Default Credentials[/dim]")

        elif provider == "azure":
            # Azure Blob Storage
            config["OPENSENSOR_AZURE_STORAGE_ACCOUNT"] = typer.prompt(
                "Storage account name",
                default=existing_config.get("OPENSENSOR_AZURE_STORAGE_ACCOUNT", ""),
            )
            auth_method = typer.prompt(
                "Auth method", type=typer.Choice(["key", "sas"]), default="key"
            )
            if auth_method == "key":
                config["OPENSENSOR_AZURE_STORAGE_KEY"] = typer.prompt(
                    "Storage account key",
                    hide_input=True,
                    default=existing_config.get("OPENSENSOR_AZURE_STORAGE_KEY", ""),
                )
            else:
                config["OPENSENSOR_AZURE_SAS_TOKEN"] = typer.prompt(
                    "SAS token",
                    hide_input=True,
                    default=existing_config.get("OPENSENSOR_AZURE_SAS_TOKEN", ""),
                )

        else:
            # S3-compatible providers (s3, r2, minio, wasabi, backblaze, hetzner)
            if provider in ("wasabi", "backblaze", "hetzner"):
                config["OPENSENSOR_STORAGE_REGION"] = typer.prompt(
                    "Region",
                    default=existing_config.get(
                        "OPENSENSOR_STORAGE_REGION",
                        "us-east-1" if provider == "wasabi" else "us-west-004",
                    ),
                )
            elif provider == "s3":
                config["OPENSENSOR_STORAGE_REGION"] = typer.prompt(
                    "Region", default=existing_config.get("OPENSENSOR_STORAGE_REGION", "us-west-2")
                )
            elif provider == "r2":
                config["OPENSENSOR_STORAGE_REGION"] = typer.prompt(
                    "Region", default=existing_config.get("OPENSENSOR_STORAGE_REGION", "auto")
                )

            # Endpoint (required for r2, minio; optional for others)
            if provider == "r2":
                console.print(
                    "\n[yellow]R2 endpoint format:[/yellow] "
                    "https://<account_id>.r2.cloudflarestorage.com"
                )
                config["OPENSENSOR_STORAGE_ENDPOINT"] = typer.prompt(
                    "R2 endpoint URL",
                    default=existing_config.get("OPENSENSOR_STORAGE_ENDPOINT", ""),
                )
            elif provider == "minio":
                config["OPENSENSOR_STORAGE_ENDPOINT"] = typer.prompt(
                    "MinIO endpoint URL",
                    default=existing_config.get(
                        "OPENSENSOR_STORAGE_ENDPOINT", "http://localhost:9000"
                    ),
                )
            elif existing_config.get("OPENSENSOR_STORAGE_ENDPOINT"):
                config["OPENSENSOR_STORAGE_ENDPOINT"] = typer.prompt(
                    "Endpoint URL (optional)",
                    default=existing_config.get("OPENSENSOR_STORAGE_ENDPOINT", ""),
                )

            # S3-compatible credentials
            config["OPENSENSOR_AWS_ACCESS_KEY_ID"] = typer.prompt(
                "Access Key ID", default=existing_config.get("OPENSENSOR_AWS_ACCESS_KEY_ID", "")
            )
            config["OPENSENSOR_AWS_SECRET_ACCESS_KEY"] = typer.prompt(
                "Secret Access Key",
                hide_input=True,
                default=existing_config.get("OPENSENSOR_AWS_SECRET_ACCESS_KEY", ""),
            )

    # Write configuration
    write_env_file(env_file, config)
    console.print(f"\nConfiguration saved to [green]{env_file}[/green]")

    # Create directories
    out_dir = config.get("OPENSENSOR_OUTPUT_DIR", "output")
    # We need to instantiate SensorConfig to get the computed health_dir
    # But since we just wrote the env file, we can rely on the default behavior
    # or simple string manipulation for now to avoid complex reloading
    health_dir = config.get("OPENSENSOR_HEALTH_DIR")
    if not health_dir:
        health_dir = f"{out_dir}-health"

    ensure_directories(out_dir, health_dir, "logs")

    console.print("\n[bold green]Setup complete![/bold green]")
    console.print("\nNext steps:")
    console.print("  1. Test sensors: [cyan]opensensor test[/cyan]")
    console.print("  2. View info: [cyan]opensensor info[/cyan]")
    console.print("  3. Setup service: [cyan]opensensor service setup[/cyan]\n")


@app.command()
def start(
    foreground: bool = typer.Option(False, "--foreground", help="Run in foreground (default)"),
):
    """
    Start the sensor data collector (foreground).

    Runs in foreground for debugging. For production, use:
    opensensor service setup
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

        # Check and display sensor availability at startup
        console.print("[bold]Sensors:[/bold]")
        sensors_status = _check_sensor_availability()
        for sensor, status in sensors_status.items():
            console.print(f"  {sensor}: {status}")
        console.print()

        # Setup logging
        log_file = app_config.log_dir / "opensensor.log"
        logger = setup_logging(level=app_config.log_level, log_file=log_file)

        # Create collector with auto-sync
        collector = PolarsSensorCollector(
            config=sensor_config, logger=logger, storage_config=storage_config
        )

        # Run collector
        console.print(f"Output: [cyan]{sensor_config.output_dir}[/cyan]")
        console.print(f"Logs: [cyan]{log_file}[/cyan]")
        console.print("\n[dim]Press Ctrl+C to stop[/dim]\n")

        collector.run()

    except FileNotFoundError:
        console.print("[red]ERROR: Configuration not found.[/red]")
        console.print("Run [cyan]opensensor setup[/cyan] first.\n")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped by user[/yellow]\n")
    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@app.command()
def test(
    warmup: int = typer.Option(5, "--warmup", "-w", help="Warm-up time in seconds"),
    readings: int = typer.Option(6, "--readings", "-r", help="Number of readings"),
    interval: float = typer.Option(10.0, "--interval", "-i", help="Interval between readings"),
):
    """
    Test sensors with live readings table.

    Initializes sensors, warms up, then displays live readings.
    """

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
        console.print("[red]ERROR: Sensor libraries not available[/red]")
        console.print(f"[dim]{e}[/dim]")
        console.print("\n[yellow]This command must run on a Raspberry Pi with sensors.[/yellow]\n")
        sys.exit(1)

    # Constants for gas sensor
    MICS6814_GAIN = 6.144
    MICS6814_I2C_ADDR = 0x49

    def voltage_to_resistance(voltage: float) -> float:
        try:
            return (voltage * 56000) / (3.3 - voltage)
        except ZeroDivisionError:
            return 0.0

    # Initialize sensors and build status table
    sensors_table = Table(title="Sensor Status", show_header=True)
    sensors_table.add_column("Sensor", style="cyan")
    sensors_table.add_column("Status")
    sensors_table.add_column("Description", style="dim")

    # BME280
    bme280 = None
    try:
        bme280 = BME280(i2c_dev=SMBus(1))
        sensors_table.add_row("BME280", "[green]OK[/green]", "Temperature, Humidity, Pressure")
    except Exception as e:
        sensors_table.add_row("BME280", "[red]FAIL[/red]", str(e)[:40])

    # Gas sensor (ADS1015/ADS1115)
    gas_adc = None
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
        sensors_table.add_row(
            "MICS6814", f"[green]OK[/green] ({adc_type})", "Gas sensor (Ox, Red, NH3)"
        )
    except Exception as e:
        sensors_table.add_row("MICS6814", "[red]FAIL[/red]", str(e)[:40])

    # LTR559
    ltr559 = None
    try:
        ltr559 = LTR559()
        sensors_table.add_row("LTR559", "[green]OK[/green]", "Light and Proximity")
    except Exception as e:
        sensors_table.add_row("LTR559", "[red]FAIL[/red]", str(e)[:40])

    # PMS5003
    pms5003 = None
    try:
        # Load config to get device path
        try:
            config = SensorConfig()
        except ValidationError:
            # Fallback for "on the fly" testing without setup
            console.print(
                "[yellow]WARNING: No configuration found. Running in temporary test mode.[/yellow]"
            )
            console.print("Run [cyan]opensensor setup[/cyan] to save settings permanently.\n")
            config = SensorConfig(station_id=UUID(int=0))

        pms5003 = PMS5003(device=config.pms5003_device)
        sensors_table.add_row(
            "PMS5003",
            f"[green]OK[/green] ({config.pms5003_device})",
            "Particulate Matter (PM1, PM2.5, PM10)",
        )
    except Exception as e:
        sensors_table.add_row("PMS5003", "[red]FAIL[/red]", str(e)[:40])

    console.print(sensors_table)

    # Check if any sensors available
    if not any([bme280, gas_adc, ltr559, pms5003]):
        console.print("\n[red]ERROR: No sensors initialized.[/red]")
        console.print("[dim]Check I2C/SPI interfaces: sudo raspi-config[/dim]\n")
        sys.exit(1)

    # Warm-up countdown
    console.print(f"\n[yellow]Warming up ({warmup}s)...[/yellow]", end="")
    for i in range(warmup, 0, -1):
        console.print(f" {i}", end="", style="dim")
        time.sleep(1)
    console.print(" [green]Ready![/green]\n")

    # Take readings
    console.print(f"[bold]Taking {readings} readings (every {interval}s):[/bold]\n")

    all_readings = []

    for reading_num in range(1, readings + 1):
        reading = {"#": reading_num}

        # BME280 readings
        if bme280:
            try:
                raw_temp = bme280.get_temperature()
                raw_hum = bme280.get_humidity()

                # Apply compensation
                cpu_temp = get_cpu_temperature()
                comp_temp = compensate_temperature(
                    raw_temp,
                    cpu_temp,
                    config.temp_compensation_factor if config.temp_compensation_enabled else 1.0,
                )

                if config.temp_compensation_enabled:
                    comp_hum = compensate_humidity(raw_hum, raw_temp, comp_temp)
                    reading["Temp °C"] = f"{comp_temp:.1f} (raw: {raw_temp:.1f})"
                    reading["Hum %"] = f"{comp_hum:.1f} (raw: {raw_hum:.1f})"
                else:
                    reading["Temp °C"] = f"{raw_temp:.1f}"
                    reading["Hum %"] = f"{raw_hum:.1f}"

                reading["hPa"] = f"{bme280.get_pressure():.0f}"
            except Exception:
                reading["Temp °C"] = "-"
                reading["Hum %"] = "-"
                reading["hPa"] = "-"

        # Gas sensor readings
        if gas_adc:
            try:
                ox = gas_adc.get_voltage("in0/gnd")
                red = gas_adc.get_voltage("in1/gnd")
                nh3 = gas_adc.get_voltage("in2/gnd")
                reading["Ox kΩ"] = f"{voltage_to_resistance(ox) / 1000:.1f}"
                reading["Red kΩ"] = f"{voltage_to_resistance(red) / 1000:.1f}"
                reading["NH3 kΩ"] = f"{voltage_to_resistance(nh3) / 1000:.1f}"
            except Exception:
                reading["Ox kΩ"] = "-"
                reading["Red kΩ"] = "-"
                reading["NH3 kΩ"] = "-"

        # Light sensor readings
        if ltr559:
            try:
                reading["Lux"] = f"{ltr559.get_lux():.0f}"
                reading["Prox"] = f"{ltr559.get_proximity()}"
            except Exception:
                reading["Lux"] = "-"
                reading["Prox"] = "-"

        # Particulate sensor readings
        if pms5003:
            try:
                pm = pms5003.read()
                reading["PM1"] = f"{pm.pm_ug_per_m3(1.0)}"
                reading["PM2.5"] = f"{pm.pm_ug_per_m3(2.5)}"
                reading["PM10"] = f"{pm.pm_ug_per_m3(10)}"
            except ReadTimeoutError:
                reading["PM1"] = "..."
                reading["PM2.5"] = "..."
                reading["PM10"] = "..."
            except Exception:
                reading["PM1"] = "-"
                reading["PM2.5"] = "-"
                reading["PM10"] = "-"

        all_readings.append(reading)

        # Build and display table
        table = Table(title=f"Readings ({reading_num}/{readings})")

        # Add columns based on what sensors we have
        if all_readings:
            for col in all_readings[0]:
                table.add_column(col, justify="right" if col != "#" else "center")

            for r in all_readings:
                table.add_row(*[str(v) for v in r.values()])

        console.print(table)

        if reading_num < readings:
            time.sleep(interval)
            console.print()

            console.print()

    # Health Metrics Section
    console.print("\n[bold]System Health[/bold]\n")

    try:
        health_metrics = collect_health_metrics()
        health_data = health_to_dict(health_metrics)

        # Filter available vs unavailable
        available = {k: v for k, v in health_data.items() if v is not None}
        unavailable = [k for k, v in health_data.items() if v is None]

        if available:
            health_table = Table(show_header=True, header_style="bold magenta")
            health_table.add_column("Metric")
            health_table.add_column("Value")

            for key, value in available.items():
                health_table.add_row(key, str(value))

            console.print(health_table)

        if unavailable:
            console.print("\n[yellow]Unavailable Metrics:[/yellow]")
            for key in unavailable:
                console.print(f"  - {key}")

    except Exception as e:
        console.print(f"[red]Error collecting health metrics: {e}[/red]")

    console.print("\n[bold green]Test complete![/bold green]")
    console.print("\nNext: [cyan]opensensor service setup[/cyan] for continuous collection\n")


@app.command()
def info():
    """
    Show configuration, sensors, and data statistics.

    Displays station config, sensor status, and collected data info.
    """
    print_banner()

    # Version info
    try:
        package_version = importlib.metadata.version("opensensor-enviroplus")
    except importlib.metadata.PackageNotFoundError:
        package_version = "dev"
    console.print(f"Version: [green]{package_version}[/green]\n")

    # Configuration
    env_file = Path(".env")
    console.print("[bold]Configuration:[/bold]")

    if env_file.exists():
        config = parse_env_file(env_file)
        station_id = config.get("OPENSENSOR_STATION_ID", "Not set")
        output_dir = config.get("OPENSENSOR_OUTPUT_DIR", "output")
        sync_enabled = config.get("OPENSENSOR_SYNC_ENABLED", "false").lower() == "true"
        health_enabled = config.get("OPENSENSOR_HEALTH_ENABLED", "true").lower() == "true"

        console.print(f"  Station ID: [cyan]{station_id}[/cyan]")
        console.print(f"  Output: [cyan]{output_dir}[/cyan]")
        if sync_enabled:
            provider = config.get("OPENSENSOR_STORAGE_PROVIDER", "s3")
            bucket = config.get("OPENSENSOR_STORAGE_BUCKET", "")
            console.print(f"  Cloud sync: [cyan]Enabled ({provider}: {bucket})[/cyan]")
        else:
            console.print("  Cloud sync: [cyan]Disabled[/cyan]")
        console.print(
            f"  Health monitoring: [cyan]{'Enabled' if health_enabled else 'Disabled'}[/cyan]"
        )
        console.print(f"  Config file: [dim]{env_file.absolute()}[/dim]")
    else:
        console.print("  [yellow]Not configured[/yellow]")
        console.print("  Run: [cyan]opensensor setup[/cyan]")

    # Sensor status
    console.print("\n[bold]Sensors:[/bold]")
    sensors_status = _check_sensor_availability()
    for sensor, status in sensors_status.items():
        console.print(f"  {sensor}: {status}")

    # Data statistics
    console.print("\n[bold]Data:[/bold]")
    try:
        sensor_config = SensorConfig()
        output_dir = sensor_config.output_dir
    except Exception:
        output_dir = Path("output")

    if output_dir.exists():
        parquet_files = list(output_dir.rglob("*.parquet"))
        total_size = sum(f.stat().st_size for f in parquet_files)
        size_mb = total_size / (1024 * 1024)
        console.print(f"  Parquet files: [green]{len(parquet_files)}[/green]")
        console.print(f"  Total size: [green]{size_mb:.2f} MB[/green]")
        console.print(f"  Location: [dim]{output_dir.absolute()}[/dim]")
    else:
        console.print("  [dim]No data collected yet[/dim]")

    # Health data
    try:
        sensor_config = SensorConfig()
        health_dir = sensor_config.health_dir
    except Exception:
        health_dir = Path("output-health")

    if health_dir.exists():
        health_files = list(health_dir.rglob("*.parquet"))
        if health_files:
            console.print(f"  Health files: [green]{len(health_files)}[/green]")

    # Service status (quick check)
    console.print("\n[bold]Service:[/bold]")
    try:
        manager = ServiceManager()
        if manager.is_installed():
            if manager.is_active():
                console.print("  Status: [green]Running[/green]")
            else:
                console.print("  Status: [yellow]Stopped[/yellow]")
            console.print(f"  Enabled: [cyan]{'Yes' if manager.is_enabled() else 'No'}[/cyan]")
        else:
            console.print("  [dim]Not installed[/dim]")
            console.print("  Run: [cyan]opensensor service setup[/cyan]")
    except Exception:
        console.print("  [dim]Service check unavailable[/dim]")

    console.print()


@app.command()
def sync(
    directory: Path | None = typer.Option(None, help="Directory to sync"),
):
    """
    Manually sync data to cloud storage.

    Uploads local parquet files to configured cloud storage
    (S3, R2, GCS, Azure, MinIO, Wasabi, Backblaze, Hetzner).
    """
    print_banner()
    console.print("[bold]Syncing to cloud...[/bold]\n")

    try:
        # Load configuration
        sensor_config = SensorConfig()
        storage_config = StorageConfig()
        app_config = AppConfig()

        if not storage_config.sync_enabled:
            console.print("[yellow]Cloud sync is not enabled.[/yellow]")
            console.print("\nEnable in .env: [cyan]OPENSENSOR_SYNC_ENABLED=true[/cyan]\n")
            sys.exit(1)

        # Setup logging
        logger = setup_logging(level=app_config.log_level)

        # Create sync client
        sync_client = ObstoreSync(config=storage_config, logger=logger)

        # Sync directory
        sync_dir = directory or sensor_config.output_dir
        files_synced = sync_client.sync_directory(sync_dir)

        if files_synced > 0:
            console.print(f"[green]Synced {files_synced} files[/green]\n")
        else:
            console.print("[dim]No new files to sync[/dim]\n")

    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]\n")
        sys.exit(1)


@app.command("fix-permissions")
def fix_permissions():
    """
    Fix serial port permissions for PMS5003 sensor.

    Requires sudo. Adds user to groups and creates udev rules.
    Reboot required after running.
    """
    import grp
    import os
    import subprocess

    print_banner()
    console.print("[bold]Fixing sensor permissions...[/bold]\n")

    # Check if running as root
    if os.geteuid() != 0:
        console.print("[red]ERROR: Requires sudo[/red]")
        console.print("\nRun: [cyan]sudo $(which opensensor) fix-permissions[/cyan]\n")
        sys.exit(1)

    # Get the actual user (not root)
    user = os.environ.get("SUDO_USER") or os.environ.get("USER")
    if not user or user == "root":
        console.print("[red]ERROR: Could not determine user.[/red]")
        console.print("Run with sudo from a regular user account.\n")
        sys.exit(1)

    console.print(f"User: [cyan]{user}[/cyan]\n")

    # Add user to required groups
    groups = ["dialout", "i2c", "gpio"]
    for group in groups:
        try:
            grp.getgrnam(group)
            result = subprocess.run(
                ["usermod", "-aG", group, user],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                console.print(f"  [green]Added to {group}[/green]")
            else:
                console.print(f"  [yellow]Warning: {group} - {result.stderr}[/yellow]")
        except KeyError:
            console.print(f"  [dim]Skipped {group} (not found)[/dim]")

    # Create udev rule for PMS5003 serial port
    # We support both /dev/ttyAMA0 and /dev/serial0 (and others if configured)
    # But udev rules match on KERNEL name, which is usually ttyAMA0 or ttyS0

    # Try to detect which one is being used or just add rules for both common ones
    udev_rules = [
        'KERNEL=="ttyAMA0", GROUP="dialout", MODE="0660"',
        'KERNEL=="ttyS0", GROUP="dialout", MODE="0660"',
        'SYMLINK=="serial0", GROUP="dialout", MODE="0660"',
    ]

    udev_file = Path("/etc/udev/rules.d/99-pms5003.rules")

    try:
        udev_file.write_text("\n".join(udev_rules) + "\n")
        console.print(f"\n[green]Created udev rule[/green]: {udev_file}")
    except OSError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)

    # Reload udev rules
    subprocess.run(["udevadm", "control", "--reload-rules"], capture_output=True)
    subprocess.run(["udevadm", "trigger", "--subsystem-match=tty"], capture_output=True)

    console.print("\n[bold green]Done![/bold green]")
    console.print("\n[yellow]REBOOT REQUIRED[/yellow]: Run [cyan]sudo reboot[/cyan]\n")


# Service management subcommand group
service_app = typer.Typer(
    name="service",
    help="Manage opensensor as a systemd service",
)
app.add_typer(service_app, name="service")


@service_app.command("setup")
def service_setup():
    """
    Quick setup: install + enable + start service.

    One command to get the service running on boot.
    """
    console.print("\n[bold]Setting up opensensor service...[/bold]\n")

    try:
        manager = ServiceManager()

        # Install
        console.print("1. Installing...")
        console.print(f"   User: [cyan]{manager.user}[/cyan]")
        console.print(f"   Path: [cyan]{manager.project_root}[/cyan]")
        manager.install()

        # Enable
        console.print("2. Enabling on boot...")
        manager.enable()

        # Start
        console.print("3. Starting...")
        manager.start()

        console.print("\n[bold green]Service running![/bold green]\n")
        console.print("Commands:")
        console.print("  [cyan]opensensor service status[/cyan]  - View status")
        console.print("  [cyan]opensensor service logs[/cyan]    - View logs")
        console.print("  [cyan]opensensor service stop[/cyan]    - Stop service\n")

    except PermissionError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@service_app.command("status")
def service_status():
    """
    Show service status and recent logs.
    """
    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print("\n[yellow]Service not installed[/yellow]")
            console.print("Run: [cyan]opensensor service setup[/cyan]\n")
            return

        console.print("\n[bold]Service Status[/bold]\n")

        # Status indicator
        if manager.is_active():
            console.print("  Status: [green]RUNNING[/green]")
        else:
            console.print("  Status: [red]STOPPED[/red]")

        console.print(f"  Enabled: [cyan]{'Yes' if manager.is_enabled() else 'No'}[/cyan]")

        # Get detailed status
        status_output, _ = manager.status()
        console.print(f"\n[dim]{status_output}[/dim]")

    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@service_app.command("logs")
def service_logs(
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines"),
):
    """
    View service logs from journalctl.
    """
    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print("[yellow]Service not installed[/yellow]")
            return

        if follow:
            console.print("[dim]Following logs... (Ctrl+C to stop)[/dim]\n")

        manager.get_logs(lines=lines, follow=follow)

    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@service_app.command("start")
def service_start():
    """Start the service."""
    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print("[red]Service not installed[/red]")
            console.print("Run: [cyan]opensensor service setup[/cyan]\n")
            sys.exit(1)

        manager.start()
        console.print("[green]Service started[/green]\n")

    except PermissionError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@service_app.command("stop")
def service_stop():
    """Stop the service."""
    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print("[yellow]Service not installed[/yellow]")
            return

        manager.stop()
        console.print("[green]Service stopped[/green]\n")

    except PermissionError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@service_app.command("restart")
def service_restart():
    """Restart the service."""
    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print("[red]Service not installed[/red]")
            sys.exit(1)

        manager.restart()
        console.print("[green]Service restarted[/green]\n")

    except PermissionError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


@service_app.command("remove")
def service_remove():
    """
    Completely remove the service.

    Stops, disables, and uninstalls the systemd service.
    """
    console.print("\n[bold]Removing opensensor service...[/bold]\n")

    try:
        manager = ServiceManager()

        if not manager.is_installed():
            console.print("[yellow]Service not installed[/yellow]\n")
            return

        # Stop
        if manager.is_active():
            console.print("1. Stopping...")
            manager.stop()

        # Disable
        if manager.is_enabled():
            console.print("2. Disabling...")
            manager.disable()

        # Uninstall
        console.print("3. Removing...")
        manager.uninstall()

        console.print("\n[green]Service removed[/green]\n")

    except PermissionError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]ERROR: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    app()
