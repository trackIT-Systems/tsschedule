"""Tests for the WittyPi 4 backend against an in-memory bus."""

import datetime
import time
import zoneinfo

import pytest
from conftest import ALARM1_BASE, UTC, FakeBus

from tsschedule import ALARM_RESET, ActionReason, bcd2bin, bin2bcd
from tsschedule.backends import wittypi4
from tsschedule.backends.wittypi4 import WittyPi4, WittyPiException


def set_system_tz(monkeypatch, name):
    monkeypatch.setenv("TZ", name)
    time.tzset()


# Probe


def test_probe_rejects_unknown_firmware(bus):
    bus.reg[wittypi4.I2C_ID] = 0x37  # Witty Pi 4 L3V7, different register map
    with pytest.raises(WittyPiException, match="0x37"):
        WittyPi4(bus)


def test_probe_reports_missing_device(bus):
    class NoDevice(FakeBus):
        def read_byte_data(self, addr, reg):
            raise OSError(121, "Remote I/O error")

    with pytest.raises(WittyPiException, match="check device connection"):
        WittyPi4(NoDevice(bus.now))


# Alarms


ALARM_CASES = {
    # (RTC time in UTC, alarm in local time)
    "after-local-midnight": (
        datetime.datetime(2025, 12, 8, 21, 0, tzinfo=UTC),
        datetime.datetime(2025, 12, 9, 0, 1, 30),
    ),
    "month-end": (
        datetime.datetime(2026, 1, 31, 12, 0, tzinfo=UTC),
        datetime.datetime(2026, 2, 1, 0, 1),
    ),
    "year-end": (
        datetime.datetime(2025, 12, 31, 22, 0, tzinfo=UTC),
        datetime.datetime(2026, 1, 1, 1, 30),
    ),
    "summer": (
        datetime.datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        datetime.datetime(2026, 7, 2, 0, 30),
    ),
}

ALARMS = {
    "startup": ("set_startup_datetime", "get_startup_datetime", ALARM1_BASE),
    "shutdown": ("set_shutdown_datetime", "get_shutdown_datetime", wittypi4.I2C_CONF_SECOND_ALARM2),
}


@pytest.mark.parametrize("tzname", ["Europe/Berlin", "UTC", "America/New_York"])
@pytest.mark.parametrize("case", ALARM_CASES.values(), ids=ALARM_CASES.keys())
@pytest.mark.parametrize("alarm", ALARMS.values(), ids=ALARMS.keys())
def test_alarm_roundtrip(wp, bus, monkeypatch, tzname, case, alarm):
    setter, getter, _ = alarm
    set_system_tz(monkeypatch, tzname)
    bus.now, ts = case
    ts = ts.replace(tzinfo=zoneinfo.ZoneInfo(tzname))

    getattr(wp, setter)(ts)

    assert getattr(wp, getter)() == ts


def test_alarm_registers_are_utc(wp, bus):
    wp.set_startup_datetime(datetime.datetime(2025, 12, 9, 0, 1, 30, tzinfo=zoneinfo.ZoneInfo("Europe/Berlin")))

    second, minute, hour, day, weekday = (bcd2bin(bus.reg[ALARM1_BASE + i]) for i in range(5))
    assert (day, hour, minute, second) == (8, 23, 1, 30)
    assert weekday == ALARM_RESET


@pytest.mark.parametrize("alarm", ALARMS.values(), ids=ALARMS.keys())
def test_alarm_reset(wp, bus, alarm):
    setter, getter, base = alarm
    getattr(wp, setter)(bus.now + datetime.timedelta(hours=1))
    getattr(wp, setter)(None)

    assert [bcd2bin(bus.reg[base + i]) for i in range(5)] == [ALARM_RESET] * 5
    assert getattr(wp, getter)() is None


def test_alarm_day_zero_is_unset(wp, bus):
    bus.reg[ALARM1_BASE + 3] = 0
    assert wp.get_startup_datetime() is None


@pytest.mark.parametrize("alarm", ALARMS.values(), ids=ALARMS.keys())
def test_alarm_at_now_is_not_in_the_future(wp, bus, alarm):
    """The daemon shuts down once get_shutdown_datetime() <= now, so an alarm at now must read back as now."""
    setter, getter, _ = alarm
    getattr(wp, setter)(bus.now)
    assert getattr(wp, getter)() == bus.now


# RTC


def test_rtc_datetime_is_local(wp, bus):
    rtc = wp.rtc_datetime
    assert rtc == bus.now
    assert rtc.utcoffset() == datetime.timedelta(hours=1)  # Berlin, winter


def test_rtc_datetime_setter_writes_utc(wp, bus):
    wp.rtc_datetime = datetime.datetime(2026, 1, 1, 0, 30, 15, tzinfo=zoneinfo.ZoneInfo("Europe/Berlin"))

    written = {reg: bcd2bin(value) for reg, value in bus.rtc_writes.items()}
    assert written == {
        wittypi4.I2C_RTC_YEARS: 25,
        wittypi4.I2C_RTC_MONTHS: 12,
        wittypi4.I2C_RTC_WEEKDAYS: 2,  # Wednesday, as datetime.weekday()
        wittypi4.I2C_RTC_DAYS: 31,
        wittypi4.I2C_RTC_HOURS: 23,
        wittypi4.I2C_RTC_MINUTES: 30,
        wittypi4.I2C_RTC_SECONDS: 15,
    }


