"""Tests for ScheduleConfiguration and ButtonEntry.

The RTC runs in UTC while schedules are configured in local time. In Europe/Berlin,
a window like 00:01-01:00 local lies on the previous UTC day, so any evaluation that
takes the calendar date from a UTC timestamp picks the wrong day (wittypi4#9).
"""

import datetime
import zoneinfo

import astral
import astral.sun
import pytest
from conftest import UTC

import tsschedule
from tsschedule import ButtonEntry, ScheduleConfiguration, _parse_geolocation_file

BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")

# configuration from wittypi4#9
SCHEDULE = [{"name": f"EnergySaver{h // 2 + 1}a", "start": f"{h:02}:00", "stop": f"{h + 1:02}:00"} for h in range(2, 24, 2)]
SCHEDULE += [{"name": "EnergySaver1a", "start": "00:01", "stop": "01:00"}]
CONFIG = {"button_delay": "00:10", "force_on": False, "lat": 54.091349, "lon": 8.973584, "schedule": SCHEDULE}


@pytest.fixture(params=["Europe/Berlin", "America/New_York", "UTC"])
def tz(request):
    return zoneinfo.ZoneInfo(request.param)


@pytest.fixture
def sc(tz):
    return ScheduleConfiguration(CONFIG | {"tz": tz.key})


def local(tz, day, hour, minute, second=0):
    return datetime.datetime(2025, 12, day, hour, minute, second, tzinfo=tz)


# Local midnight (wittypi4#9)


@pytest.mark.parametrize("now_tz", [None, UTC], ids=["local-now", "utc-now"])
def test_active_after_local_midnight(sc, tz, now_tz):
    now = local(tz, 9, 0, 30)
    now = now.astimezone(now_tz) if now_tz else now

    assert sc.active(now)
    assert sc.next_shutdown(now) == local(tz, 9, 1, 0)
    assert sc.next_startup(now) == local(tz, 9, 2, 0)


@pytest.mark.parametrize("now_tz", [None, UTC], ids=["local-now", "utc-now"])
def test_startup_after_local_midnight(sc, tz, now_tz):
    # shortly after local midnight, before the 00:01 window opens
    now = local(tz, 9, 0, 0, 30)
    now = now.astimezone(now_tz) if now_tz else now

    assert not sc.active(now)
    assert sc.next_startup(now) == local(tz, 9, 0, 1)


def test_results_independent_of_now_timezone(sc, tz):
    """Every minute of a day must evaluate the same whether `now` is local or UTC."""
    start = local(tz, 8, 22, 0)
    for minute in range(0, 6 * 60):
        now = start + datetime.timedelta(minutes=minute)
        now_utc = now.astimezone(UTC)
        assert sc.active(now) == sc.active(now_utc), now
        assert sc.next_startup(now) == sc.next_startup(now_utc), now
        assert sc.next_shutdown(now) == sc.next_shutdown(now_utc), now


# Timezone


@pytest.mark.parametrize(
    ("now", "startup"),
    [
        (datetime.datetime(2025, 7, 1, 12, 0, tzinfo=UTC), datetime.datetime(2025, 7, 1, 22, 0, tzinfo=BERLIN)),
        (datetime.datetime(2025, 12, 1, 12, 0, tzinfo=UTC), datetime.datetime(2025, 12, 1, 22, 0, tzinfo=BERLIN)),
    ],
    ids=["summer", "winter"],
)
def test_default_timezone_follows_dst(local_tz, now, startup):
    """Without an explicit tz the system zone is used, including its DST rules."""
    sc = ScheduleConfiguration({"schedule": [{"name": "evening", "start": "22:00", "stop": "23:00"}]})

    assert sc.next_startup(now) == startup
    assert sc.next_shutdown(startup) == startup + datetime.timedelta(hours=1)


def test_tz_from_config(local_tz):
    sc = ScheduleConfiguration({"tz": "America/New_York", "schedule": [{"name": "e", "start": "22:00", "stop": "23:00"}]})
    now = datetime.datetime(2025, 12, 1, 12, 0, tzinfo=UTC)

    assert sc.next_startup(now) == datetime.datetime(2025, 12, 1, 22, 0, tzinfo=zoneinfo.ZoneInfo("America/New_York"))


def test_invalid_tz_falls_back_to_system(local_tz):
    sc = ScheduleConfiguration({"tz": "Mars/Olympus_Mons", "schedule": [{"name": "e", "start": "22:00", "stop": "23:00"}]})

    for month in (1, 7):  # winter and summer time
        now = datetime.datetime(2025, month, 1, 12, 0, tzinfo=UTC)
        assert sc.next_startup(now) == datetime.datetime(2025, month, 1, 22, 0, tzinfo=BERLIN)


