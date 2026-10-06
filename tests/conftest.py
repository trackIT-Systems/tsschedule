"""Shared fixtures: an in-memory WittyPi 4, a fake Raspberry Pi 5 and a model of the WittyPi firmware's alarms."""

import datetime
import struct
import time

import pytest

import tsschedule
from tsschedule import ALARM_RESET, bcd2bin, bin2bcd
from tsschedule.backends import raspberrypi5, wittypi4
from tsschedule.backends.raspberrypi5 import RaspberryPi5
from tsschedule.backends.wittypi4 import WittyPi4

UTC = datetime.UTC

ALARM1_BASE = wittypi4.I2C_CONF_SECOND_ALARM1
ALARM2_BASE = wittypi4.I2C_CONF_SECOND_ALARM2

_RTC_FIELDS = {
    wittypi4.I2C_RTC_SECONDS: "second",
    wittypi4.I2C_RTC_MINUTES: "minute",
    wittypi4.I2C_RTC_HOURS: "hour",
    wittypi4.I2C_RTC_DAYS: "day",
    wittypi4.I2C_RTC_MONTHS: "month",
}


class FakeBus:
    """smbus2.SMBus stand-in: plain registers plus an RTC that runs in UTC.

    The RTC reads from `now`; writes to RTC registers are recorded in `rtc_writes`.
    """

    def __init__(self, now: datetime.datetime):
        self.now = now.astimezone(UTC)
        self.reg = {wittypi4.I2C_ID: 0x26, wittypi4.I2C_FW_REVISION: 0x01}
        self.rtc_writes = {}
        for base in (ALARM1_BASE, ALARM2_BASE):
            for offset in range(5):
                self.reg[base + offset] = bin2bcd(ALARM_RESET)

    def read_byte_data(self, addr, reg):
        if reg in _RTC_FIELDS:
            return bin2bcd(getattr(self.now, _RTC_FIELDS[reg]))
        if reg == wittypi4.I2C_RTC_YEARS:
            return bin2bcd(self.now.year - 2000)
        return self.reg.get(reg, 0)

    def write_byte_data(self, addr, reg, value):
        if wittypi4.I2C_RTC_SECONDS <= reg <= wittypi4.I2C_RTC_YEARS:
            self.rtc_writes[reg] = int(value)
        else:
            self.reg[reg] = int(value)

    def read_word_data(self, addr, reg):
        return self.reg.get(reg, 0)

    def close(self):
        pass


def _fw_timestamp(day, hour, minute, second):
    # firmware's getTimestamp(): only the day of month, no month or year
    return day * 86400 + hour * 3600 + minute * 60 + second


def fake_firmware_tick(bus: FakeBus) -> str | None:
    """One watchdog tick of the firmware's processAlarmIfNeeded().

    Returns "startup" or "shutdown" if an alarm triggers at bus.now.
    """
    now = bus.now
    cur = _fw_timestamp(now.day, now.hour, now.minute, now.second)
    for base, name in ((ALARM1_BASE, "startup"), (ALARM2_BASE, "shutdown")):
        second, minute, hour, day = (bcd2bin(bus.reg[base + i]) for i in range(4))
        if ALARM_RESET in (second, minute, hour, day):
            continue
        if 0 <= cur - _fw_timestamp(day, hour, minute, second) < 2:
            return name
    return None


class FakePi5Root:
    """Files of a Raspberry Pi 5 below tmp_path: the RTC in sysfs and the device-tree diagnostics."""

    def __init__(self, root):
        self.rtc = root / "sys/class/rtc/rtc0"
        self.power = root / "proc/device-tree/chosen/power"
        self.rsts = root / "proc/device-tree/chosen/bootloader/rsts"
        self.rtc.mkdir(parents=True)
        self.power.mkdir(parents=True)
        self.rsts.parent.mkdir(parents=True)
        (self.rtc / "wakealarm").write_text("0")

    @property
    def wakealarm(self) -> str:
        return (self.rtc / "wakealarm").read_text()

    def set_rtc(self, now: datetime.datetime):
        now = now.astimezone(UTC)
        (self.rtc / "date").write_text(now.strftime("%Y-%m-%d\n"))
        (self.rtc / "time").write_text(now.strftime("%H:%M:%S\n"))

    def set_dt_u32(self, name: str, value: int):
        path = self.rsts if name == "rsts" else self.power / name
        path.write_bytes(struct.pack(">I", value))


@pytest.fixture(autouse=True)
def no_geolocation(monkeypatch):
    """Never read the host's /etc/geolocation; tests opt in by patching `_parse_geolocation_file` again."""
    monkeypatch.setattr(tsschedule, "_parse_geolocation_file", lambda path="/etc/geolocation": None)


@pytest.fixture
def local_tz(monkeypatch):
    """Run in Europe/Berlin, so local and RTC (UTC) days differ around midnight."""
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def bus(local_tz):
    return FakeBus(datetime.datetime(2025, 12, 8, 12, 0, tzinfo=UTC))


@pytest.fixture
def wp(bus):
    return WittyPi4(bus)


@pytest.fixture
def pi5_root(tmp_path, monkeypatch, local_tz):
    root = FakePi5Root(tmp_path)
    root.set_rtc(datetime.datetime(2025, 12, 8, 12, 0, tzinfo=UTC))
    monkeypatch.setattr(RaspberryPi5, "RTC_PATH", str(root.rtc))
    monkeypatch.setattr(RaspberryPi5, "RTC_WAKEALARM_PATH", str(root.rtc / "wakealarm"))
    monkeypatch.setattr(raspberrypi5, "POWER_DT_PATH", root.power)
    monkeypatch.setattr(raspberrypi5, "BOOTLOADER_RSTS_PATH", root.rsts)
    return root


@pytest.fixture
def pi5(pi5_root):
    return RaspberryPi5(check_eeprom=False)
