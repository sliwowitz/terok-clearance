# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The ``org.terok.Clearance1`` varlink interface — events out, verdicts in.

Subscribers call ``Subscribe()`` with ``more=true`` and receive a stream
of [`ClearanceEvent`][terok_clearance.domain.events.ClearanceEvent] records until
the connection closes.  Verdict clients call ``Verdict()`` as a regular
RPC; the hub validates the ``(container, request_id, dest)`` triple
against its live-pending state before shelling out to ``terok-shield``.

The whole surface is typed-Python → `asyncvarlink` derives the
varlink IDL from these annotations, so there's no hand-authored
``.varlink`` file to drift from the code.  ``varlinkctl`` can still
introspect the service at runtime via the standard
``org.varlink.service.GetInterfaceDescription`` method.
"""

from collections.abc import AsyncIterator, Awaitable, Callable

from asyncvarlink import VarlinkInterface, varlinkmethod

from terok_clearance.domain.events import ClearanceEvent

#: Interface name used for varlink dispatch and ``varlinkctl`` introspection.
#: The transport is a plain unix socket (no D-Bus daemon in the loop); the
#: name lives purely as the introspection identifier.
CLEARANCE_INTERFACE_NAME = "org.terok.Clearance1"


class Clearance1Interface(VarlinkInterface, name=CLEARANCE_INTERFACE_NAME):
    """Varlink interface served by the clearance hub.

    The hub's behaviour is injected as callables so the state machine
    stays testable without a live varlink connection:

    * ``event_stream_factory`` — returns a fresh ``AsyncIterator``
      yielding [`ClearanceEvent`][terok_clearance.ClearanceEvent] instances.  The hub owns one
      per connected subscriber so backpressure is local to the slow
      client.
    * ``apply_verdict`` — validates the triple and, on success, shells
      out to ``terok-shield``.  Raises a typed varlink error for any
      refusal path; returns ``True`` only when the shield invocation
      itself succeeded.
    * ``set_mute`` — stops (or resumes) the prompts for one container.
      Mute is about who is asked, never about what the shield does: the
      connection was already refused before the hub saw it.
    * ``mutes`` — reads the mute set back, so a UI can show the state the
      hub actually holds instead of the last thing it asked for.

    Containers are addressed by the key their events carry in
    ``ClearanceEvent.container``: under the terok supervisor, the 12-char
    short container id — never the podman name or a task name.
    """

    def __init__(
        self,
        event_stream_factory: Callable[[], AsyncIterator[ClearanceEvent]],
        apply_verdict: Callable[[str, str, str, str], Awaitable[bool]],
        set_mute: Callable[[str, bool], Awaitable[bool]],
        mutes: Callable[[], Awaitable[list[str]]],
    ) -> None:
        """Bind the event stream factory and the verdict, mute and mute-query callables."""
        self._event_stream_factory = event_stream_factory
        self._apply_verdict = apply_verdict
        self._set_mute = set_mute
        self._mutes = mutes

    @varlinkmethod(return_parameter="event", delay_generator=False)
    async def Subscribe(self) -> AsyncIterator[ClearanceEvent]:  # noqa: N802
        """Stream hub events to this caller until the connection closes.

        Every yield is forwarded immediately with ``continues=true``;
        the stream ends only when the client disconnects.  A buffered
        (``delay_generator=True``) stream would hold the first event
        until a second arrives, breaking the "something just happened"
        liveness contract operators expect from a notification channel.
        """
        async for event in self._event_stream_factory():
            yield event

    @varlinkmethod(return_parameter="ok")
    async def Verdict(  # noqa: N802
        self, *, container: str, request_id: str, dest: str, action: str
    ) -> bool:
        """Apply *action* (``allow`` / ``deny``) to *dest* for *container*.

        Returns ``True`` when ``terok-shield`` accepted the verdict.
        Raises [`UnknownRequest`][terok_clearance.wire.errors.UnknownRequest],
        [`VerdictTupleMismatch`][terok_clearance.wire.errors.VerdictTupleMismatch],
        [`InvalidAction`][terok_clearance.wire.errors.InvalidAction], or
        [`ShieldCliFailed`][terok_clearance.wire.errors.ShieldCliFailed] on the
        four refusal paths — clients get a typed error they can render
        without stringly-matching the message.

        Once shield has run, every subscriber also sees the outcome as a
        ``verdict_applied`` event.  When shield refused, the event's
        ``reason`` holds shield's stderr, so each popup can say why.
        """
        return await self._apply_verdict(container, request_id, dest, action)

    @varlinkmethod(return_parameter="muted")
    async def SetMute(self, *, container: str, muted: bool) -> bool:  # noqa: N802
        """Stop (``muted``) or resume prompting for *container*'s refusals.

        *container* is the key the container's events carry — the 12-char
        short id under the terok supervisor.  A hub that knows which
        container it serves refuses any other key with
        [`UnknownContainer`][terok_clearance.wire.errors.UnknownContainer]
        rather than parking it where no event will ever match.

        Returns the mute state now in force.  Muting suppresses the
        ``connection_blocked`` prompt only: the connection was refused by the
        shield before the hub ever saw it.  Nothing is auto-allowed, and
        nothing is auto-denied either — a mute changes what an operator is
        asked, not what the firewall did.

        The mute does not decide whether the refusal is recorded either.
        The reader still writes it to the container's audit log while the
        task's shield audit is enabled.  With audit off, a muted refusal
        leaves no record at all: no prompt and no audit line.
        """
        return await self._set_mute(container, muted)

    @varlinkmethod(return_parameter="containers")
    async def Mutes(self) -> list[str]:  # noqa: N802
        """Return the containers whose prompts this hub is dropping, sorted.

        The read-back half of ``SetMute``, in the same keys events carry.  A
        hub restart returns to its launch default, so a UI that remembered
        its own last request would drift; asking the hub cannot.  Hubs that
        predate this method answer ``org.varlink.service.MethodNotFound`` —
        callers show that as "unknown", never as "not muted".
        """
        return await self._mutes()
