# OpenSensor Enviroplus

Modern, CLI-based environmental sensor data collector using Polars, Apache Arrow, and Delta Lake for Raspberry Pi Enviro+.

## Features

- **Modern Stack**: Polars streaming, Apache Arrow, Delta Lake
- **Memory Efficient**: Optimized for Raspberry Pi with limited RAM
- **CLI-First**: Simple Python commands replace bash scripts
- **Smart Logging**: Rich console output for easy debugging
- **Cloud Sync**: Built-in sync using obstore (S3, GCS, Azure)
- **Type Safe**: Pydantic settings with validation
- **Production Ready**: Graceful error handling, automatic retries

## Quick Start

### Installation

```bash
# Install UV package manager
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
cd /root/byclaude/opensensor-enviroplus
uv sync

# Activate virtual environment
source .venv/bin/activate
```

### Setup

```bash
# Interactive setup (creates .env configuration)
opensensor setup

# Or non-interactive
opensensor setup --station-id "01234567-89ab-cdef-0123-456789abcdef" --no-interactive
```

### Usage

```bash
# Start collecting data
opensensor start

# Run in foreground (for debugging)
opensensor start --foreground

# View status
opensensor status

# Sync to cloud
opensensor sync

# View logs
opensensor logs

# Follow logs in real-time
opensensor logs --follow

# View configuration
opensensor config
```

## Configuration

Configuration via `.env` file (created by `opensensor setup`):

```bash
# Station identification
OPENSENSOR_STATION_ID=<uuid>

# Sensor settings
OPENSENSOR_READ_INTERVAL=5
OPENSENSOR_BATCH_DURATION=900
OPENSENSOR_TEMP_COMPENSATION_ENABLED=true
OPENSENSOR_TEMP_COMPENSATION_FACTOR=2.25

# Output
OPENSENSOR_OUTPUT_DIR=output
OPENSENSOR_USE_DELTA=true
OPENSENSOR_COMPRESSION=zstd

# Cloud sync
OPENSENSOR_SYNC_ENABLED=true
OPENSENSOR_SYNC_INTERVAL_MINUTES=15
OPENSENSOR_STORAGE_BUCKET=my-bucket
OPENSENSOR_STORAGE_PREFIX=sensor-data
OPENSENSOR_STORAGE_REGION=us-west-2
OPENSENSOR_AWS_ACCESS_KEY_ID=<key>
OPENSENSOR_AWS_SECRET_ACCESS_KEY=<secret>

# Logging
OPENSENSOR_LOG_LEVEL=INFO
OPENSENSOR_LOG_DIR=logs
```

## Architecture

### Data Flow

```
Sensors -> Collector (Polars) -> Delta Lake -> Cloud Storage (obstore)
```

### Output Format (Delta Lake)

```
output/
  delta/
    _delta_log/
      00000000000000000000.json
    part-*.parquet
```

## Differences from Original

| Feature | Old | New |
|---------|-----|-----|
| Data library | pandas + DuckDB | Polars + Arrow |
| Storage | Partitioned Parquet | Delta Lake |
| Configuration | bash scripts | Pydantic Settings |
| Setup | install.sh | `opensensor setup` |
| Sync | rclone | obstore |
| Logging | print statements | Rich + structured |
| Memory | Higher | 50% lower |

## Development

```bash
# Install with dev dependencies
uv sync --group dev

# Format code
ruff format .

# Lint
ruff check .
```

## License

MIT

## Credits

- [enviroplus-community](https://github.com/walkthru-earth/enviroplus-python)
- [Polars](https://pola.rs/)
- [obstore](https://developmentseed.org/obstore/)
- [Typer](https://typer.tiangolo.com/)