# Configuration


@pytest.mark.parametrize(("value", "expected"), [(None, 10), ("00:30", 30), ("garbage", 10)])
def test_button_delay(value, expected):
    config = {"schedule": SCHEDULE} | ({"button_delay": value} if value else {})
    assert ScheduleConfiguration(config).button_delay == datetime.timedelta(minutes=expected)


@pytest.mark.parametrize("config", [{}, {"schedule": None}, {"schedule": []}], ids=["missing", "none", "empty"])
def test_no_schedule_forces_on(config):
    sc = ScheduleConfiguration(config)
    assert sc.force_on
    assert sc.active()
    assert sc.next_shutdown() is None


def test_force_on(tz):
    sc = ScheduleConfiguration(CONFIG | {"tz": tz.key, "force_on": True})
    now = local(tz, 9, 1, 30)  # between windows
    assert sc.active(now)
    assert sc.next_shutdown(now) is None


SUN_ENTRIES = {
    "offset": {"name": "sun", "start": "sunrise-01:00", "stop": "sunset+01:00"},
    "bare": {"name": "sun", "start": "sunrise", "stop": "sunset"},
}


@pytest.mark.parametrize("entry", SUN_ENTRIES.values(), ids=SUN_ENTRIES.keys())
def test_sun_entry_without_location_is_skipped(local_tz, entry):
    """Sun-relative times only fail once evaluated, so they must be checked when loading."""
    sc = ScheduleConfiguration({"schedule": [entry, {"name": "e", "start": "22:00", "stop": "23:00"}]})
    assert [e.name for e in sc.entries] == ["e"]


@pytest.mark.parametrize(("start", "stop"), [("sunrise", "sunset"), ("sunrise+00:00", "sunset-00:00")], ids=["bare", "offset"])
def test_sun_entry_with_location(start, stop):
    """A bare sun event resolves to the event itself, not to midnight."""
    sc = ScheduleConfiguration(
        {"tz": "Europe/Berlin", "lat": 50.8, "lon": 8.77, "schedule": [{"name": "day", "start": start, "stop": stop}]}
    )
    noon = datetime.datetime(2025, 12, 8, 12, 0, tzinfo=BERLIN)
    sunset = astral.sun.sunset(astral.Observer(50.8, 8.77), date=noon.date(), tzinfo=BERLIN)

    assert sc.active(noon)
    assert sc.next_shutdown(noon) == sunset
    assert sc.next_startup(noon) == astral.sun.sunrise(astral.Observer(50.8, 8.77), date=datetime.date(2025, 12, 9), tzinfo=BERLIN)


def test_location_from_geolocation_file(monkeypatch):
    monkeypatch.setattr(tsschedule, "_parse_geolocation_file", lambda path="/etc/geolocation": (50.8, 8.77))
    sc = ScheduleConfiguration({"tz": "Europe/Berlin", "schedule": [{"name": "day", "start": "sunrise+00:00", "stop": "sunset-00:00"}]})
    assert [e.name for e in sc.entries] == ["day"]


def test_overlapping_entries():
    sc = ScheduleConfiguration(
        {
            "tz": "Europe/Berlin",
            "schedule": [{"name": "a", "start": "10:00", "stop": "12:00"}, {"name": "b", "start": "11:00", "stop": "13:00"}],
        }
    )
    now = datetime.datetime(2025, 12, 8, 10, 30, tzinfo=BERLIN)
    assert sc.next_shutdown(now) == datetime.datetime(2025, 12, 8, 13, 0, tzinfo=BERLIN)


def test_overnight_entry():
    sc = ScheduleConfiguration({"tz": "Europe/Berlin", "schedule": [{"name": "night", "start": "22:00", "stop": "02:00"}]})
    now = datetime.datetime(2025, 12, 8, 23, 0, tzinfo=BERLIN)
    assert sc.active(now)
    assert sc.next_shutdown(now) == datetime.datetime(2025, 12, 9, 2, 0, tzinfo=BERLIN)


# Brownout recovery grid


@pytest.fixture
def recovery_sc():
    return ScheduleConfiguration(
        {
            "tz": "Europe/Berlin",
            "recovery_interval": "00:30",
            "recovery_guard": "00:05",
            "schedule": [{"name": "day", "start": "08:00", "stop": "18:00"}],
        }
    )


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ((10, 0), (10, 30)),  # on a grid point: strictly after now
        ((10, 10), (10, 30)),
        ((10, 26), (11, 0)),  # inside the guard: skip to the following grid point
        ((10, 25), (10, 30)),  # exactly at the guard
        ((17, 27), None),  # guard skips 17:30 to 18:00, which is off
        ((17, 20), (17, 30)),
        ((20, 0), None),  # off period
        ((7, 40), (8, 0)),  # the window opens on a grid point
        ((7, 57), (8, 30)),  # 08:00 is inside the guard
    ],
)
def test_next_recovery(recovery_sc, now, expected):
    now = datetime.datetime(2025, 12, 8, *now, tzinfo=BERLIN)
    expected = datetime.datetime(2025, 12, 8, *expected, tzinfo=BERLIN) if expected else None
    assert recovery_sc.next_recovery(now) == expected


