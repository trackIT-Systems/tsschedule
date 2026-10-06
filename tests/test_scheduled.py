"""Tests for the tsscheduled daemon against an in-memory WittyPi 4 and a fake Raspberry Pi 5."""

import datetime
import io
import os
import pathlib
import zoneinfo

import pytest
import yaml
from conftest import UTC, fake_firmware_tick

from tsschedule import ActionReason, ScheduleConfiguration, scheduled
from tsschedule.backends import wittypi4
from tsschedule.backends.wittypi4 import WittyPi4

BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")

# schedule from wittypi4#9, as the daemon reads it
SCHEDULE_YML = """
button_delay: 00:10
force_on: false
lat: 54.091349
lon: 8.973584
schedule:
- {name: EnergySaver1a, start: 00:01, stop: 01:00}
- {name: EnergySaver2a, start: 02:00, stop: 03:00}
- {name: EnergySaver3a, start: 04:00, stop: 05:00}
- {name: EnergySaver4a, start: 06:00, stop: 07:00}
- {name: EnergySaver5a, start: 08:00, stop: 09:00}
- {name: EnergySaver6a, start: '10:00', stop: '11:00'}
- {name: EnergySaver7a, start: '12:00', stop: '13:00'}
- {name: EnergySaver8a, start: '14:00', stop: '15:00'}
- {name: EnergySaver9a, start: '16:00', stop: '17:00'}
- {name: EnergySaver10a, start: '18:00', stop: '19:00'}
- {name: EnergySaver11a, start: '20:00', stop: '21:00'}
- {name: EnergySaver12a, start: '22:00', stop: '23:00'}
"""

RECOVERY_YML = """
recovery_interval: 00:30
recovery_guard: 00:05
schedule:
- {name: day, start: '08:00', stop: '18:00'}
"""


@pytest.fixture
def shutdowns_called(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduled.os, "system", calls.append)
    return calls


@pytest.fixture
def daemon(wp, shutdowns_called):
    return scheduled.PowerManagerDaemon(wp, io.StringIO(SCHEDULE_YML))


@pytest.fixture
def sc(local_tz):
    return ScheduleConfiguration(yaml.safe_load(SCHEDULE_YML))


def expected_events(sc, start, end):
    """Window starts and stops of the schedule between start and end."""
    starts = sorted(
        {e.next_start(t) for e in sc.entries for t in (start, start + datetime.timedelta(days=1)) if e.next_start(t) < end}
    )
    stops = {e.next_stop(t) for e in sc.entries for t in starts if e.next_start(t) == t and e.next_stop(t) < end}
    return starts, stops


# Simulations


def test_wittypi4_follows_schedule_across_midnight(daemon, bus, sc):
    """Run daemon and firmware for two days; every window must be powered, nothing else (wittypi4#9)."""
    start = datetime.datetime(2025, 12, 8, 20, 0, tzinfo=UTC)  # 21:00 in Berlin
    end = start + datetime.timedelta(days=2)
    boots, shutdowns = [], []
    powered = True

    bus.now = start
    while bus.now < end:
        event = fake_firmware_tick(bus)
        if event == "startup" and not powered:
            powered = True
            bus.reg[wittypi4.I2C_ACTION_REASON] = ActionReason.ALARM_STARTUP.value
            boots.append(daemon._device.rtc_datetime)
        elif event == "shutdown" and powered:
            powered = False
            daemon._set_termination_alarms(sc)
            shutdowns.append(daemon._device.rtc_datetime)

        # daemon loop, every 60s; after the firmware, as it isn't aligned to the minute on a real system
        if powered and bus.now.second == 0 and daemon._update_alarms(sc, daemon._device.rtc_datetime):
            powered = False
            daemon._set_termination_alarms(sc)
            shutdowns.append(daemon._device.rtc_datetime)

        bus.now += datetime.timedelta(seconds=1)

    starts, stops = expected_events(sc, start, end)
    assert boots == starts
    # booted outside of the schedule at 21:00, so the first shutdown is immediate
    assert shutdowns[0] == start
    assert set(shutdowns[1:]) == stops


