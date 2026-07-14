"""Raspberry Pi 5 Backend - Hardware-specific implementation.

This module provides the Raspberry Pi 5 backend for the tsOS Schedule Daemon.
"""

import datetime
import logging
import os
import pathlib
import struct
import subprocess
import tempfile

from .. import ActionReason
from .base import PowerManager
from .wittypi4 import WittyPiException

logger = logging.getLogger("tsschedule.backends.raspberrypi5")

POWER_DT_PATH = pathlib.Path("/proc/device-tree/chosen/power")
BOOTLOADER_RSTS_PATH = pathlib.Path("/proc/device-tree/chosen/bootloader/rsts")

POWER_RESET_BITS = {
    0: "over_voltage",
    1: "under_voltage",
    2: "over_temperature",
    3: "enable_signal",
    4: "watchdog",
}

# PM_RSTS reset-reason bits (same value as vcgencmd get_rsts).
PM_RSTS_BITS = {
    12: "power_on_reset",
    5: "watchdog_full_reset",
    4: "watchdog_quick_reset",
    2: "debugger_hard_reset",
    1: "debugger_full_reset",
    0: "debugger_quick_reset",
}


def _read_dt_u32(path: pathlib.Path) -> int | None:
    """Read a big-endian u32 device-tree property, or None if unavailable."""
    try:
        return struct.unpack(">I", path.read_bytes())[0]
    except (OSError, struct.error):
        return None


