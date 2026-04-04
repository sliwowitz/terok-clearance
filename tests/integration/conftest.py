# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Fixtures and skip guards for integration tests.

Environment requirements are expressed via pytest markers:

- ``needs_dbus``: a D-Bus session bus must be reachable.
- ``needs_notification_daemon``: a notification daemon must be
  running on the session bus.

The ``dbus_session`` fixture always launches a **private** bus so that
test notifications never appear in the developer's notification bar.
The ``notification_daemon`` fixture starts dunst on that private bus.
"""

import os
import shutil
import signal
import subprocess
import time
from collections.abc import AsyncIterator, Iterator

import pytest

from terok_dbus import DbusNotifier, Notifier, create_notifier


def _has(binary: str) -> bool:
    """Check if a binary is available on PATH."""
    return shutil.which(binary) is not None


@pytest.fixture(scope="session")
def dbus_session() -> Iterator[str]:
    """Launch a private D-Bus session bus for the test run.

    Always starts a fresh bus — even when the user's desktop session
    has one — so that test notifications stay invisible.

    Yields:
        The private bus address string.
    """
    if not _has("dbus-launch"):
        pytest.skip("dbus-launch not installed")

    proc = subprocess.run(
        ["dbus-launch", "--sh-syntax"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        pytest.skip(f"dbus-launch failed: {proc.stderr}")

    env = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            env[key] = val.rstrip(";").strip("'")

    bus_address = env.get("DBUS_SESSION_BUS_ADDRESS", "")
    bus_pid = env.get("DBUS_SESSION_BUS_PID", "")

    if not bus_address:
        pytest.skip(f"dbus-launch did not provide DBUS_SESSION_BUS_ADDRESS: {proc.stdout!r}")

    # Override the env for the entire test session so create_notifier()
    # connects to the private bus, not the user's desktop session.
    original = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = bus_address

    yield bus_address

    if original is not None:
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = original
    else:
        os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)

    if bus_pid:
        try:
            os.kill(int(bus_pid), signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass


@pytest.fixture(scope="session")
def notification_daemon(dbus_session: str) -> Iterator[None]:
    """Start dunst on the private test bus.

    Skips if dunst is not installed.
    """
    if not _has("dunst"):
        pytest.skip(
            "dunst not installed; install dunst or run via 'make test-matrix' for full coverage"
        )

    proc = subprocess.Popen(
        ["dunst"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "DBUS_SESSION_BUS_ADDRESS": dbus_session},
    )
    time.sleep(0.5)

    if proc.poll() is not None:
        pytest.skip("dunst failed to start")

    yield

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
async def notifier(dbus_session: str, notification_daemon: None) -> AsyncIterator[Notifier]:
    """Provide a connected ``DbusNotifier`` backed by the private test bus.

    Disconnects automatically after the test.
    """
    n = await create_notifier()
    if not isinstance(n, DbusNotifier):
        pytest.skip("D-Bus notifier backend unavailable in integration environment")
    yield n
    await n.disconnect()