def test_raspberrypi5_follows_schedule_across_midnight(pi5_root, pi5, sc, shutdowns_called):
    """Same as for the WittyPi 4; the Pi 5 RTC reads UTC and the daemon triggers the shutdowns itself."""
    daemon = scheduled.PowerManagerDaemon(pi5, io.StringIO(SCHEDULE_YML))
    start = datetime.datetime(2025, 12, 8, 20, 0, tzinfo=UTC)  # 21:00 in Berlin
    end = start + datetime.timedelta(days=2)
    boots, shutdowns = [], []

    now = start
    while now < end:
        # powered: daemon loop every 60s
        pi5_root.set_rtc(now)
        if daemon._update_alarms(sc, pi5.rtc_datetime):
            daemon._set_termination_alarms(sc)
            shutdowns.append(now)

            # powered off: sleep until the wake alarm
            wakealarm = int(pi5_root.wakealarm)
            assert wakealarm, f"no wake alarm set at {now}"
            now = datetime.datetime.fromtimestamp(wakealarm, UTC)
            if now < end:
                boots.append(now)
            continue
        now += datetime.timedelta(seconds=60)

    starts, stops = expected_events(sc, start, end)
    assert boots == starts
    # booted outside of the schedule at 21:00, so the first shutdown is immediate
    assert shutdowns[0] == start
    assert set(shutdowns[1:]) == stops


def test_recovery_after_brownout(bus, local_tz, shutdowns_called):
    """A power loss during an on-time is recovered on the next grid point, without the termination alarms."""
    daemon = scheduled.PowerManagerDaemon(WittyPi4(bus), io.StringIO(RECOVERY_YML))
    sc = ScheduleConfiguration(yaml.safe_load(RECOVERY_YML))
    bus.now = datetime.datetime(2025, 12, 8, 9, 10, tzinfo=UTC)  # 10:10 in Berlin, on
    daemon._update_alarms(sc, daemon._device.rtc_datetime)

    # brownout: power is lost, the firmware keeps running from the RTC battery
    boot = None
    while boot is None:
        bus.now += datetime.timedelta(seconds=1)
        if fake_firmware_tick(bus) == "startup":
            boot = daemon._device.rtc_datetime

    assert boot == datetime.datetime(2025, 12, 8, 10, 30, tzinfo=BERLIN)


def test_no_recovery_wake_during_off_time(daemon, bus, local_tz):
    sc = ScheduleConfiguration(yaml.safe_load(RECOVERY_YML))
    bus.now = datetime.datetime(2025, 12, 8, 16, 50, tzinfo=UTC)  # 17:50 in Berlin, the last grid point is inside the guard

    daemon._update_alarms(sc, daemon._device.rtc_datetime)

    assert daemon._device.get_startup_datetime() == datetime.datetime(2025, 12, 9, 8, 0, tzinfo=BERLIN)


# Loop steps


def test_update_alarms_when_active(daemon, bus, sc, shutdowns_called):
    bus.now = datetime.datetime(2025, 12, 8, 23, 30, tzinfo=UTC)  # 00:30 in Berlin
    now = daemon._device.rtc_datetime

    assert not daemon._update_alarms(sc, now)

    assert daemon._device.get_shutdown_datetime() == sc.next_shutdown(now)
    assert daemon._device.get_startup_datetime() == sc.next_startup(now)
    assert shutdowns_called == []


def test_update_alarms_when_inactive(daemon, bus, sc, shutdowns_called):
    """Outside of the schedule, next_shutdown() is now, so the daemon shuts down right away."""
    bus.now = datetime.datetime(2025, 12, 8, 23, 30, 15, tzinfo=UTC) - datetime.timedelta(hours=1)  # 23:30 Berlin
    now = daemon._device.rtc_datetime

    assert daemon._update_alarms(sc, now)

    assert daemon._device.get_startup_datetime() == sc.next_startup(now)
    assert shutdowns_called == ["shutdown 0"]


def test_update_alarms_shuts_down_when_shutdown_time_arrived(daemon, bus, sc, shutdowns_called):
    bus.now = datetime.datetime(2025, 12, 9, 0, 0, tzinfo=UTC)  # 01:00 in Berlin, end of a window

    assert daemon._update_alarms(sc, daemon._device.rtc_datetime)
    assert shutdowns_called == ["shutdown 0"]


