"""
Modern sensor data collector using Polars and Apache Arrow.
Memory-efficient streaming with Delta Lake for append-capable storage.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow as pa

# Sensor imports
try:
    from bme280 import BME280
    from enviroplus import gas
    from ltr559 import LTR559
    from pms5003 import PMS5003, ReadTimeoutError
    from smbus2 import SMBus

    SENSORS_AVAILABLE = True
except ImportError:
    SENSORS_AVAILABLE = False

from opensensor_enviroplus.config.settings import SensorConfig, StorageConfig
from opensensor_enviroplus.sync.obstore_sync import ObstoreSync
from opensensor_enviroplus.utils.logging import (
    log_batch_write,
    log_error,
    log_sensor_reading,
    log_status,
)


class PolarsSensorCollector:
    """
    Production-ready sensor collector using Polars streaming.

    Features:
    - Memory-efficient batch collection
    - Delta Lake for append-capable storage
    - Apache Arrow for zero-copy operations
    - Graceful sensor error handling
    - Smart logging for debugging
    """

    def __init__(
        self,
        config: SensorConfig,
        logger: logging.Logger,
        storage_config: StorageConfig | None = None,
    ):
        self.config = config
        self.logger = logger
        self.buffer: list[dict[str, Any]] = []

        # Calculate next clock-aligned batch boundary (00, 15, 30, 45 minutes)
        self.next_batch_time = self._calculate_next_batch_boundary()
        self.cpu_temps: list[float] = []

        # Sensor warm-up tracking
        self.readings_count = 0
        self.warmup_readings = 10  # Skip first 10 readings for sensor stabilization

        # Sync setup
        self.storage_config = storage_config
        self.sync_client = None

        if storage_config and storage_config.sync_enabled:
            self.sync_client = ObstoreSync(config=storage_config, logger=logger)
            # Calculate next clock-aligned sync boundary (00, 15, 30, 45 minutes)
            self.next_sync_time = self._calculate_next_sync_boundary()
        else:
            self.next_sync_time = None

        # Initialize sensors
        self._init_sensors()

        # Arrow schema for type safety and efficiency
        self.schema = pa.schema(
            [
                ("timestamp", pa.timestamp("ms", tz="UTC")),
                ("station_id", pa.string()),
                ("temperature", pa.float32()),
                ("raw_temperature", pa.float32()),
                ("pressure", pa.float32()),
                ("humidity", pa.float32()),
                ("oxidised", pa.float32()),
                ("reducing", pa.float32()),
                ("nh3", pa.float32()),
                ("lux", pa.float32()),
                ("proximity", pa.float32()),
                ("pm1", pa.float32()),
                ("pm25", pa.float32()),
                ("pm10", pa.float32()),
                ("particles_03um", pa.float32()),
                ("particles_05um", pa.float32()),
                ("particles_10um", pa.float32()),
                ("particles_25um", pa.float32()),
                ("particles_50um", pa.float32()),
                ("particles_100um", pa.float32()),
            ]
        )

    def _init_sensors(self) -> None:
        """Initialize hardware sensors with error handling."""
        if not SENSORS_AVAILABLE:
            self.logger.warning("WARNING:  Sensor libraries not available (mock mode)")
            self.bme280 = None
            self.ltr559 = None
            self.pms5003 = None
            return

        try:
            self.bme280 = BME280(i2c_dev=SMBus(1))
            gas.enable_adc()
            gas.set_adc_gain(4.096)
            log_status("BME280 and gas sensor initialized", self.logger, "")
        except Exception as e:
            log_error(e, self.logger, "BME280 initialization failed")
            self.bme280 = None

        try:
            self.ltr559 = LTR559()
            log_status("LTR559 light sensor initialized", self.logger, "")
        except Exception as e:
            log_error(e, self.logger, "LTR559 initialization failed")
            self.ltr559 = None

        try:
            self.pms5003 = PMS5003()
            log_status("PMS5003 particulate sensor initialized", self.logger, "")
        except Exception as e:
            log_error(e, self.logger, "PMS5003 initialization failed")
            self.pms5003 = None

    def _get_cpu_temperature(self) -> float:
        """Get CPU temperature for compensation."""
        try:
            with Path("/sys/class/thermal/thermal_zone0/temp").open() as f:
                return float(f.read()) / 1000.0
        except (OSError, ValueError):
            return 40.0

    def _compensate_temperature(self, raw_temp: float) -> float:
        """Compensate temperature for CPU heat."""
        if not self.config.temp_compensation_enabled:
            return raw_temp

        cpu_temp = self._get_cpu_temperature()

        if not self.cpu_temps:
            self.cpu_temps = [cpu_temp] * 5
        else:
            self.cpu_temps = self.cpu_temps[1:] + [cpu_temp]

        avg_cpu_temp = sum(self.cpu_temps) / len(self.cpu_temps)
        return raw_temp - ((avg_cpu_temp - raw_temp) / self.config.temp_compensation_factor)

    def read_sensors(self) -> dict[str, Any]:
        """Read from all available sensors."""
        data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc),
            "station_id": str(self.config.station_id),
        }

        # BME280: temperature, pressure, humidity
        if self.bme280:
            try:
                raw_temp = self.bme280.get_temperature()
                data["temperature"] = self._compensate_temperature(raw_temp)
                data["raw_temperature"] = raw_temp
                data["pressure"] = self.bme280.get_pressure()
                data["humidity"] = self.bme280.get_humidity()

                # Gas sensor
                gas_data = gas.read_all()
                data["oxidised"] = gas_data.oxidising / 1000.0
                data["reducing"] = gas_data.reducing / 1000.0
                data["nh3"] = gas_data.nh3 / 1000.0
            except Exception as e:
                log_error(e, self.logger, "BME280/Gas read error")

        # LTR559: light sensor
        if self.ltr559:
            try:
                data["lux"] = self.ltr559.get_lux()
                data["proximity"] = self.ltr559.get_proximity()
            except Exception as e:
                log_error(e, self.logger, "LTR559 read error")

        # PMS5003: particulate matter
        if self.pms5003:
            try:
                pm = self.pms5003.read()
                data["pm1"] = float(pm.pm_ug_per_m3(1.0))
                data["pm25"] = float(pm.pm_ug_per_m3(2.5))
                data["pm10"] = float(pm.pm_ug_per_m3(10.0))
                data["particles_03um"] = float(pm.pm_per_1l_air(0.3))
                data["particles_05um"] = float(pm.pm_per_1l_air(0.5))
                data["particles_10um"] = float(pm.pm_per_1l_air(1.0))
                data["particles_25um"] = float(pm.pm_per_1l_air(2.5))
                data["particles_50um"] = float(pm.pm_per_1l_air(5.0))
                data["particles_100um"] = float(pm.pm_per_1l_air(10.0))
            except (ReadTimeoutError, ValueError) as e:
                log_error(e, self.logger, "PMS5003 read error")
                # Set PM fields to None on error
                for field in [
                    "pm1",
                    "pm25",
                    "pm10",
                    "particles_03um",
                    "particles_05um",
                    "particles_10um",
                    "particles_25um",
                    "particles_50um",
                    "particles_100um",
                ]:
                    data.setdefault(field, None)

        log_sensor_reading(data, self.logger)
        return data

    def _calculate_next_batch_boundary(self) -> datetime:
        """
        Calculate the next clock-aligned batch boundary.

        Aligns batches to 0, 15, 30, 45 minute marks for consistency.
        Example: If started at 10:37, next boundary is 10:45.
        """
        now = datetime.now(timezone.utc)
        batch_minutes = self.config.batch_duration // 60  # Convert seconds to minutes

        # Calculate current minute aligned to batch interval
        current_minute = now.minute
        next_boundary_minute = ((current_minute // batch_minutes) + 1) * batch_minutes

        # Create next boundary time
        if next_boundary_minute >= 60:
            # Rolls over to next hour
            next_boundary = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_boundary = now.replace(minute=next_boundary_minute, second=0, microsecond=0)

        return next_boundary

    def _calculate_next_sync_boundary(self) -> datetime:
        """
        Calculate the next clock-aligned sync boundary.

        Aligns syncs to 0, 15, 30, 45 minute marks for consistency with batch writes.
        Example: If started at 10:37 with 15min sync, next boundary is 10:45.
        """
        now = datetime.now(timezone.utc)
        sync_minutes = self.storage_config.sync_interval_minutes

        # Calculate current minute aligned to sync interval
        current_minute = now.minute
        next_boundary_minute = ((current_minute // sync_minutes) + 1) * sync_minutes

        # Create next boundary time
        if next_boundary_minute >= 60:
            # Rolls over to next hour
            next_boundary = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_boundary = now.replace(minute=next_boundary_minute, second=0, microsecond=0)

        return next_boundary

    def collect_reading(self) -> None:
        """Collect a sensor reading and buffer it."""
        reading = self.read_sensors()
        self.readings_count += 1

        # Skip initial readings during sensor warm-up period
        if self.readings_count <= self.warmup_readings:
            self.logger.debug(
                f"Skipping warm-up reading {self.readings_count}/{self.warmup_readings}"
            )
            return

        self.buffer.append(reading)

    def should_flush(self) -> bool:
        """
        Check if it's time to flush the batch.

        Uses clock-aligned boundaries (00, 15, 30, 45 minutes) instead of elapsed time.
        This ensures consistent batch times across all sensors.
        """
        return datetime.now(timezone.utc) >= self.next_batch_time

    def flush_batch(self) -> None:
        """Write buffered readings using Polars and Delta Lake."""
        if not self.buffer:
            self.logger.warning("WARNING:  No data to flush")
            return

        start_time = time.time()
        count = len(self.buffer)

        try:
            # Create Polars DataFrame from buffer
            df = pl.DataFrame(self.buffer)

            # Optimize data types and ensure proper timestamp format
            df = self._optimize_dtypes(df)

            # Write Hive-partitioned Parquet
            self._write_parquet_partitioned(df)

            # Clear buffer and calculate next boundary
            self.buffer.clear()
            self.next_batch_time = self._calculate_next_batch_boundary()

            duration = time.time() - start_time
            log_batch_write(count, self.config.output_dir, duration, self.logger)
            self.logger.info(f"Next batch at: {self.next_batch_time.strftime('%H:%M:%S UTC')}")

        except Exception as e:
            log_error(e, self.logger, "Failed to flush batch")

    def _optimize_dtypes(self, df: pl.DataFrame) -> pl.DataFrame:
        """Optimize column data types for memory efficiency."""
        float_cols = [
            "temperature",
            "raw_temperature",
            "pressure",
            "humidity",
            "oxidised",
            "reducing",
            "nh3",
            "lux",
            "proximity",
            "pm1",
            "pm25",
            "pm10",
            "particles_03um",
            "particles_05um",
            "particles_10um",
            "particles_25um",
            "particles_50um",
            "particles_100um",
        ]

        # Convert all float columns to Float32 for memory efficiency
        for col in float_cols:
            if col in df.columns:
                df = df.with_columns(pl.col(col).cast(pl.Float32))

        # Ensure timestamp is datetime (not string)
        if "timestamp" in df.columns:
            df = df.with_columns(
                pl.col("timestamp").cast(pl.Datetime(time_unit="ms", time_zone="UTC"))
            )

        return df

    def should_sync(self) -> bool:
        """
        Check if it's time to sync to cloud storage.

        Uses clock-aligned boundaries (00, 15, 30, 45 minutes) instead of elapsed time.
        This ensures consistent sync times across all sensors, matching batch writes.
        """
        if not self.sync_client or not self.storage_config or not self.next_sync_time:
            return False

        return datetime.now(timezone.utc) >= self.next_sync_time

    def sync_data(self) -> None:
        """Sync data to cloud storage."""
        if not self.sync_client:
            return

        try:
            files_synced = self.sync_client.sync_directory(self.config.output_dir)
            if files_synced > 0:
                log_status(
                    f"Synced {files_synced} files to cloud storage",
                    self.logger,
                    "SYNC",
                )

            # Calculate next clock-aligned sync boundary
            self.next_sync_time = self._calculate_next_sync_boundary()
            self.logger.info(f"Next sync at: {self.next_sync_time.strftime('%H:%M:%S UTC')}")

        except Exception as e:
            log_error(e, self.logger, "Cloud sync failed")

    def _write_parquet_partitioned(self, df: pl.DataFrame) -> None:
        """
        Write Hive-partitioned Parquet files matching opensensor.space architecture.

        Output structure: station={id}/year={y}/month={m}/day={d}/data_{HHMM}.parquet

        Note: station_id is NOT stored in the parquet file itself - it's only in the
        directory structure. DuckDB automatically extracts it with hive_partitioning=true.
        This follows Hive partitioning best practices and reduces file size.
        """
        # Get batch end time for filename
        batch_end = datetime.now(timezone.utc)

        # Extract partition values from first timestamp in batch
        first_ts = df["timestamp"][0]
        if isinstance(first_ts, str):
            first_ts = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))

        year = first_ts.year
        month = first_ts.month
        day = first_ts.day

        # Create filename with batch end time (HHMM format)
        filename = f"data_{batch_end.strftime('%H%M')}.parquet"

        # Build Hive-partitioned path
        partition_path = (
            self.config.output_dir
            / f"station={self.config.station_id}"
            / f"year={year}"
            / f"month={month:02d}"
            / f"day={day:02d}"
        )

        # Create directory structure
        partition_path.mkdir(parents=True, exist_ok=True)

        # Full file path
        file_path = partition_path / filename

        # Remove partition columns from dataframe (station_id is in directory structure)
        # This follows Hive partitioning best practices and reduces file size
        df_to_write = df.drop("station_id")

        # Write Parquet with compression
        df_to_write.write_parquet(
            str(file_path),
            compression=self.config.compression,
            statistics=True,
            use_pyarrow=False,  # Use Polars native writer
        )

        self.logger.debug(
            f"Wrote {len(df_to_write)} rows to {partition_path.relative_to(self.config.output_dir)}/{filename}"
        )

    def run(self) -> None:
        """Main collection loop."""
        batch_minutes = self.config.batch_duration // 60
        log_status(
            f"Starting collection: {self.config.read_interval}s interval, "
            f"clock-aligned {batch_minutes}min batches (00, 15, 30, 45)",
            self.logger,
            "",
        )

        # Warm-up notification
        warmup_time = self.warmup_readings * self.config.read_interval
        log_status(
            f"Sensor warm-up: skipping first {self.warmup_readings} readings (~{warmup_time}s)",
            self.logger,
            "WARMUP",
        )

        # Next batch time
        log_status(
            f"First batch at: {self.next_batch_time.strftime('%H:%M:%S UTC')}",
            self.logger,
            "BATCH",
        )

        if self.sync_client:
            sync_minutes = self.storage_config.sync_interval_minutes
            log_status(
                f"Auto-sync enabled: clock-aligned {sync_minutes}min intervals (00, 15, 30, 45) "
                f"to {self.storage_config.storage_bucket}",
                self.logger,
                "SYNC",
            )
            log_status(
                f"First sync at: {self.next_sync_time.strftime('%H:%M:%S UTC')}",
                self.logger,
                "SYNC",
            )

        try:
            while True:
                self.collect_reading()

                if self.should_flush():
                    self.flush_batch()

                if self.should_sync():
                    self.sync_data()

                time.sleep(self.config.read_interval)

        except KeyboardInterrupt:
            self.logger.info("Stopping collection...")
            if self.buffer:
                self.flush_batch()
            # Final sync on exit
            if self.sync_client:
                self.logger.info("Performing final sync...")
                self.sync_data()
        except Exception as e:
            log_error(e, self.logger, "Collection error")
            raise