class RaspberryPi5(PowerManager):
    """Interface to Raspberry Pi 5 onboard RTC for sleep/wake operations.

    This class provides RTC-based power management for Raspberry Pi 5 using the
    onboard real-time clock. It supports:
    - RTC datetime operations
    - Wake alarm configuration via sysfs
    - System shutdown with wake scheduling

    The Raspberry Pi 5 RTC functionality is more limited than WittyPi hardware:
    - No live voltage/current monitoring
    - No temperature monitoring
    - No hardware power cut delays
    - Boot-time reset and power diagnostics via device-tree

    Requires:
    - EEPROM configuration: POWER_OFF_ON_HALT=1 and WAKE_ON_GPIO=0
    - Root privileges for EEPROM configuration (checked automatically)

    Args:
        tz: Timezone for RTC operations (default: UTC)
        check_eeprom: If True (default), check and configure EEPROM settings

    Raises:
        WittyPiException: If RTC wakealarm interface is not available
    """

    RTC_PATH = "/sys/class/rtc/rtc0"
    RTC_WAKEALARM_PATH = RTC_PATH + "/wakealarm"

    def __init__(self, tz=datetime.UTC, check_eeprom: bool = True):
        """Initialize Raspberry Pi 5 RTC interface.

        Args:
            tz: Timezone for RTC operations (default: UTC)
            check_eeprom: If True, check and configure EEPROM settings (default: True)
        """
        super().__init__(tz)

        # Check if RTC wakealarm is available
        wakealarm_path = pathlib.Path(self.RTC_WAKEALARM_PATH)
        if not wakealarm_path.exists():
            raise WittyPiException(
                f"RTC wakealarm not available at {self.RTC_WAKEALARM_PATH}. "
                "Ensure Raspberry Pi 5 RTC is enabled."
            )

        # Check and configure EEPROM if requested
        if check_eeprom:
            self._check_eeprom_config()

        self._shutdown_datetime: datetime.datetime | None = None
        self._power_reset: int | None = None
        self._pm_rsts: int | None = None
        self._max_current: int | None = None
        self._usb_over_current_detected: bool | None = None
        self._read_power_diagnostics()

        logger.info("Raspberry Pi 5 RTC interface initialized")

    def _read_power_diagnostics(self):
        """Read boot-time reset and power diagnostics from device-tree."""
        self._pm_rsts = _read_dt_u32(BOOTLOADER_RSTS_PATH)

        if not POWER_DT_PATH.is_dir():
            return

        self._power_reset = _read_dt_u32(POWER_DT_PATH / "power_reset")
        self._max_current = _read_dt_u32(POWER_DT_PATH / "max_current")

        usb_oc = _read_dt_u32(POWER_DT_PATH / "usb_over_current_detected")
        if usb_oc is not None:
            self._usb_over_current_detected = bool(usb_oc)

    def _check_eeprom_config(self):
        """Check and update EEPROM configuration for sleep/wake support.

        Verifies that POWER_OFF_ON_HALT=1 and WAKE_ON_GPIO=0 are set.
        Updates configuration if needed using rpi-eeprom-config.
        """
        try:
            # Create temporary file for bootconf
            with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as tmp_file:
                bootconf_path = tmp_file.name

            try:
                # Dump current configuration
                try:
                    subprocess.run(
                        ["rpi-eeprom-config", "--out", bootconf_path],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except FileNotFoundError:
                    logger.warning(
                        "rpi-eeprom-config not found. EEPROM configuration check skipped. "
                        "Ensure POWER_OFF_ON_HALT=1 and WAKE_ON_GPIO=0 are set manually."
                    )
                    return
                except subprocess.CalledProcessError as e:
                    logger.warning("Failed to dump EEPROM config: %s", e)
                    return
                except PermissionError:
                    logger.warning(
                        "Permission denied accessing EEPROM. Run as root or ensure "
                        "POWER_OFF_ON_HALT=1 and WAKE_ON_GPIO=0 are set manually."
                    )
                    return

                # Read and parse configuration
                config_updated = False
                lines = []
                power_off_set = False
                wake_on_gpio_set = False

                try:
                    with open(bootconf_path, "r") as f:
                        for line in f:
                            line_stripped = line.strip()
                            # Skip comments and empty lines
                            if not line_stripped or line_stripped.startswith("#"):
                                lines.append(line)
                                continue

                            # Check POWER_OFF_ON_HALT
                            if line_stripped.startswith("POWER_OFF_ON_HALT="):
                                power_off_set = True
                                if line_stripped != "POWER_OFF_ON_HALT=1":
                                    logger.info("Updating POWER_OFF_ON_HALT from '%s' to 'POWER_OFF_ON_HALT=1'", line_stripped)
                                    lines.append("POWER_OFF_ON_HALT=1\n")
                                    config_updated = True
                                else:
                                    lines.append(line)
                            # Check WAKE_ON_GPIO
                            elif line_stripped.startswith("WAKE_ON_GPIO="):
                                wake_on_gpio_set = True
                                if line_stripped != "WAKE_ON_GPIO=0":
                                    logger.info("Updating WAKE_ON_GPIO from '%s' to 'WAKE_ON_GPIO=0'", line_stripped)
                                    lines.append("WAKE_ON_GPIO=0\n")
                                    config_updated = True
                                else:
                                    lines.append(line)
                            else:
                                lines.append(line)

                    # Add missing settings
                    if not power_off_set:
                        logger.info("Adding POWER_OFF_ON_HALT=1")
                        lines.append("POWER_OFF_ON_HALT=1\n")
                        config_updated = True

                    if not wake_on_gpio_set:
                        logger.info("Adding WAKE_ON_GPIO=0")
                        lines.append("WAKE_ON_GPIO=0\n")
                        config_updated = True

                    # Write updated configuration if needed
                    if config_updated:
                        with open(bootconf_path, "w") as f:
                            f.writelines(lines)

                        # Apply configuration
                        try:
                            subprocess.run(
                                ["rpi-eeprom-config", "--apply", bootconf_path],
                                check=True,
                                capture_output=True,
                                text=True,
                            )
                            logger.info("EEPROM configuration updated successfully. Reboot required for changes to take effect.")
                        except subprocess.CalledProcessError as e:
                            logger.error("Failed to apply EEPROM configuration: %s", e)
                        except PermissionError:
                            logger.warning(
                                "Permission denied applying EEPROM config. Run as root or "
                                "set POWER_OFF_ON_HALT=1 and WAKE_ON_GPIO=0 manually."
                            )
                    else:
                        logger.debug("EEPROM configuration already correct")

                except IOError as e:
                    logger.warning("Failed to read/write bootconf file: %s", e)

            finally:
                # Clean up temporary file
                try:
                    os.unlink(bootconf_path)
                except OSError:
                    pass

        except Exception as e:
            logger.warning("Unexpected error during EEPROM configuration check: %s", e)

    @property
    def rtc_datetime(self) -> datetime.datetime:
        """Get the current RTC datetime.

        Reads from RTC sysfs date and time files.

        Returns:
            Current datetime from RTC, converted to configured timezone.
        """
        date_path = pathlib.Path(f"{self.RTC_PATH}/date")
        time_path = pathlib.Path(f"{self.RTC_PATH}/time")

        # Read date (format: YYYY-MM-DD)
        date_str = date_path.read_text().strip()
        # Read time (format: HH:MM:SS)
        time_str = time_path.read_text().strip()

        # Parse date and time
        date_parts = date_str.split("-")
        time_parts = time_str.split(":")

        if len(date_parts) != 3 or len(time_parts) != 3:
            raise ValueError(f"Unexpected date/time format: {date_str} {time_str}")

        year = int(date_parts[0])
        month = int(date_parts[1])
        day = int(date_parts[2])
        hour = int(time_parts[0])
        minute = int(time_parts[1])
        second = int(time_parts[2])

        # Create datetime in UTC (RTC sysfs provides UTC time)
        rtc_dt = datetime.datetime(year, month, day, hour, minute, second, tzinfo=datetime.timezone.utc)

        # Convert to configured timezone
        return rtc_dt.astimezone(self._tz)

    def set_startup_datetime(self, ts: datetime.datetime | None):
        """Set the scheduled startup time (wake alarm).

        Configures when the Raspberry Pi 5 should wake from sleep. Pass None
        to disable the wake alarm.

        Args:
            ts: Datetime to wake up, or None to disable alarm.
                Will be converted to the configured timezone.

        Note:
            Logs a warning if the specified time is in the past.
            On Raspberry Pi 5, the alarm must be cleared before setting a new value.
        """
        wakealarm_path = pathlib.Path(self.RTC_WAKEALARM_PATH)

        if ts is None:
            # Clear alarm by writing 0
            try:
                wakealarm_path.write_text("0")
                logger.debug("Wake alarm cleared")
            except (IOError, OSError) as e:
                raise WittyPiException(f"Failed to clear wake alarm: {e}") from e
            return

        ts = ts.astimezone(self._tz)
        if ts < datetime.datetime.now(tz=self._tz):
            logger.warning("Wake time is in the past: %s", ts)

        # Convert to Unix timestamp (seconds since epoch)
        wake_timestamp = int(ts.timestamp())

        try:
            # Check current alarm value
            current_alarm = None
            try:
                current_alarm_str = wakealarm_path.read_text().strip()
                if current_alarm_str and current_alarm_str != "0":
                    current_alarm = int(current_alarm_str)
            except (IOError, OSError, ValueError):
                pass

            # Only update if the value is different
            if current_alarm == wake_timestamp:
                logger.debug("Wake alarm already set to %s (timestamp: %d)", ts, wake_timestamp)
                return

            # Clear existing alarm first (required on Raspberry Pi 5)
            try:
                wakealarm_path.write_text("0")
            except (IOError, OSError) as e:
                logger.warning("Failed to clear existing alarm before setting new one: %s", e)
                # Continue anyway - might work if alarm was already cleared

            # Set new alarm value
            wakealarm_path.write_text(str(wake_timestamp))
            logger.info("Wake alarm set for %s (timestamp: %d)", ts, wake_timestamp)
        except (IOError, OSError) as e:
            raise WittyPiException(f"Failed to set wake alarm: {e}") from e

    def get_startup_datetime(self) -> datetime.datetime | None:
        """Get the currently configured startup time (wake alarm).

        Returns:
            Datetime when next wake is scheduled, or None if alarm disabled.
        """
        wakealarm_path = pathlib.Path(self.RTC_WAKEALARM_PATH)

        try:
            alarm_value = wakealarm_path.read_text().strip()
            if not alarm_value or alarm_value == "0":
                return None

            wake_timestamp = int(alarm_value)
            return datetime.datetime.fromtimestamp(wake_timestamp, tz=self._tz)
        except (IOError, OSError, ValueError) as e:
            logger.debug("Failed to read wake alarm: %s", e)
            return None

    def set_shutdown_datetime(self, ts: datetime.datetime | None):
        """Set the scheduled shutdown time.

        Configures when the Raspberry Pi 5 should shut down. When the time
        arrives, the system will be halted (entering low-power mode if
        POWER_OFF_ON_HALT=1 is set).

        Args:
            ts: Datetime to shut down, or None to disable alarm.
                Will be converted to the configured timezone.

        Note:
            This stores the shutdown time but does not set a hardware alarm.
            The daemon should call this periodically and trigger shutdown
            when the time arrives.
        """
        if ts is None:
            self._shutdown_datetime = None
            return

        ts = ts.astimezone(self._tz)
        if ts < datetime.datetime.now(tz=self._tz):
            logger.warning("Shutdown time is in the past: %s", ts)

        self._shutdown_datetime = ts

    def get_shutdown_datetime(self) -> datetime.datetime | None:
        """Get the currently configured shutdown time.

        Returns:
            Datetime when next shutdown is scheduled, or None if alarm disabled.
        """
        return getattr(self, "_shutdown_datetime", None)

    def clear_flags(self):
        """Clear all alarm flags.

        For Raspberry Pi 5, this is a no-op as wake alarm flags are automatically
        managed by the kernel. Kept for interface compatibility.
        """
        pass

    @property
    def power_reset(self) -> int | None:
        """PMIC reset reason bitfield from device-tree, or None if unavailable."""
        return self._power_reset

    @property
    def power_reset_reasons(self) -> list[str]:
        """Decoded PMIC reset reason names for set bits in power_reset."""
        if self._power_reset is None:
            return []
        return [
            name for bit, name in POWER_RESET_BITS.items()
            if self._power_reset & (1 << bit)
        ]

    @property
    def pm_rsts(self) -> int | None:
        """PM_RSTS reset-reason register from device-tree, or None if unavailable."""
        return self._pm_rsts

    @property
    def pm_rsts_reasons(self) -> list[str]:
        """Decoded reset reason names for set bits in pm_rsts."""
        if self._pm_rsts is None:
            return []
        return [
            name for bit, name in PM_RSTS_BITS.items()
            if self._pm_rsts & (1 << bit)
        ]

    @property
    def max_current(self) -> int | None:
        """Negotiated PSU current limit in mA, or None if unavailable."""
        return self._max_current

    @property
    def usb_over_current_detected(self) -> bool | None:
        """Whether USB overcurrent was detected during boot, or None if unavailable."""
        return self._usb_over_current_detected

    def get_status(self) -> dict[str, int | str | bool | None]:
        """Get boot-time reset and power supply diagnostics.

        Returns:
            Dictionary containing pm_rsts, power_reset, decoded reasons,
            max_current, and usb_over_current_detected values.
        """
        return {
            "PM RSTs": self._pm_rsts,
            "PM RSTs Reasons": ", ".join(self.pm_rsts_reasons) or "none",
            "Power Reset": self._power_reset,
            "Power Reset Reasons": ", ".join(self.power_reset_reasons) or "none",
            "Max Current (mA)": self._max_current,
            "USB Overcurrent Detected": self._usb_over_current_detected,
        }

    @property
    def action_reason(self) -> ActionReason:
        """Get the reason for the current power state.

        Maps PMIC power_reset and PM_RSTS bits to ActionReason where possible.
        """
        if self._power_reset:
            if self._power_reset & (1 << 1):
                return ActionReason.LOW_VOLTAGE
            if self._power_reset & (1 << 2):
                return ActionReason.OVER_TEMPERATURE
            if self._power_reset & (1 << 4):
                return ActionReason.REBOOT

        if self._pm_rsts:
            if self._pm_rsts & ((1 << 5) | (1 << 4)):
                return ActionReason.REBOOT

        return ActionReason.REASON_NA

