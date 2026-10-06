# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the major version is 0, minor releases may contain breaking changes.

## [Unreleased]

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
