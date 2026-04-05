# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the terok-dbus command registry."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from terok_dbus._registry import COMMANDS, CommandDef, _handle_notify, _handle_subscribe


class TestCommandRegistry:
    """Verify command definitions are well-formed."""

    def test_commands_is_tuple(self):
        assert isinstance(COMMANDS, tuple)

    def test_all_entries_are_commanddef(self):
        for cmd in COMMANDS:
            assert isinstance(cmd, CommandDef)

    def test_notify_command_exists(self):
        names = {cmd.name for cmd in COMMANDS}
        assert "notify" in names

    def test_subscribe_command_exists(self):
        names = {cmd.name for cmd in COMMANDS}
        assert "subscribe" in names

    def test_all_commands_have_handlers(self):
        for cmd in COMMANDS:
            assert cmd.handler is not None, f"{cmd.name} has no handler"

    def test_notify_has_summary_arg(self):
        notify = next(cmd for cmd in COMMANDS if cmd.name == "notify")
        arg_names = [a.name for a in notify.args]
        assert "summary" in arg_names

    def test_subscribe_has_no_required_args(self):
        subscribe = next(cmd for cmd in COMMANDS if cmd.name == "subscribe")
        assert len(subscribe.args) == 0


class TestHandleNotify:
    """Tests for the ``_handle_notify`` handler function."""

    async def test_sends_notification_and_prints_id(self, capsys):
        mock_notifier = AsyncMock()
        mock_notifier.notify.return_value = 7

        with patch("terok_dbus.create_notifier", new_callable=AsyncMock) as mock_factory:
            mock_factory.return_value = mock_notifier
            await _handle_notify(summary="Alpha", body="Beta", timeout=5000)

        mock_notifier.notify.assert_awaited_once_with("Alpha", "Beta", timeout_ms=5000)
        mock_notifier.disconnect.assert_awaited_once()
        assert capsys.readouterr().out.strip() == "7"

    async def test_disconnects_on_notify_error(self):
        mock_notifier = AsyncMock()
        mock_notifier.notify.side_effect = RuntimeError("boom")

        with patch("terok_dbus.create_notifier", new_callable=AsyncMock) as mock_factory:
            mock_factory.return_value = mock_notifier
            with pytest.raises(RuntimeError, match="boom"):
                await _handle_notify(summary="Fail")

        mock_notifier.disconnect.assert_awaited_once()

    async def test_uses_defaults(self, capsys):
        mock_notifier = AsyncMock()
        mock_notifier.notify.return_value = 0

        with patch("terok_dbus.create_notifier", new_callable=AsyncMock) as mock_factory:
            mock_factory.return_value = mock_notifier
            await _handle_notify(summary="Title")

        mock_notifier.notify.assert_awaited_once_with("Title", "", timeout_ms=-1)


class TestHandleSubscribe:
    """Tests for the ``_handle_subscribe`` handler function."""

    async def test_lifecycle_start_wait_stop_disconnect(self):
        mock_notifier = AsyncMock()
        mock_subscriber = AsyncMock()
        mock_event = MagicMock()
        mock_event.wait = AsyncMock()  # resolves immediately → no signal needed

        with (
            patch("terok_dbus.create_notifier", new_callable=AsyncMock) as mock_factory,
            patch("terok_dbus.EventSubscriber", return_value=mock_subscriber),
            patch("asyncio.Event", return_value=mock_event),
        ):
            mock_factory.return_value = mock_notifier
            await _handle_subscribe()

        mock_subscriber.start.assert_awaited_once()
        mock_subscriber.stop.assert_awaited_once()
        mock_notifier.disconnect.assert_awaited_once()

    async def test_stop_called_when_start_fails(self):
        mock_notifier = AsyncMock()
        mock_subscriber = AsyncMock()
        mock_subscriber.start.side_effect = RuntimeError("bus gone")

        with (
            patch("terok_dbus.create_notifier", new_callable=AsyncMock) as mock_factory,
            patch("terok_dbus.EventSubscriber", return_value=mock_subscriber),
        ):
            mock_factory.return_value = mock_notifier
            with pytest.raises(RuntimeError, match="bus gone"):
                await _handle_subscribe()

        mock_subscriber.stop.assert_awaited_once()
        mock_notifier.disconnect.assert_awaited_once()

    async def test_disconnect_called_when_subscriber_init_fails(self):
        mock_notifier = AsyncMock()

        with (
            patch("terok_dbus.create_notifier", new_callable=AsyncMock) as mock_factory,
            patch("terok_dbus.EventSubscriber", side_effect=TypeError("bad arg")),
        ):
            mock_factory.return_value = mock_notifier
            with pytest.raises(TypeError, match="bad arg"):
                await _handle_subscribe()

        mock_notifier.disconnect.assert_awaited_once()
