# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The :class:`ClearanceEvent` value type.

One flat dataclass carries every event kind the hub fans out to
subscribers.  Varlink IDL can't model sum types directly, so the
``type`` field discriminates and the remaining fields are populated
per-kind — the same pattern ``io.systemd.Resolve.Monitor`` uses.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClearanceEvent:
    """One event fanned out to every ``Subscribe()`` caller.

    Known values of ``type``:

    * ``connection_blocked`` — sets ``request_id``, ``dest``, ``port``,
      ``proto``, ``domain``.  Requires an operator verdict.
    * ``verdict_applied`` — sets ``request_id``, ``action``, ``ok``.
    * ``container_started`` — just ``container``.
    * ``container_exited`` — ``container`` + ``reason``.
    * ``shield_up`` / ``shield_down`` / ``shield_down_all`` — just
      ``container``.

    Unknown values are forwarded unchanged so the wire format can grow
    without breaking clients pinned to older schemas.
    """

    type: str
    container: str
    request_id: str = ""
    dest: str = ""
    port: int = 0
    proto: int = 0
    domain: str = ""
    action: str = ""
    ok: bool = False
    reason: str = ""
