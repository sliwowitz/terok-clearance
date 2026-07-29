# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Logging seam for the standalone ``serve()`` daemons.

[`configure_logging`][terok_clearance.runtime.service.configure_logging] is
the funnel both the hub and verdict ``serve()`` entry points call before
blocking on signals.  It must route through [`configure`][terok_util.configure]
with ``stderr=True`` so the launcher keeps reading the pipe even on a journald
host.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from terok_clearance.runtime.service import configure_logging


def test_configure_logging_routes_through_util_with_stderr() -> None:
    """Defaults: hub identity, INFO level, stderr kept."""
    with patch("terok_clearance.runtime.service.configure") as configure:
        configure_logging()

    configure.assert_called_once_with(
        identifier="terok-clearance-hub", level=logging.INFO, stderr=True
    )


def test_configure_logging_forwards_explicit_level() -> None:
    """A caller-supplied level reaches the unified facility verbatim."""
    with patch("terok_clearance.runtime.service.configure") as configure:
        configure_logging(logging.DEBUG)

    assert configure.call_args.kwargs["level"] == logging.DEBUG
