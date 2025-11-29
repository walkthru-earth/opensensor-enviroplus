"""
Modern sensor data collector using Polars and Apache Arrow.
Memory-efficient streaming with Delta Lake for append-capable storage.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import polars as pl
import pyarrow as pa

from opensensor_enviroplus.config.settings import SensorConfig, StorageConfig
from opensensor_enviroplus.sync.obstore_sync import ObstoreSync
from opensensor_enviroplus.utils.compensation import (
    compensate_humidity,
    compensate_temperature,
    get_cpu_temperature,
)
from opensensor_enviroplus.utils.health import collect_health_metrics, health_to_dict
from opensensor_enviroplus.utils.logging import (
    log_batch_write,
    log_error,
    log_sensor_reading,
    log_status,
)

# Sensor imports (direct dependencies, no enviroplus wrapper)
try:
    import ads1015
    import gpiod
    import gpiodevice
    from bme280 import BME280
    from gpiod.line import Direction, Value
    from ltr559 import LTR559
    from pms5003 import PMS5003, ReadTimeoutError
    from smbus2 import SMBus

    SENSORS_AVAILABLE = True
except ImportError:
    SENSORS_AVAILABLE = False

# MICS6814 gas sensor constants
MICS6814_GAIN = 6.144
MICS6814_I2C_ADDR = 0x49
MICS6814_HEATER_PIN = "GPIO24"


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
        health_storage_config: StorageConfig | None = None,
    ):
        self.config = config
        self.logger = logger
        self.buffer: list[dict[str, Any]] = []
        self.health_buffer: list[dict[str, Any]] = []  # System health metrics

        # Calculate next clock-aligned batch boundary (00, 15, 30, 45 minutes)
        self.next_batch_time = self._calculate_next_batch_boundary()
        self.cpu_temps: list[float] = []

        # Sensor warm-up tracking
        self.readings_count = 0
        self.warmup_readings = 10  # Skip first 10 readings for sensor stabilization

        # Health collection interval (every N sensor readings)
        self.health_interval = 12  # ~1 minute at 5s intervals

        # Sync setup
        self.storage_config = storage_config
        self.health_storage_config = health_storage_config
        self.sync_client = None
        self.health_sync_client = None

        if storage_config and storage_config.sync_enabled:
            self.sync_client = ObstoreSync(config=storage_config, logger=logger)

        # Initialize health sync client
        # Logic:
        # 1. If health config has sync_enabled=True, use it.
        # 2. If health config has sync_enabled=False (default) but main sync is enabled,
        #    we assume health sync is desired and inherit settings from main config.
        if health_storage_config:
            # Inherit enabled state if not explicitly set (default is False)
            if (
                not health_storage_config.sync_enabled
                and storage_config
                and storage_config.sync_enabled
            ):
                health_storage_config.sync_enabled = True

            # If enabled (explicitly or via inheritance), ensure we have required settings
            if health_storage_config.sync_enabled:
                # If bucket is missing, inherit everything from main config
                if not health_storage_config.storage_bucket and storage_config:
                    health_storage_config.storage_provider = storage_config.storage_provider
                    health_storage_config.storage_bucket = storage_config.storage_bucket
                    health_storage_config.storage_region = storage_config.storage_region
                    health_storage_config.storage_endpoint = storage_config.storage_endpoint
                    health_storage_config.aws_access_key_id = storage_config.aws_access_key_id
                    health_storage_config.aws_secret_access_key = (
                        storage_config.aws_secret_access_key
                    )
                    health_storage_config.gcs_service_account_path = (
                        storage_config.gcs_service_account_path
                    )
                    health_storage_config.azure_storage_account = (
                        storage_config.azure_storage_account
                    )
                    health_storage_config.azure_storage_key = storage_config.azure_storage_key
                    health_storage_config.azure_sas_token = storage_config.azure_sas_token

                    # Set default health prefix if missing
                    if not health_storage_config.storage_prefix and storage_config.storage_prefix:
                        health_storage_config.storage_prefix = (
                            f"{storage_config.storage_prefix}-health"
                        )

                # Initialize client if we have a valid config
                if health_storage_config.storage_bucket:
                    self.health_sync_client = ObstoreSync(
                        config=health_storage_config, logger=logger
                    )

        if (storage_config and storage_config.sync_enabled) or (
            health_storage_config and health_storage_config.sync_enabled
        ):
            # Calculate next clock-aligned sync boundary (00, 15, 30, 45 minutes)
            # Use interval from main config or health config (prefer main if available)
            interval = 15
            if storage_config:
                interval = storage_config.sync_interval_minutes
            elif health_storage_config:
                interval = health_storage_config.sync_interval_minutes

            self.next_sync_time = self._calculate_next_sync_boundary(interval)
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
                ("raw_humidity", pa.float32()),
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
            self.gas_adc = None
            return

        # BME280: temperature, pressure, humidity
        try:
            self.bme280 = BME280(i2c_dev=SMBus(1))
            log_status("BME280 sensor initialized", self.logger, "")
        except Exception as e:
            log_error(e, self.logger, "BME280 initialization failed")
            self.bme280 = None

        # MICS6814 gas sensor via ADS1015 ADC (direct, no enviroplus wrapper)
        try:
            ads1015.I2C_ADDRESS_DEFAULT = MICS6814_I2C_ADDR
            self.gas_adc = ads1015.ADS1015(i2c_addr=MICS6814_I2C_ADDR)
            adc_type = self.gas_adc.detect_chip_type()

            self.gas_adc.set_mode("single")
            self.gas_adc.set_programmable_gain(MICS6814_GAIN)
            if adc_type == "ADS1115":
                self.gas_adc.set_sample_rate(128)
            else:
                self.gas_adc.set_sample_rate(1600)

            # Enable heater via GPIO24
            outh = gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE)
            self._gas_heater = gpiodevice.get_pin(MICS6814_HEATER_PIN, "EnviroPlus", outh)
            log_status(f"MICS6814 gas sensor initialized ({adc_type})", self.logger, "")
        except Exception as e:
            log_error(e, self.logger, "Gas sensor initialization failed")
            self.gas_adc = None
            self._gas_heater = None

        # LTR559: light and proximity
        try:
            self.ltr559 = LTR559()
            log_status("LTR559 light sensor initialized", self.logger, "")
        except Exception as e:
            log_error(e, self.logger, "LTR559 initialization failed")
            self.ltr559 = None

        # PMS5003: particulate matter
        try:
            self.pms5003 = PMS5003(device=self.config.pms5003_device)
            log_status(
                f"PMS5003 particulate sensor initialized ({self.config.pms5003_device})",
                self.logger,
                "",
            )
        except Exception as e:
            log_error(e, self.logger, "PMS5003 initialization failed")
            self.pms5003 = None

    def _get_cpu_temperature(self) -> float:
        """Get CPU temperature for compensation."""
        return get_cpu_temperature()

    @staticmethod
    def _voltage_to_resistance(voltage: float) -> float:
        """
        Convert ADC voltage to resistance for MICS6814 gas sensor.

        The MICS6814 is a resistive sensor. This formula converts the voltage
        reading from the ADS1015 ADC to resistance in Ohms.

        Formula: R = (V * 56000) / (3.3 - V)
        Where 56000 is the load resistor value and 3.3V is the reference voltage.
        """
        try:
            return (voltage * 56000) / (3.3 - voltage)
        except ZeroDivisionError:
            return 0.0

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
        return compensate_temperature(
            raw_temp,
            cpu_temp,
            self.config.temp_compensation_factor,
            avg_cpu_temp,
        )

    def _compensate_humidity(
        self, raw_humidity: float, raw_temp: float, compensated_temp: float
    ) -> float:
        """
        Compensate humidity based on corrected temperature.

        The BME280's humidity reading is affected by temperature errors.
        This uses the dewpoint approach from Pimoroni's official examples:
        https://github.com/pimoroni/enviroplus-python/blob/main/examples/weather-and-light.py

        Formula:
        1. Calculate dewpoint from raw readings
        2. Recalculate humidity using compensated temperature and dewpoint
        """
        if not self.config.temp_compensation_enabled:
            return raw_humidity

        # Use shared utility
        return compensate_humidity(raw_humidity, raw_temp, compensated_temp)

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
                raw_humidity = self.bme280.get_humidity()
                compensated_temp = self._compensate_temperature(raw_temp)

                data["temperature"] = compensated_temp
                data["raw_temperature"] = raw_temp
                data["pressure"] = self.bme280.get_pressure()
                data["humidity"] = self._compensate_humidity(
                    raw_humidity, raw_temp, compensated_temp
                )
                data["raw_humidity"] = raw_humidity
            except Exception as e:
                log_error(e, self.logger, "BME280 read error")

        # MICS6814 gas sensor via ADS1015 ADC
        if self.gas_adc:
            try:
                ox = self.gas_adc.get_voltage("in0/gnd")
                red = self.gas_adc.get_voltage("in1/gnd")
                nh3 = self.gas_adc.get_voltage("in2/gnd")

                # Convert voltage to resistance (kOhms)
                data["oxidised"] = self._voltage_to_resistance(ox) / 1000.0
                data["reducing"] = self._voltage_to_resistance(red) / 1000.0
                data["nh3"] = self._voltage_to_resistance(nh3) / 1000.0
            except Exception as e:
                log_error(e, self.logger, "Gas sensor read error")

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

    def _calculate_next_sync_boundary(self, interval_minutes: int = 15) -> datetime:
        """
        Calculate the next clock-aligned sync boundary.

        Aligns syncs to 0, 15, 30, 45 minute marks for consistency with batch writes.
        Example: If started at 10:37 with 15min sync, next boundary is 10:45.
        """
        now = datetime.now(timezone.utc)
        sync_minutes = interval_minutes

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

        # Collect health metrics periodically (less frequent than sensor data)
        if self.config.health_enabled and self.readings_count % self.health_interval == 0:
            self._collect_health()

    def should_flush(self) -> bool:
        """
        Check if it's time to flush the batch.

        Uses clock-aligned boundaries (00, 15, 30, 45 minutes) instead of elapsed time.
        This ensures consistent batch times across all sensors.
        """
        return datetime.now(timezone.utc) >= self.next_batch_time

    def _collect_health(self) -> None:
        """Collect system health metrics."""
        try:
            health = collect_health_metrics()
            health_data = health_to_dict(health)
            health_data["station_id"] = str(self.config.station_id)
            self.health_buffer.append(health_data)
            self.logger.debug(
                f"Health: CPU={health.cpu_temp_c:.1f}°C, "
                f"Mem={health.memory_percent_used:.0f}%, "
                f"NTP={'synced' if health.clock_synced else 'NOT synced'}"
            )
        except Exception as e:
            log_error(e, self.logger, "Health collection error")

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

            # Write health data if available and enabled
            if self.config.health_enabled and self.health_buffer:
                self._write_health_parquet()

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
            "raw_humidity",
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
        if not self.next_sync_time:
            return False

        has_sync = (self.sync_client is not None) or (self.health_sync_client is not None)
        if not has_sync:
            return False

        return datetime.now(timezone.utc) >= self.next_sync_time

    def sync_data(self) -> None:
        """Sync data to cloud storage."""
        if not self.sync_client and not self.health_sync_client:
            return

        try:
            # Sync sensor data
            if self.sync_client:
                files_synced = self.sync_client.sync_directory(self.config.output_dir)
                if files_synced > 0:
                    log_status(
                        f"Synced {files_synced} files to cloud storage",
                        self.logger,
                        "SYNC",
                    )

            # Sync health data
            if self.health_sync_client and self.config.health_dir:
                health_synced = self.health_sync_client.sync_directory(self.config.health_dir)
                if health_synced > 0:
                    log_status(
                        f"Synced {health_synced} health files to cloud storage",
                        self.logger,
                        "SYNC",
                    )

            # Calculate next clock-aligned sync boundary
            # Use interval from main config or health config
            interval = 15
            if self.storage_config:
                interval = self.storage_config.sync_interval_minutes
            elif self.health_storage_config:
                interval = self.health_storage_config.sync_interval_minutes

            self.next_sync_time = self._calculate_next_sync_boundary(interval)
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
            use_pyarrow=True,  # Use PyArrow for better compatibility and file size
        )

        self.logger.debug(
            f"Wrote {len(df_to_write)} rows to {partition_path.relative_to(self.config.output_dir)}/{filename}"
        )

    def _write_health_parquet(self) -> None:
        """
        Write system health metrics to a separate Parquet file.

        Output structure (sibling to sensor data, won't break existing queries):
        - Sensor data: output/station={id}/year={y}/month={m}/day={d}/data_*.parquet
        - Health data: output-health/station={id}/year={y}/month={m}/day={d}/health_*.parquet

        Health data includes: CPU temp, memory, disk, WiFi signal, NTP sync status,
        power source, uptime - critical for remote monitoring and debugging.
        """
        if not self.health_buffer:
            return

        batch_end = datetime.now(timezone.utc)

        # Create health DataFrame
        df = pl.DataFrame(self.health_buffer)

        # Extract partition values from first timestamp
        first_ts = df["timestamp"][0]
        if isinstance(first_ts, str):
            first_ts = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))

        year = first_ts.year
        month = first_ts.month
        day = first_ts.day

        filename = f"health_{batch_end.strftime('%H%M')}.parquet"

        # Health data goes in a sibling directory (output-health instead of output)
        # This keeps sensor and health data completely separate with identical partition structure
        health_output_dir = self.config.health_dir
        partition_path = (
            health_output_dir
            / f"station={self.config.station_id}"
            / f"year={year}"
            / f"month={month:02d}"
            / f"day={day:02d}"
        )

        partition_path.mkdir(parents=True, exist_ok=True)
        file_path = partition_path / filename

        # Remove station_id (in directory structure)
        df_to_write = df.drop("station_id")

        df_to_write.write_parquet(
            str(file_path),
            compression=self.config.compression,
            statistics=True,
            use_pyarrow=True,
        )

        self.health_buffer.clear()
        self.logger.debug(f"Wrote {len(df_to_write)} health records to {filename}")

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

        # Health monitoring status
        if self.config.health_enabled:
            log_status(
                "Health monitoring enabled (CPU, memory, disk, WiFi, NTP sync)",
                self.logger,
                "HEALTH",
            )
        else:
            log_status("Health monitoring disabled", self.logger, "HEALTH")

        if self.sync_client or self.health_sync_client:
            # Determine interval and bucket for logging
            sync_minutes = 15
            bucket_info = ""

            if self.sync_client and self.storage_config:
                sync_minutes = self.storage_config.sync_interval_minutes
                bucket_info = f"to {self.storage_config.storage_bucket}"

            if self.health_sync_client and self.health_storage_config:
                if not bucket_info:
                    sync_minutes = self.health_storage_config.sync_interval_minutes
                    bucket_info = f"health to {self.health_storage_config.storage_bucket}"
                else:
                    bucket_info += f" (health to {self.health_storage_config.storage_bucket})"

            log_status(
                f"Auto-sync enabled: clock-aligned {sync_minutes}min intervals (00, 15, 30, 45) "
                f"{bucket_info}",
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
            if self.sync_client or self.health_sync_client:
                self.logger.info("Performing final sync...")
                self.sync_data()
        except Exception as e:
            log_error(e, self.logger, "Collection error")
            raise
