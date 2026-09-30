# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Typed varlink errors the hub can raise from ``Verdict`` and ``SetMute``.

Subclasses of `asyncvarlink.TypedVarlinkErrorReply` — the hub
raises one of these on a refusal path (four for ``Verdict``, one for
``SetMute``), and clients pattern-match on the error name instead of
stringly-matching a message.  All of them share the
``org.terok.Clearance1`` interface namespace.
"""

from asyncvarlink import TypedVarlinkErrorReply

from terok_clearance.wire.interface import CLEARANCE_INTERFACE_NAME


class UnknownRequest(TypedVarlinkErrorReply, interface=CLEARANCE_INTERFACE_NAME, paramprefix=""):
    """``Verdict`` referenced a ``request_id`` the hub didn't emit.

    Fail-closed for the attacker's dream-up case: a peer connecting to
    the clearance socket synthesises a verdict for a block that was
    never broadcast.  No binding, no action.
    """

    class Parameters:
        """Typed payload for the varlink error reply."""

        request_id: str


class VerdictTupleMismatch(
    TypedVarlinkErrorReply, interface=CLEARANCE_INTERFACE_NAME, paramprefix=""
):
    """``(container, dest)`` don't match the hub's pending record.

    Cheap defence against replay attackers who sniffed a ``request_id``
    on this connection but try to apply a verdict against a different
    destination.  ``expected_*`` are what the hub recorded when it
    emitted ``connection_blocked``; ``got_*`` are what the call
    carried.
    """

    class Parameters:
        """Typed payload for the varlink error reply."""

        expected_container: str
        expected_dest: str
        got_container: str
        got_dest: str


class InvalidAction(TypedVarlinkErrorReply, interface=CLEARANCE_INTERFACE_NAME, paramprefix=""):
    """``action`` wasn't one of ``allow`` / ``deny``."""

    class Parameters:
        """Typed payload for the varlink error reply."""

        action: str


class ShieldCliFailed(TypedVarlinkErrorReply, interface=CLEARANCE_INTERFACE_NAME, paramprefix=""):
    """``terok-shield allow|deny`` exited non-zero or timed out.

    The operator's click reached the hub, but the firewall did not accept
    it, so the notification's premise ("you decided X") would be wrong.
    This error goes back to the ``Verdict`` caller only.  The red "Allow
    failed" / "Deny failed" popups are drawn from the ``verdict_applied``
    event the hub fans out to every subscriber.  That event carries the
    same text, sanitised, as its ``reason``.  ``stderr`` is whatever
    ``terok-shield`` wrote, capped at 512 bytes by the verdict helper.
    """

    class Parameters:
        """Typed payload for the varlink error reply."""

        action: str
        stderr: str


class UnknownContainer(TypedVarlinkErrorReply, interface=CLEARANCE_INTERFACE_NAME, paramprefix=""):
    """``SetMute`` named a container this hub does not serve.

    A per-container hub knows its container by one canonical key — the
    12-char short id every event carries — plus the aliases its launcher
    registered (the full id, the podman name).  Any other key could never
    match an event, so accepting it would report a mute while every prompt
    kept coming.  ``expected`` is the canonical key the caller should have
    used.
    """

    class Parameters:
        """Typed payload for the varlink error reply."""

        container: str
        expected: str
