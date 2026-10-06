"""Tests for hardware detection."""

import datetime

import pytest
from conftest import UTC, FakeBus

import tsschedule
from tsschedule import detect_hardware


@pytest.fixture
def no_i2c(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory: '/dev/i2c-1'")

    monkeypatch.setattr(tsschedule.smbus2, "SMBus", missing)


@pytest.mark.parametrize("hardware", ["wittypi4", "raspberrypi5"])
def test_manual_override(hardware):
    assert detect_hardware(hardware) == hardware


def test_detects_wittypi4(monkeypatch):
    monkeypatch.setattr(tsschedule.smbus2, "SMBus", lambda *args, **kwargs: FakeBus(datetime.datetime.now(UTC)))
    assert detect_hardware() == "wittypi4"
    assert detect_hardware("garbage") == "wittypi4"


def test_wrong_firmware_id_is_not_wittypi4(monkeypatch, no_i2c):
    bus = FakeBus(datetime.datetime.now(UTC))
    bus.reg[0] = 0x37
    monkeypatch.setattr(tsschedule.smbus2, "SMBus", lambda *args, **kwargs: bus)
    monkeypatch.setattr(tsschedule.pathlib.Path, "exists", lambda self: False)
    assert detect_hardware() is None


def test_nothing_detected(monkeypatch, no_i2c):
    monkeypatch.setattr(tsschedule.pathlib.Path, "exists", lambda self: False)
    assert detect_hardware() is None
