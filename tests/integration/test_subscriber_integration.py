# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for EventSubscriber — real D-Bus signals via dbus-fast ServiceInterface."""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method, signal

from terok_dbus._interfaces import (
    CLEARANCE_BUS_NAME,
    CLEARANCE_INTERFACE_NAME,
    CLEARANCE_OBJECT_PATH,
    SHIELD_BUS_NAME,
    SHIELD_INTERFACE_NAME,
    SHIELD_OBJECT_PATH,
)
from terok_dbus._subscriber import EventSubscriber

# ── Mock D-Bus services ───────────────────────────────────────────────


class MockShield1(ServiceInterface):
    """Mock implementation of org.terok.Shield1 for testing."""

    def __init__(self) -> None:
        super().__init__(SHIELD_INTERFACE_NAME)
        self._verdict_log: list[tuple[str, str]] = []

    @signal()
    def connection_blocked(
        self,
        container: "s",
        dest: "s",
        port: "q",
        proto: "q",
        domain: "s",
        request_id: "s",
    ) -> None:
        """Emit a ConnectionBlocked signal."""

    @method()
    def verdict(self, request_id: "s", action: "s") -> "b":
        """Record a Verdict call and return success."""
        self._verdict_log.append((request_id, action))
        return True

    @signal()
    def verdict_applied(
        self,
        container: "s",
        dest: "s",
        request_id: "s",
        action: "s",
        ok: "b",
    ) -> None:
        """Emit a VerdictApplied signal."""


class MockClearance1(ServiceInterface):
    """Mock implementation of org.terok.Clearance1 for testing."""

    def __init__(self) -> None:
        super().__init__(CLEARANCE_INTERFACE_NAME)
        self._resolve_log: list[tuple[str, str]] = []

    @signal()
    def request_received(
        self,
        request_id: "s",
        project: "s",
        task: "s",
        dest: "s",
        port: "q",
        reason: "s",
    ) -> None:
        """Emit a RequestReceived signal."""

    @method()
    def resolve(self, request_id: "s", action: "s") -> "b":
        """Record a Resolve call and return success."""
        self._resolve_log.append((request_id, action))
        return True

    @signal()
    def request_resolved(
        self,
        request_id: "s",
        action: "s",
        ips: "as",
    ) -> None:
        """Emit a RequestResolved signal."""


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def shield_service(dbusmock_session) -> AsyncIterator[MockShield1]:
    """Export a MockShield1 service on the private test bus."""
    bus = await MessageBus().connect()
    svc = MockShield1()
    bus.export(SHIELD_OBJECT_PATH, svc)
    await bus.request_name(SHIELD_BUS_NAME)
    yield svc
    bus.disconnect()


@pytest.fixture
async def clearance_service(dbusmock_session) -> AsyncIterator[MockClearance1]:
    """Export a MockClearance1 service on the private test bus."""
    bus = await MessageBus().connect()
    svc = MockClearance1()
    bus.export(CLEARANCE_OBJECT_PATH, svc)
    await bus.request_name(CLEARANCE_BUS_NAME)
    yield svc
    bus.disconnect()


@pytest.fixture
async def subscriber_bus(dbusmock_session) -> AsyncIterator[MessageBus]:
    """A separate bus connection for the EventSubscriber."""
    bus = await MessageBus().connect()
    yield bus
    bus.disconnect()


# ── Tests ─────────────────────────────────────────────────────────────


@pytest.mark.needs_dbus
class TestShieldSubscriberIntegration:
    """Shield1 signal → notification → action → verdict flow on a real bus."""

    async def test_connection_blocked_creates_notification(
        self, shield_service: MockShield1, subscriber_bus: MessageBus
    ):
        mock_notifier = AsyncMock()
        mock_notifier.notify = AsyncMock(return_value=1)
        mock_notifier.on_action = AsyncMock()

        sub = EventSubscriber(mock_notifier, bus=subscriber_bus)
        await sub.start()

        # Allow signal subscriptions to settle
        await asyncio.sleep(0.1)

        shield_service.connection_blocked("ctr1", "10.0.0.1", 443, 6, "example.com", "req-1")
        await asyncio.sleep(0.2)

        mock_notifier.notify.assert_awaited_once()
        summary = mock_notifier.notify.call_args[0][0]
        assert "example.com:443" in summary
        await sub.stop()

    async def test_action_routes_verdict_to_service(
        self, shield_service: MockShield1, subscriber_bus: MessageBus
    ):
        mock_notifier = AsyncMock()
        mock_notifier.notify = AsyncMock(return_value=1)
        mock_notifier.on_action = AsyncMock()

        sub = EventSubscriber(mock_notifier, bus=subscriber_bus)
        await sub.start()
        await asyncio.sleep(0.1)

        shield_service.connection_blocked("ctr1", "10.0.0.1", 443, 6, "test.com", "req-2")
        await asyncio.sleep(0.2)

        # Simulate operator clicking "Allow"
        action_cb = mock_notifier.on_action.call_args[0][1]
        action_cb("accept")
        await asyncio.sleep(0.2)

        assert ("req-2", "accept") in shield_service._verdict_log
        await sub.stop()


@pytest.mark.needs_dbus
class TestClearanceSubscriberIntegration:
    """Clearance1 signal → notification → action → resolve flow on a real bus."""

    async def test_request_received_creates_notification(
        self, clearance_service: MockClearance1, subscriber_bus: MessageBus
    ):
        mock_notifier = AsyncMock()
        mock_notifier.notify = AsyncMock(return_value=1)
        mock_notifier.on_action = AsyncMock()

        sub = EventSubscriber(mock_notifier, bus=subscriber_bus)
        await sub.start()
        await asyncio.sleep(0.1)

        clearance_service.request_received("req-10", "proj", "build", "pypi.org", 443, "deps")
        await asyncio.sleep(0.2)

        mock_notifier.notify.assert_awaited_once()
        summary = mock_notifier.notify.call_args[0][0]
        assert "build" in summary
        assert "pypi.org:443" in summary
        await sub.stop()

    async def test_action_routes_resolve_to_service(
        self, clearance_service: MockClearance1, subscriber_bus: MessageBus
    ):
        mock_notifier = AsyncMock()
        mock_notifier.notify = AsyncMock(return_value=1)
        mock_notifier.on_action = AsyncMock()

        sub = EventSubscriber(mock_notifier, bus=subscriber_bus)
        await sub.start()
        await asyncio.sleep(0.1)

        clearance_service.request_received("req-11", "proj", "test", "npm.org", 443, "install")
        await asyncio.sleep(0.2)

        action_cb = mock_notifier.on_action.call_args[0][1]
        action_cb("deny")
        await asyncio.sleep(0.2)

        assert ("req-11", "deny") in clearance_service._resolve_log
        await sub.stop()