def test_rtc_sysclock_match(wp, bus):
    bus.now = datetime.datetime.now(UTC).replace(microsecond=0)
    assert wp.rtc_sysclock_match(threshold=datetime.timedelta(seconds=5))

    bus.now -= datetime.timedelta(seconds=30)
    assert not wp.rtc_sysclock_match()


def test_clear_flags(wp, bus):
    bus.reg[wittypi4.I2C_RTC_CTRL2] = 0xFF
    bus.reg[wittypi4.I2C_CONF_FLAG_ALARM1] = 1
    bus.reg[wittypi4.I2C_CONF_FLAG_ALARM2] = 1

    wp.clear_flags()

    assert bus.reg[wittypi4.I2C_RTC_CTRL2] == 0b10111111  # only the alarm flag (AF)
    assert bus.reg[wittypi4.I2C_CONF_FLAG_ALARM1] == 0
    assert bus.reg[wittypi4.I2C_CONF_FLAG_ALARM2] == 0


# Conversions


def test_bcd_roundtrip():
    for value in range(100):
        assert bcd2bin(bin2bcd(value)) == value
    assert bin2bcd(59) == 0x59
    assert bcd2bin(0x23) == 23


@pytest.mark.xfail(strict=True, reason="adjustments are truncated instead of rounded")
@pytest.mark.parametrize("prop", ["adj_vin", "adj_vout", "adj_iout"])
def test_adjustment_roundtrip(wp, prop):
    for centi in range(-127, 128):
        setattr(wp, prop, centi / 100)
        assert getattr(wp, prop) == pytest.approx(centi / 100), centi


def test_adjustment_encoding(wp, bus):
    # same encoding as UUGear's wittyPi.sh: negative values are stored as 255 + value
    wp.adj_vin = -0.2
    assert bus.reg[wittypi4.I2C_CONF_ADJ_VIN] == 235
    wp.adj_vin = 0.2
    assert bus.reg[wittypi4.I2C_CONF_ADJ_VIN] == 20


@pytest.mark.parametrize("prop", ["lv_threshold", "recovery_voltage"])
def test_voltage_threshold(wp, bus, prop):
    wp_reg = {"lv_threshold": wittypi4.I2C_CONF_LOW_VOLTAGE, "recovery_voltage": wittypi4.I2C_CONF_RECOVERY_VOLTAGE}[prop]

    setattr(wp, prop, 0.0)  # disabled
    assert bus.reg[wp_reg] == 255
    assert getattr(wp, prop) == 0.0

    for deci in range(1, 255):
        setattr(wp, prop, deci / 10)
        assert getattr(wp, prop) == pytest.approx(deci / 10), deci


@pytest.mark.parametrize(("value", "expected"), [(2.5, 2.5), (-1, 0.0), (30, 25.0)])
def test_power_cut_delay_is_clamped(wp, value, expected):
    wp.power_cut_delay = value
    assert wp.power_cut_delay == expected


@pytest.mark.parametrize("prop", ["below_temperature_threshold", "over_temperature_threshold"])
@pytest.mark.parametrize("celsius", [-30, -1, 0, 25, 80])
def test_temperature_threshold(wp, prop, celsius):
    setattr(wp, prop, celsius)
    assert getattr(wp, prop) == celsius


@pytest.mark.parametrize(("msb", "lsb", "celsius"), [(0x19, 0x00, 25.0), (0x00, 0x80, 0.5), (0xFF, 0x80, -0.5)])
def test_lm75b_temperature(wp, bus, msb, lsb, celsius):
    # SMBus words are little endian, the LM75B sends MSB first
    bus.reg[wittypi4.I2C_LM75B_TEMPERATURE] = msb | (lsb << 8)
    assert wp.lm75b_temperature == celsius


def test_status_and_config_dump(wp, bus):
    bus.reg[wittypi4.I2C_VOLTAGE_IN_I] = 12
    bus.reg[wittypi4.I2C_VOLTAGE_IN_D] = 34

    assert wp.get_status()["Input Voltage (V)"] == pytest.approx(12.34)
    config = wp.dump_config()
    assert config["firmware_id"] == 0x26
    assert config["voltage_in"] == pytest.approx(12.34)


# Action reason


@pytest.mark.parametrize("reason", list(ActionReason))
def test_action_reason_known(wp, bus, reason):
    bus.reg[wittypi4.I2C_ACTION_REASON] = reason.value
    assert wp.action_reason is reason


@pytest.mark.parametrize("value", [0x09, 0x0D, 0xFF])
def test_action_reason_unknown(wp, bus, value):
    bus.reg[wittypi4.I2C_ACTION_REASON] = value
    assert wp.action_reason is ActionReason.REASON_NA
