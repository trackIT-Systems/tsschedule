# WittyPi 4 Hardware Setup

This document provides hardware-specific setup instructions for using tsschedule with the WittyPi 4 power management board.

## Basic Workflow

The basic workflow followed by the WittyPi is described in the figure cited from UUGear's manual:

![WittyPi basic workflow, as seen in UUGear's user manual, Chapter 4.](../img/wittypi_workflow.jpg)

## GPIO Configuration

To enable turning the Raspberry Pi on and shutting it down gracefully, one can make use of existing dtoverlays, described in `/boot/firmware/overlays/README`:

```
Name:   gpio-shutdown
Info:   Initiates a shutdown when GPIO pin changes. The given GPIO pin
        is configured as an input key that generates KEY_POWER events.
...
Name:   gpio-led
Info:   This is a generic overlay for activating LEDs (or any other component)
        by a GPIO pin.
```

WittyPi uses inverted logic for the shutdown button, i.e. `active_high`. Also the virtual button press is quite short (can be < 1ms), hence debouncing should be disabled. Using the following entries in `/boot/firmware/config.txt`, sysup and shutdown is made available:

```ini
dtoverlay=gpio-shutdown,gpio_pin=4,debounce=0,active_low=0
dtoverlay=gpio-led,gpio=17,label=sysup,trigger=heartbeat
```

> Note: The SYSUP signal `(0, 1, 0, 1)` in 100ms intervals is sent using the trigger `heartbeat`, as this by accident matches the required interval.

## Real Time Clock (RTC) Linux Driver

Witty Pi 4 exposes the PCF85063 through the MCU at I2C address `0x08`. The kernel driver and device-tree overlay live in the [wittypi4](https://github.com/trackIT-Systems/wittypi4) repository (`rtc-pcf85063-wittypi4` plus `wittypi4-overlay.dts`). Follow that project's README to build with DKMS and load the overlay.

tsschedule only talks to the MCU over I2C from userspace; it does not ship the kernel module.
## TxD Power Cut

WittyPi recognizes the Raspberry Pi's shutdown by monitoring the TxD output. This mostly works reliable, but sometimes leads to a hangup where the Raspberry Pi shutdown, but TxD is still high and power is not cut.

To make this more reliable a systemd service can be created, that forcefully sets GPIO 14 low (and thereby disables TxD / the serial console). An example service is to be found in [`etc/wittypid-power.service`](../etc/wittypid-power.service).

## Python API Usage

### Quick Example

```python
import smbus2
from tsschedule.backends.wittypi4 import WittyPi4
import datetime

# Initialize WittyPi 4 backend
bus = smbus2.SMBus(1, force=True)
wp = WittyPi4(bus)

# Read hardware status
print(f"Input: {wp.voltage_in}V, Output: {wp.voltage_out}V @ {wp.current_out}A")
print(f"Temperature: {wp.lm75b_temperature}°C")
print(f"RTC Time: {wp.rtc_datetime}")

# Schedule next startup in 1 hour
wp.set_startup_datetime(datetime.datetime.now() + datetime.timedelta(hours=1))
```

For complete API reference, see the [Python API Documentation](API.md).

## Systemd Service Configuration

For production use, install tsscheduled as a systemd service. See [`etc/wittypid-power.service`](../etc/wittypid-power.service) for WittyPi 4 specific configuration.

## Hardware Resources

### Datasheets
- [WittyPi 4](https://www.uugear.com/doc/WittyPi4_UserManual.pdf)
- [PCF85063A](https://www.nxp.com/docs/en/data-sheet/PCF85063A.pdf) (RTC)
- [LM75B](https://www.ti.com/lit/ds/symlink/lm75b.pdf) (Temperature Sensor)

