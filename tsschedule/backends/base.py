"""Base class for power management hardware backends.

This module provides the abstract base class for power management hardware
with RTC and alarm capabilities.
"""

import datetime
import logging

from .. import ActionReason

logger = logging.getLogger(__name__)


class PowerManager:
    """Base class for power management hardware with RTC and alarm capabilities.

    This abstract base class defines the common interface for power management
    devices that support RTC operations and wake/sleep scheduling. Subclasses
    implement platform-specific functionality.

    Common interface:
    - RTC datetime read/write
    - Startup/shutdown alarm configuration
    - Action reason detection
    - RTC/system clock synchronization checking
    """

    def __init__(self, tz=datetime.UTC):
        """Initialize power manager.

        Args:
            tz: Timezone for RTC operations (default: UTC)
        """
        self._tz = tz
        # alarms whose time is in the past, so the warning is logged once per occurrence
        self._alarms_in_past: set[str] = set()

    def _check_in_past(self, alarm: str, ts: datetime.datetime | None, now: datetime.datetime | None = None):
        """Warn when an alarm time is in the past, once until it is not anymore.

        The daemon sets the alarms every minute; repeats are logged at DEBUG.

        Args:
            alarm: Name of the alarm, as it starts the message (e.g. "Startup")
            ts: Alarm time, or None if the alarm is cleared
            now: Current time to compare against; not needed when ts is None
        """
        if ts is not None and now is not None and ts < now:
            if alarm in self._alarms_in_past:
                logger.debug("%s time is still in the past: %s", alarm, ts)
            else:
                logger.warning("%s time is in the past: %s", alarm, ts)
                self._alarms_in_past.add(alarm)
        elif alarm in self._alarms_in_past:
            if ts is None:
                logger.info("%s alarm cleared, its time is no longer in the past", alarm)
            else:
                logger.info("%s time is no longer in the past: %s", alarm, ts)
            self._alarms_in_past.discard(alarm)

    @property
    def rtc_datetime(self) -> datetime.datetime:
        """Get the current RTC datetime.

        Returns:
            Current datetime from RTC, converted to configured timezone.
        """
        raise NotImplementedError

    @rtc_datetime.setter
    def rtc_datetime(self, value: datetime.datetime):
        """Set the RTC datetime.

        Args:
            value: Datetime to set (will be converted to configured timezone).
        """
        raise NotImplementedError

    def set_startup_datetime(self, ts: datetime.datetime | None):
        """Set the scheduled startup time.

        Args:
            ts: Datetime to power on, or None to disable alarm.
        """
        raise NotImplementedError

    def get_startup_datetime(self) -> datetime.datetime | None:
        """Get the currently configured startup time.

        Returns:
            Datetime when next startup is scheduled, or None if alarm disabled.
        """
        raise NotImplementedError

    def set_shutdown_datetime(self, ts: datetime.datetime | None):
        """Set the scheduled shutdown time.

        Args:
            ts: Datetime to shut down, or None to disable alarm.
        """
        raise NotImplementedError

    def get_shutdown_datetime(self) -> datetime.datetime | None:
        """Get the currently configured shutdown time.

        Returns:
            Datetime when next shutdown is scheduled, or None if alarm disabled.
        """
        raise NotImplementedError

    def clear_flags(self):
        """Clear all alarm flags.

        Should be called after boot to acknowledge alarm triggers.
        """
        raise NotImplementedError

    @property
    def action_reason(self) -> ActionReason:
        """Get the reason for the current power state.

        Returns:
            ActionReason enum indicating why the system was powered on.
        """
        raise NotImplementedError

    def rtc_sysclock_match(self, threshold=datetime.timedelta(seconds=2)) -> bool:
        """Check if RTC time matches system clock within threshold.

        Args:
            threshold: Maximum allowed time difference (default: 2 seconds)

        Returns:
            True if RTC and system clock are within threshold, False otherwise.
        """
        return abs(self.rtc_datetime - datetime.datetime.now(tz=self._tz)) < threshold