@pytest.mark.parametrize(
    ("reason", "shutdown"),
    [
        (ActionReason.ALARM_SHUTDOWN, True),
        (ActionReason.LOW_VOLTAGE, True),
        (ActionReason.OVER_TEMPERATURE, True),
        (ActionReason.ALARM_STARTUP, False),
        (ActionReason.BUTTON_CLICK, False),
    ],
)
def test_update_alarms_after_shutdown_alarm(daemon, bus, sc, shutdowns_called, reason, shutdown):
    bus.now = datetime.datetime(2025, 12, 8, 23, 30, tzinfo=UTC)  # active
    bus.reg[wittypi4.I2C_ACTION_REASON] = reason.value

    assert daemon._update_alarms(sc, daemon._device.rtc_datetime) == shutdown
    assert shutdowns_called == (["shutdown 0"] if shutdown else [])


def test_termination_sets_next_startup_from_rtc(daemon, bus, sc):
    bus.now = datetime.datetime(2025, 12, 8, 22, 0, 30, tzinfo=UTC)  # 23:00:30 in Berlin

    daemon._set_termination_alarms(sc)

    assert daemon._device.get_shutdown_datetime() is None
    assert daemon._device.get_startup_datetime() == datetime.datetime(2025, 12, 8, 23, 1, tzinfo=UTC)


# Startup checks


def test_run_exits_on_implausible_rtc(daemon, monkeypatch):
    monkeypatch.setattr(scheduled, "last_known_time", lambda: datetime.datetime(2030, 1, 1, tzinfo=UTC))
    with pytest.raises(SystemExit) as exc:
        daemon.run()
    assert exc.value.code == 3


def test_run_exits_on_rtc_sysclock_mismatch(daemon, monkeypatch):
    monkeypatch.setattr(scheduled, "last_known_time", lambda: datetime.datetime(2020, 1, 1, tzinfo=UTC))
    with pytest.raises(SystemExit) as exc:
        daemon.run()  # the fake RTC is in 2025
    assert exc.value.code == 3


def test_run_exits_without_clock_sources(daemon, root):
    with pytest.raises(SystemExit) as exc:
        daemon.run()
    assert exc.value.code == 3


def test_run_configures_wittypi(daemon, bus, monkeypatch):
    monkeypatch.setattr(scheduled, "last_known_time", lambda: datetime.datetime(2030, 1, 1, tzinfo=UTC))
    with pytest.raises(SystemExit):
        daemon.run()

    assert bus.reg[wittypi4.I2C_CONF_DEFAULT_ON] == 1
    assert bus.reg[wittypi4.I2C_CONF_DEFAULT_ON_DELAY] == 1
    assert bus.reg[wittypi4.I2C_CONF_POWER_CUT_DELAY] == 250


# Clock sources


@pytest.fixture
def root(tmp_path, monkeypatch):
    """Redirect the daemon's clock sources into tmp_path."""
    for name in ("FAKE_HWCLOCK_PATH", "TIMESYNC_CLOCK_PATH", "CHRONY_DRIFT_PATH"):
        path = getattr(scheduled, name)
        monkeypatch.setattr(scheduled, name, tmp_path / path.relative_to("/"))
    return tmp_path


def touch(path: pathlib.Path, ts: datetime.datetime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    os.utime(path, (ts.timestamp(), ts.timestamp()))


def test_fake_hwclock(root):
    (root / "etc").mkdir()
    (root / "etc/fake-hwclock.data").write_text("2025-12-08 21:30:00\n")

    assert scheduled.fake_hwclock() == datetime.datetime(2025, 12, 8, 21, 30, tzinfo=UTC)


def test_last_known_time_is_most_recent(root):
    (root / "etc").mkdir()
    (root / "etc/fake-hwclock.data").write_text("2025-12-08 21:30:00\n")
    timesync = datetime.datetime(2025, 12, 9, 6, 0, tzinfo=UTC)
    touch(root / "var/lib/systemd/timesync/clock", timesync)
    touch(root / "var/lib/chrony/chrony.drift", datetime.datetime(2025, 12, 1, tzinfo=UTC))

    assert scheduled.last_known_time() == timesync


def test_last_known_time_skips_malformed_fake_hwclock(root):
    (root / "etc").mkdir()
    (root / "etc/fake-hwclock.data").write_text("garbage")
    timesync = datetime.datetime(2025, 12, 9, 6, 0, tzinfo=UTC)
    touch(root / "var/lib/systemd/timesync/clock", timesync)

    assert scheduled.last_known_time() == timesync


def test_last_known_time_without_sources(root):
    with pytest.raises(RuntimeError):
        scheduled.last_known_time()
