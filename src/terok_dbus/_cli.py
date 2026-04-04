# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""CLI entry point for ``terok-dbus-notify`` — send a test notification."""

import argparse
import asyncio
import sys

from terok_dbus import create_notifier


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for terok-dbus-notify."""
    parser = argparse.ArgumentParser(
        prog="terok-dbus-notify",
        description="Send a desktop notification via D-Bus.",
    )
    parser.add_argument("summary", help="Notification title")
    parser.add_argument("body", nargs="?", default="", help="Notification body text")
    parser.add_argument(
        "-t",
        "--timeout",
        type=int,
        default=-1,
        metavar="MS",
        help="Expiration timeout in milliseconds (-1 = server default)",
    )
    return parser


async def _async_main(args: argparse.Namespace) -> None:
    """Send a single notification and print its ID."""
    notifier = await create_notifier()
    notification_id = await notifier.notify(
        args.summary,
        args.body,
        timeout_ms=args.timeout,
    )
    print(notification_id)  # noqa: T201


def main() -> None:
    """Entry point for ``terok-dbus-notify``."""
    parser = _build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        sys.exit(130)