def test_next_recovery_from_utc_now(recovery_sc):
    now = datetime.datetime(2025, 12, 8, 9, 10, tzinfo=UTC)  # 10:10 in Berlin
    assert recovery_sc.next_recovery(now) == datetime.datetime(2025, 12, 8, 10, 30, tzinfo=BERLIN)


@pytest.mark.parametrize("value", [None, "00:00", "garbage"])
def test_next_recovery_disabled(value):
    config = {"tz": "Europe/Berlin", "schedule": [{"name": "day", "start": "08:00", "stop": "18:00"}]}
    if value:
        config["recovery_interval"] = value
    sc = ScheduleConfiguration(config)

    assert sc.recovery_interval is None
    assert sc.next_recovery(datetime.datetime(2025, 12, 8, 10, 0, tzinfo=BERLIN)) is None


def test_next_recovery_force_on():
    sc = ScheduleConfiguration({"tz": "Europe/Berlin", "force_on": True, "recovery_interval": "01:00", "schedule": []})
    now = datetime.datetime(2025, 12, 8, 22, 10, tzinfo=BERLIN)
    assert sc.next_recovery(now) == datetime.datetime(2025, 12, 8, 23, 0, tzinfo=BERLIN)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # 2026-03-29, clocks go from 02:00 to 03:00: the grid point 02:00 is the same instant as 03:00
        ((2026, 3, 29, 1, 40), (2026, 3, 29, 1, 0)),
        ((2026, 3, 29, 3, 10), (2026, 3, 29, 2, 0)),
        # 2026-10-25, clocks go from 03:00 back to 02:00
        ((2026, 10, 25, 1, 40), (2026, 10, 25, 0, 0)),
        ((2026, 10, 25, 3, 10), (2026, 10, 25, 3, 0)),
    ],
)
def test_next_recovery_on_dst_day(now, expected):
    """The grid follows the local wall clock; compare instants in UTC, as the RTC alarm does."""
    sc = ScheduleConfiguration({"tz": "Europe/Berlin", "force_on": True, "recovery_interval": "01:00", "schedule": []})
    now = datetime.datetime(*now, tzinfo=BERLIN)

    result = sc.next_recovery(now)

    assert result.astimezone(UTC) > now.astimezone(UTC)
    assert result.astimezone(UTC) == datetime.datetime(*expected, tzinfo=UTC)


# ButtonEntry


def test_button_entry_boot_ts_is_stable(monkeypatch):
    """Recomputing the boot time drifts with the clocks and can keep next_shutdown() from converging."""
    monotonic = iter(range(1000, 2000))
    monkeypatch.setattr(tsschedule.time, "monotonic", lambda: next(monotonic))
    entry = ButtonEntry(datetime.timedelta(minutes=10), tz=UTC)

    assert entry.boot_ts == entry.boot_ts


def test_button_entry_keeps_system_on(local_tz):
    sc = ScheduleConfiguration({"schedule": [{"name": "e", "start": "22:00", "stop": "23:00"}]})
    entry = ButtonEntry(datetime.timedelta(minutes=10))
    sc.entries.append(entry)
    now = entry.boot_ts + datetime.timedelta(minutes=1)

    assert sc.active(now)
    assert sc.next_shutdown(now) == entry.boot_ts + datetime.timedelta(minutes=10)
    assert not sc.active(entry.boot_ts + datetime.timedelta(minutes=11))


def test_button_entry_disabled():
    entry = ButtonEntry(None, tz=UTC)
    assert not entry.active()
    assert entry.prev_stop() is None


# Geolocation file


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("# geoclue\n54.09 # lat\n8.97\n10\n100\n", (54.09, 8.97)),
        ("-33.9\n-70.6\n", (-33.9, -70.6)),
        ("54.09\n", None),
        ("91\n8.97\n", None),
        ("54.09\n181\n", None),
        ("north\neast\n", None),
    ],
    ids=["comments", "southwest", "too-short", "lat-range", "lon-range", "garbage"],
)
def test_parse_geolocation_file(tmp_path, content, expected):
    path = tmp_path / "geolocation"
    path.write_text(content)
    assert _parse_geolocation_file(str(path)) == expected


def test_parse_geolocation_file_missing(tmp_path):
    assert _parse_geolocation_file(str(tmp_path / "missing")) is None
