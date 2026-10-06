# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the major version is 0, minor releases may contain breaking changes.

## [Unreleased]

### Changed

- **Breaking:** requires `scheduleparse` 2026.10.2.
- Schedules default to the system timezone with its DST rules instead of a fixed UTC offset.
- On termination, the next startup is computed from the RTC instead of the system clock.
- A start outside the schedule keeps the system on for `button_delay`, like a button press,
  instead of shutting down right away.

### Fixed

- Schedule windows between local midnight and UTC midnight (e.g. 00:01–01:00 in Europe/Berlin)
  were evaluated on the wrong day on the Raspberry Pi 5, whose RTC reads UTC, and never woke
  the system (wittypi4#9).
- An unknown action reason from newer WittyPi firmware crashed `tsscheduled` at startup; it now
  maps to `REASON_NA`.
- A sunrise/sunset schedule entry without a location crashed `tsscheduled` at startup; it is now
  skipped with a warning.
- A bare sun event without an offset (e.g. `start: sunrise`) resolved to midnight; it now
  resolves to the event. Previously only `sunrise+00:00` worked.
- `ButtonEntry` computes the boot time once, so `next_shutdown()` converges.
- WittyPi voltage and current adjustments (`adj_vin`, `adj_vout`, `adj_iout`) were truncated,
  writing values 0.01 off.
- Built wheels contained only metadata and no code.
- Unquoted times like `18:00` in the schedule file were read as numbers and crashed `tsscheduled`.
- Without any clock source to check the RTC against, `tsscheduled` crashed; it now exits with
  code 3. A malformed `/etc/fake-hwclock.data` no longer hides the other clock sources.

### Added

- Tests for both backends, schedule edge cases and the daemon, including two-day simulations with
  the WittyPi firmware's alarm handling and the Raspberry Pi 5 wake alarm.
- CI runs the tests on Python 3.11 to 3.14 and checks that the built wheel installs and runs.

### Removed

- The unused dependencies `i2cdevice` and `gpiozero`.

## [0.5.0] - 2026-09-17

### Removed

- **Breaking:** The `rtc-pcf85063-wittypi4` kernel RTC driver and device-tree overlay. They now
  live in the separate `wittypi4` DKMS module, which WittyPi 4 setups must install.
  tsschedule only talks to the MCU from userspace.

## [0.4.0] - 2026-07-14

### Added

- Brownout recovery: `recovery_interval` and `recovery_guard` schedule options set periodic
  power-on alarms during scheduled on-times, so a Pi that lost power mid-run comes back up
  without waking during off periods.
- Pi 5 boot-time power diagnostics (`power_reset`, `max_current`, `usb_over_current_detected`)
  read from the device tree.
- Pi 5 `PM_RSTS` reset-reason logging; watchdog resets map to an `ActionReason`.

## [0.3.0] - 2026-01-26

### Added

- Support for the Raspberry Pi Compute Module 5.

## [0.2.0] - 2026-01-08

### Added

- Raspberry Pi 5 backend using the built-in RTC, on a common backend base class.
- `tz` schedule option to set the schedule timezone.
- Raspberry Pi 5 documentation.

### Changed

- **Breaking:** `ScheduleConfiguration` no longer takes a `tz` argument; the timezone comes from
  the `tz` configuration key and defaults to the system timezone.
- `button_delay` now defaults to 10 minutes when absent or invalid.

## [0.1.0] - 2026-01-08

First release as `tsschedule`, formerly `wittypi4`.

### Added

- `tsscheduled` daemon (`tsschedule.scheduled:main`), which shuts down and wakes the Pi
  according to a YAML schedule, with astronomical times from `astral` and button-press entries.
- WittyPi 4 backend in `tsschedule.backends.wittypi4`, with the `rtc-pcf85063-wittypi4`
  kernel RTC driver.
- Locations from `/etc/geolocation`, alternative `fake_hwclock` methods and the
  `wittypid-power.service` systemd unit.
- API and WittyPi 4 documentation.

[Unreleased]: https://github.com/trackIT-Systems/tsschedule/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/trackIT-Systems/tsschedule/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/trackIT-Systems/tsschedule/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/trackIT-Systems/tsschedule/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/trackIT-Systems/tsschedule/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/trackIT-Systems/tsschedule/releases/tag/v0.1.0
