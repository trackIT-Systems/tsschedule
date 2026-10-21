"""Tests for logging to the journal (native protocol) and to stderr."""

import logging
import os
import socket
import struct
import tempfile

import pytest

from tsschedule import journal
from tsschedule.journal import JournalHandler, journal_field, log_handler, stderr_is_journal, syslog_priority


def parse_datagram(data: bytes) -> dict[str, str]:
    """The fields of one native-protocol datagram."""
    fields = {}
    while data:
        line, _, rest = data.partition(b"\n")
        if b"=" in line:
            key, _, value = line.partition(b"=")
            fields[key.decode()] = value.decode()
            data = rest
        else:
            (length,) = struct.unpack("<Q", rest[:8])
            fields[line.decode()] = rest[8 : 8 + length].decode()
            assert rest[8 + length : 9 + length] == b"\n"
            data = rest[9 + length :]
    return fields


@pytest.fixture
def journald():
    """A datagram socket standing in for journald; yields (path, receive)."""
    # AF_UNIX paths are limited to ~100 bytes, pytest's tmp_path can be longer
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        path = os.path.join(directory, "socket")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        server.bind(path)
        server.settimeout(1)

        def receive() -> dict[str, str]:
            return parse_datagram(server.recv(65536))

        yield path, receive
        server.close()


@pytest.fixture
def make_logger():
    """A logger writing only to the given handler, which is closed afterwards."""
    handlers = []

    def make(handler: logging.Handler, formatter: bool = True) -> logging.Logger:
        if formatter:
            handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        handlers.append(handler)
        logger = logging.getLogger("test.journal")
        logger.handlers[:] = [handler]
        logger.propagate = False
        logger.setLevel(logging.DEBUG)
        return logger

    yield make
    for handler in handlers:
        handler.close()
    logging.getLogger("test.journal").handlers.clear()


@pytest.mark.parametrize(
    "level, priority",
    [(logging.CRITICAL, 2), (logging.ERROR, 3), (logging.WARNING, 4), (logging.INFO, 6), (logging.DEBUG, 7), (25, 6)],
)
def test_syslog_priority(level, priority):
    assert syslog_priority(level) == priority


def test_journal_field_plain_and_multiline():
    assert journal_field("MESSAGE", "hello") == b"MESSAGE=hello\n"
    assert journal_field("MESSAGE", "a\nb") == b"MESSAGE\n" + struct.pack("<Q", 3) + b"a\nb\n"


def test_handler_sends_fields(journald, make_logger):
    path, receive = journald
    logger = make_logger(JournalHandler("tsscheduled", path))

    logger.warning("Cannot read %s", "/dev/x")
    fields = receive()

    assert fields["MESSAGE"] == "test.journal: Cannot read /dev/x"
    assert fields["PRIORITY"] == "4"
    assert fields["SYSLOG_IDENTIFIER"] == "tsscheduled"
    assert fields["CODE_FILE"] == __file__
    assert fields["CODE_FUNC"] == "test_handler_sends_fields"
    assert int(fields["CODE_LINE"]) > 0


def test_traceback_is_one_entry(journald, make_logger):
    path, receive = journald
    logger = make_logger(JournalHandler("tsscheduled", path))

    try:
        raise ValueError("broken")
    except ValueError:
        logger.exception("Failed")
    fields = receive()

    assert fields["PRIORITY"] == "3"
    assert fields["MESSAGE"].startswith("test.journal: Failed\nTraceback")
    assert fields["MESSAGE"].endswith("ValueError: broken")


def test_send_failure_falls_back_to_stderr(journald, make_logger, capsys):
    path, _ = journald
    handler = JournalHandler("tsscheduled", path)
    logger = make_logger(handler)
    handler.socket.close()

    logger.error("Lost")

    assert capsys.readouterr().err == "<3>test.journal: Lost\n"


def test_missing_socket_raises(tmp_path):
    with pytest.raises(OSError):
        JournalHandler("tsscheduled", str(tmp_path / "missing"))


def test_stderr_is_journal(monkeypatch, tmp_path):
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    assert not stderr_is_journal()

    with open(tmp_path / "stderr", "w") as stderr:
        stat = os.fstat(stderr.fileno())
        monkeypatch.setattr(journal.sys, "stderr", stderr)
        monkeypatch.setenv("JOURNAL_STREAM", f"{stat.st_dev}:{stat.st_ino}")
        assert stderr_is_journal()
        monkeypatch.setenv("JOURNAL_STREAM", f"{stat.st_dev}:{stat.st_ino + 1}")
        assert not stderr_is_journal()
        monkeypatch.setenv("JOURNAL_STREAM", "garbage")
        assert not stderr_is_journal()


def test_log_handler_uses_journal_under_systemd(monkeypatch, journald, make_logger):
    path, receive = journald
    monkeypatch.setattr(journal, "stderr_is_journal", lambda: True)
    monkeypatch.setattr(JournalHandler.__init__, "__defaults__", (path,))

    handler = log_handler("tsscheduled")
    assert isinstance(handler, JournalHandler)
    make_logger(handler, formatter=False).info("Started")
    fields = receive()

    assert fields["MESSAGE"] == "test.journal: Started"
    assert fields["SYSLOG_IDENTIFIER"] == "tsscheduled"
    assert fields["PRIORITY"] == "6"


def test_log_handler_on_terminal(monkeypatch, make_logger, capsys):
    monkeypatch.setattr(journal, "stderr_is_journal", lambda: False)

    make_logger(log_handler("tsscheduled"), formatter=False).warning("Careful")

    assert capsys.readouterr().err == "WARNING test.journal: Careful\n"


def test_log_handler_without_journald(monkeypatch, make_logger, capsys, tmp_path):
    monkeypatch.setattr(journal, "stderr_is_journal", lambda: True)
    monkeypatch.setattr(JournalHandler.__init__, "__defaults__", (str(tmp_path / "missing"),))

    make_logger(log_handler("tsscheduled"), formatter=False).warning("Careful")

    assert capsys.readouterr().err == "WARNING test.journal: Careful\n"
