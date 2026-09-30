# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared test fixtures and constants for terok-clearance."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# Activate dbusmock's pytest fixtures (dbusmock_session, dbusmock_system).
# pytest_plugins must live in the top-level conftest. pytest 8.4 and later
# reject it in a non-top-level conftest. Only the integration tests use these
# fixtures. This declaration does not affect the unit suite.
pytest_plugins = "dbusmock.pytest_fixtures"

# ── Test data constants ────────────────────────────────────────────────
# Reusable across unit and integration tests for subscriber scenarios.

DEST_IP = "198.51.100.1"
DEST_IP_ALT = "198.51.100.2"
RESOLVED_IPS = ["198.51.100.1", "198.51.100.2"]
DOMAIN = "registry.example.net"
DOMAIN_ALT = "repo.example.net"
CONTAINER = "sandbox-alpha"
PROJECT = "warp-core"
TASK = "build"
REASON = "install deps"

# Container identity.  A hub keys a container by the 12-char short id the
# shield reader stamps on its events; the full id and the podman name are
# aliases at best.  Every one of these is a distinct string on purpose, so
# a test that passes one where another belongs fails instead of agreeing
# with itself.
CONTAINER_SHORT_ID = "c0ffee0a1b2c"
CONTAINER_FULL_ID = CONTAINER_SHORT_ID + "3d" * 26
CONTAINER_NAME = "warp-core-cli-build"
OTHER_SHORT_ID = "decaf0e1f2a3"

# What ``terok-shield allow`` writes to stderr when the security tier refuses
# a target.  This matches shield's real message, including its em dash, which
# the hub's printable-ASCII rule turns into a space.  The next step comes at
# the end of the line, and TIER_REFUSAL_ADVICE is that part, so a test can
# check that it survives to the popup.
TIER_REFUSAL_ADVICE = "break-glass only: use a shield.override (reason + expires)"
TIER_REFUSAL_STDERR = (
    f"Error: {DOMAIN} is a security-tier host — {TIER_REFUSAL_ADVICE} to reach it directly"
)


@pytest.fixture
def private_runtime_dir(tmp_path: Path) -> Iterator[Path]:
    """A mode-0700, uid-owned directory for ingester / clearance sockets.

    ``EventIngester`` and ``ClearanceHub`` both refuse to bind under a
    parent the current uid doesn't own or that's group/world accessible
    — the on-disk auth boundary.  ``tmp_path`` is usually fine, but some
    CI hosts use a shared tmpfs with bespoke modes.  This fixture forces
    the correct shape so socket-bind tests see a clean slate.
    """
    priv = tmp_path / "runtime"
    priv.mkdir(mode=0o700)
    os.chmod(priv, 0o700)
    yield priv
