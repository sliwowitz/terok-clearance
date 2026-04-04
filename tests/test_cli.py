# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the terok-dbus-notify CLI entry point."""

from unittest.mock import AsyncMock, patch

from terok_dbus._cli import _build_parser, main


class TestCliParser:
    """Argument parsing tests."""

    def test_summary_required(self):
        parser = _build_parser()
        args = parser.parse_args(["Hello"])
        assert args.summary == "Hello"
        assert args.body == ""
        assert args.timeout == -1

    def test_summary_and_body(self):
        parser = _build_parser()
        args = parser.parse_args(["Hello", "World"])
        assert args.summary == "Hello"
        assert args.body == "World"

    def test_timeout_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["-t", "5000", "Hello"])
        assert args.timeout == 5000

    def test_timeout_long_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["--timeout", "3000", "Hello"])
        assert args.timeout == 3000


class TestCliMain:
    """Integration tests for the main() entry point."""

    def test_main_sends_notification(self):
        mock_notifier = AsyncMock()
        mock_notifier.notify.return_value = 42

        with (
            patch("terok_dbus._cli.create_notifier", new_callable=AsyncMock) as mock_factory,
            patch("sys.argv", ["terok-dbus-notify", "Test", "Body"]),
        ):
            mock_factory.return_value = mock_notifier
            main()
            mock_notifier.notify.assert_awaited_once_with(
                "Test",
                "Body",
                timeout_ms=-1,
            )
