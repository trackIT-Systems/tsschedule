"""Tests for the Raspberry Pi 5 backend against fake sysfs and device-tree files."""

import datetime
import zoneinfo

import pytest
from conftest import UTC

from tsschedule import ActionReason
from tsschedule.backends.raspberrypi5 import RaspberryPi5
from tsschedule.backends.wittypi4 import WittyPiException

BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")


def test_requires_wakealarm(pi5_root):
    (pi5_root.rtc / "wakealarm").unlink()
    with pytest.raises(WittyPiException, match="wakealarm"):
        RaspberryPi5(check_eeprom=False)


# RTC


def test_rtc_datetime_is_utc(pi5, pi5_root):
    pi5_root.set_rtc(datetime.datetime(2025, 12, 8, 23, 30, 15, tzinfo=UTC))
    assert pi5.rtc_datetime == datetime.datetime(2025, 12, 8, 23, 30, 15, tzinfo=UTC)
    assert pi5.rtc_datetime.utcoffset() == datetime.timedelta(0)


def test_rtc_datetime_rejects_garbage(pi5, pi5_root):
    (pi5_root.rtc / "date").write_text("garbage\n")
    with pytest.raises(ValueError):
        pi5.rtc_datetime


# Wake alarm


def test_startup_roundtrip(pi5, pi5_root):
    ts = datetime.datetime(2025, 12, 9, 0, 1, tzinfo=BERLIN)
    pi5.set_startup_datetime(ts)

    assert pi5_root.wakealarm == str(int(ts.timestamp()))
    assert pi5.get_startup_datetime() == ts


def test_startup_clear(pi5, pi5_root):
    pi5.set_startup_datetime(datetime.datetime(2025, 12, 9, 0, 1, tzinfo=BERLIN))
    pi5.set_startup_datetime(None)

    assert pi5_root.wakealarm == "0"
    assert pi5.get_startup_datetime() is None


def test_startup_not_rewritten_when_unchanged(pi5, pi5_root, monkeypatch):
    ts = datetime.datetime(2025, 12, 9, 0, 1, tzinfo=BERLIN)
    pi5.set_startup_datetime(ts)

    writes = []
    monkeypatch.setattr(type(pi5_root.rtc / "wakealarm"), "write_text", lambda self, data: writes.append(data))
    pi5.set_startup_datetime(ts)

    assert writes == []


def test_startup_cleared_before_rewrite(pi5, pi5_root, monkeypatch):
    pi5.set_startup_datetime(datetime.datetime(2025, 12, 9, 0, 1, tzinfo=BERLIN))

    path_type = type(pi5_root.rtc / "wakealarm")
    write_text = path_type.write_text
    writes = []

    def record(self, data):
        writes.append(data)
        return write_text(self, data)

    monkeypatch.setattr(path_type, "write_text", record)
    ts = datetime.datetime(2025, 12, 9, 2, 0, tzinfo=BERLIN)
    pi5.set_startup_datetime(ts)

    assert writes == ["0", str(int(ts.timestamp()))]


@pytest.mark.parametrize("content", ["", "0", "garbage"])
def test_startup_unset(pi5, pi5_root, content):
    (pi5_root.rtc / "wakealarm").write_text(content)
    assert pi5.get_startup_datetime() is None


def test_shutdown_is_kept_in_software(pi5, pi5_root):
    ts = datetime.datetime(2025, 12, 9, 1, 0, tzinfo=BERLIN)
    pi5.set_shutdown_datetime(ts)
    assert pi5.get_shutdown_datetime() == ts
    assert pi5_root.wakealarm == "0"

    pi5.set_shutdown_datetime(None)
    assert pi5.get_shutdown_datetime() is None


# Diagnostics


def test_diagnostics_missing(pi5):
    assert pi5.pm_rsts is None
    assert pi5.power_reset is None
    assert pi5.max_current is None
    assert pi5.usb_over_current_detected is None
    assert pi5.action_reason is ActionReason.REASON_NA
    assert pi5.get_status()["PM RSTs Reasons"] == "none"


def test_diagnostics(pi5_root):
    pi5_root.set_dt_u32("rsts", 1 << 12 | 1 << 5)
    pi5_root.set_dt_u32("power_reset", 1 << 1)
    pi5_root.set_dt_u32("max_current", 5000)
    pi5_root.set_dt_u32("usb_over_current_detected", 1)

    pi5 = RaspberryPi5(check_eeprom=False)

    assert pi5.pm_rsts_reasons == ["power_on_reset", "watchdog_full_reset"]
    assert pi5.power_reset_reasons == ["under_voltage"]
    assert pi5.max_current == 5000
    assert pi5.usb_over_current_detected is True
    assert pi5.get_status()["Power Reset Reasons"] == "under_voltage"


def test_diagnostics_truncated_property(pi5_root):
    (pi5_root.power / "max_current").write_bytes(b"\x00\x01")
    assert RaspberryPi5(check_eeprom=False).max_current is None


@pytest.mark.parametrize(
    ("power_reset", "rsts", "reason"),
    [
        (None, None, ActionReason.REASON_NA),
        (1 << 1, None, ActionReason.LOW_VOLTAGE),
        (1 << 2, None, ActionReason.OVER_TEMPERATURE),
        (1 << 4, None, ActionReason.REBOOT),
        (1 << 0, None, ActionReason.REASON_NA),  # over_voltage has no action reason
        (None, 1 << 4, ActionReason.REBOOT),
        (None, 1 << 5, ActionReason.REBOOT),
        (None, 1 << 12, ActionReason.REASON_NA),  # power on
        (1 << 1, 1 << 5, ActionReason.LOW_VOLTAGE),  # the PMIC reason wins
    ],
)
def test_action_reason(pi5_root, power_reset, rsts, reason):
    if power_reset is not None:
        pi5_root.set_dt_u32("power_reset", power_reset)
    if rsts is not None:
        pi5_root.set_dt_u32("rsts", rsts)

    assert RaspberryPi5(check_eeprom=False).action_reason is reason
