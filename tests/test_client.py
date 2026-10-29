# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""End-to-end ClearanceClient ↔ ClearanceHub tests over a real varlink socket.

Spins a [`ClearanceHub`][terok_clearance.ClearanceHub] on a throwaway unix socket in a
per-test-temp directory, points a [`ClearanceClient`][terok_clearance.ClearanceClient] at it, and
exercises the full Subscribe + Verdict round-trips.  The reader→hub
side is driven by a raw unix-socket writer (mimicking terok-shield's
``SocketEmitter``) so the tests don't depend on any real reader
subprocess.

Shield exec is stubbed at the hub level so no actual ``terok-shield``
binary is spawned.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from terok_clearance.client.client import (
    ClearanceClient,
    container_mutes,
    set_container_mute,
)
from terok_clearance.client.subscriber import EventSubscriber
from terok_clearance.hub.server import ClearanceHub
from terok_clearance.wire.errors import UnknownContainer

from .conftest import (
    CONTAINER,
    CONTAINER_FULL_ID,
    CONTAINER_NAME,
    CONTAINER_SHORT_ID,
    DEST_IP,
    DOMAIN,
    OTHER_SHORT_ID,
    TIER_REFUSAL_ADVICE,
    TIER_REFUSAL_STDERR,
)

# ── Fixtures ──────────────────────────────────────────────────────────


async def _stub_shield_ok(*args, **kwargs) -> tuple[bool, str]:  # noqa: ANN002,ANN003
    return True, ""


async def _stub_shield_fail(*args, **kwargs) -> tuple[bool, str]:  # noqa: ANN002,ANN003
    return False, "nft lock"


@pytest.fixture
async def hub(private_runtime_dir: Path) -> AsyncIterator[ClearanceHub]:
    """A started [`ClearanceHub`][terok_clearance.ClearanceHub] with a stubbed shield exec."""
    h = ClearanceHub(
        clearance_socket=private_runtime_dir / "clearance.sock",
        reader_socket=private_runtime_dir / "reader.sock",
    )
    h._verdict_client.apply = _stub_shield_ok  # default: every verdict succeeds
    await h.start()
    try:
        yield h
    finally:
        with contextlib.suppress(Exception):
            await h.stop()


@pytest.fixture
async def client(hub: ClearanceHub) -> AsyncIterator[tuple[ClearanceClient, list]]:
    """A connected [`ClearanceClient`][terok_clearance.ClearanceClient] + a list that collects its events."""
    received: list = []

    async def on_event(event) -> None:  # noqa: ANN001
        received.append(event)

    c = ClearanceClient(socket_path=hub._clearance_socket)
    await c.start(on_event)
    # Give the Subscribe() call a moment to register on the hub.
    await asyncio.sleep(0.05)
    try:
        yield c, received
    finally:
        with contextlib.suppress(Exception):
            await c.stop()


def _emit_reader_event(sock_path: Path, payload: dict) -> None:
    """Write one JSON line to the hub's reader socket — same shape the real reader uses."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(sock_path))
    s.sendall((json.dumps(payload) + "\n").encode())
    s.close()


# ── Subscribe round-trips ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connection_blocked_reaches_subscriber(
    hub: ClearanceHub, client: tuple[ClearanceClient, list]
) -> None:
    """A reader 'pending' event surfaces as a connection_blocked ClearanceEvent."""
    _, received = client
    _emit_reader_event(
        hub._reader_socket,
        {
            "type": "pending",
            "container": CONTAINER,
            "id": f"{CONTAINER}:1",
            "dest": DEST_IP,
            "port": 443,
            "proto": 6,
            "domain": DOMAIN,
        },
    )
    await asyncio.sleep(0.1)
    assert any(e.type == "connection_blocked" for e in received)
    block = next(e for e in received if e.type == "connection_blocked")
    assert block.container == CONTAINER
    assert block.request_id == f"{CONTAINER}:1"
    assert block.domain == DOMAIN


@pytest.mark.asyncio
async def test_multiple_event_types_flow_through(
    hub: ClearanceHub, client: tuple[ClearanceClient, list]
) -> None:
    """Lifecycle + shield-state events land with the right discriminator."""
    _, received = client
    for payload in (
        {"type": "container_started", "container": CONTAINER},
        {"type": "shield_down", "container": CONTAINER},
        {"type": "container_exited", "container": CONTAINER, "reason": "poststop"},
    ):
        _emit_reader_event(hub._reader_socket, payload)
    await asyncio.sleep(0.1)
    types = [e.type for e in received]
    assert "container_started" in types
    assert "shield_down" in types
    assert "container_exited" in types


# ── Verdict round-trips ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verdict_success_returns_true_and_fans_out(
    hub: ClearanceHub, client: tuple[ClearanceClient, list]
) -> None:
    """Successful Verdict returns True + a verdict_applied event broadcasts."""
    c, received = client
    _emit_reader_event(
        hub._reader_socket,
        {
            "type": "pending",
            "container": CONTAINER,
            "id": f"{CONTAINER}:1",
            "dest": DEST_IP,
            "port": 443,
            "proto": 6,
            "domain": DOMAIN,
        },
    )
    await asyncio.sleep(0.1)

    ok = await c.verdict(CONTAINER, f"{CONTAINER}:1", DOMAIN, "allow")
    await asyncio.sleep(0.1)

    assert ok is True
    verdict_events = [e for e in received if e.type == "verdict_applied"]
    assert len(verdict_events) == 1
    assert verdict_events[0].ok is True
    assert verdict_events[0].action == "allow"


@pytest.mark.asyncio
async def test_verdict_with_unknown_request_id_returns_false(
    hub: ClearanceHub, client: tuple[ClearanceClient, list]
) -> None:
    """Blind verdicts are refused at the hub and collapsed to False client-side."""
    c, _ = client
    ok = await c.verdict(CONTAINER, "never-seen:99", DOMAIN, "allow")
    assert ok is False


@pytest.mark.asyncio
async def test_verdict_with_tuple_mismatch_returns_false(
    hub: ClearanceHub, client: tuple[ClearanceClient, list]
) -> None:
    """Request_id the hub emitted but for a different (container, dest) is refused."""
    c, _ = client
    _emit_reader_event(
        hub._reader_socket,
        {
            "type": "pending",
            "container": CONTAINER,
            "id": f"{CONTAINER}:1",
            "dest": DEST_IP,
            "port": 443,
            "proto": 6,
            "domain": DOMAIN,
        },
    )
    await asyncio.sleep(0.1)

    ok = await c.verdict("wrong-container", f"{CONTAINER}:1", DOMAIN, "allow")
    assert ok is False


@pytest.mark.asyncio
async def test_verdict_with_invalid_action_returns_false(
    hub: ClearanceHub, client: tuple[ClearanceClient, list]
) -> None:
    """``action`` outside {allow, deny} is refused at the hub."""
    c, _ = client
    ok = await c.verdict(CONTAINER, f"{CONTAINER}:1", DOMAIN, "maybe")
    assert ok is False


@pytest.mark.asyncio
async def test_verdict_shield_failure_returns_false_and_fans_out_failure(
    hub: ClearanceHub, client: tuple[ClearanceClient, list]
) -> None:
    """Shield's non-zero exit raises on the caller AND fans out ok=False."""
    hub._verdict_client.apply = _stub_shield_fail
    c, received = client
    _emit_reader_event(
        hub._reader_socket,
        {
            "type": "pending",
            "container": CONTAINER,
            "id": f"{CONTAINER}:1",
            "dest": DEST_IP,
            "port": 443,
            "proto": 6,
            "domain": DOMAIN,
        },
    )
    await asyncio.sleep(0.1)

    ok = await c.verdict(CONTAINER, f"{CONTAINER}:1", DOMAIN, "allow")
    await asyncio.sleep(0.1)

    assert ok is False
    verdict_events = [e for e in received if e.type == "verdict_applied"]
    assert len(verdict_events) == 1
    assert verdict_events[0].ok is False


async def _eventually(condition: Callable[[], bool], *, timeout_s: float = 2.0) -> None:
    """Poll *condition* until it holds: a varlink round trip signals nothing on completion."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not condition():
        if loop.time() > deadline:
            raise AssertionError(f"condition did not hold within {timeout_s}s")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_tier_refusal_explains_itself_in_the_popup(hub: ClearanceHub) -> None:
    """The security tier's "use an override" line reaches the operator, not just a log.

    The whole path: the reader's prompt, the operator's Allow click, the
    subscriber's ``Verdict`` (whose reply it never reads), shield's refusal,
    and the hub's ``verdict_applied`` fan-out, which is what the failure
    popup is drawn from.
    """
    hub._verdict_client.apply = AsyncMock(return_value=(False, TIER_REFUSAL_STDERR))
    notifier = AsyncMock()
    notifier.notify = AsyncMock(return_value=1)
    subscriber = EventSubscriber(notifier, socket_path=hub._clearance_socket)
    await subscriber.start()
    try:
        await _eventually(lambda: bool(hub._subscribers))
        _emit_reader_event(
            hub._reader_socket,
            {
                "type": "pending",
                "container": CONTAINER,
                "id": f"{CONTAINER}:1",
                "dest": DEST_IP,
                "port": 443,
                "proto": 6,
                "domain": DOMAIN,
            },
        )
        await _eventually(lambda: notifier.on_action.await_count == 1)
        click = notifier.on_action.await_args.args[1]
        click("allow")
        await _eventually(lambda: notifier.notify.await_count == 2)
    finally:
        await subscriber.stop()

    title, body = notifier.notify.await_args.args[:2]
    assert title == f"Allow failed: {DOMAIN}"
    assert TIER_REFUSAL_ADVICE in body


# ── Client lifecycle ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verdict_before_start_returns_false() -> None:
    """Calling ``verdict()`` without ``start()`` is a logged no-op."""
    c = ClearanceClient(socket_path=Path("/dev/null"))
    ok = await c.verdict(CONTAINER, f"{CONTAINER}:1", DOMAIN, "allow")
    assert ok is False


# ── Reconnect loop ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconnects_after_hub_restart(private_runtime_dir: Path) -> None:
    """Hub stops + restarts → client's Subscribe loop rebinds automatically."""
    sock_path = private_runtime_dir / "clearance.sock"
    reader_path = private_runtime_dir / "reader.sock"
    hub_1 = ClearanceHub(clearance_socket=sock_path, reader_socket=reader_path)
    hub_1._verdict_client.apply = _stub_shield_ok
    await hub_1.start()

    received: list = []

    async def on_event(event) -> None:  # noqa: ANN001
        received.append(event)

    c = ClearanceClient(socket_path=sock_path)
    await c.start(on_event)
    await asyncio.sleep(0.05)

    # Drop the hub, stand a fresh one up on the same path.
    await hub_1.stop()
    await asyncio.sleep(0.05)  # let the client's stream task observe the reset
    hub_2 = ClearanceHub(clearance_socket=sock_path, reader_socket=reader_path)
    hub_2._verdict_client.apply = _stub_shield_ok
    await hub_2.start()

    # Skip the client's back-off sleep so the test doesn't drag.
    c.poke_reconnect()
    for _ in range(50):
        await asyncio.sleep(0.05)
        if c._sub_proxy is not None:
            break

    _emit_reader_event(
        reader_path,
        {
            "type": "pending",
            "container": CONTAINER,
            "id": f"{CONTAINER}:2",
            "dest": DEST_IP,
            "port": 443,
            "proto": 6,
            "domain": DOMAIN,
        },
    )
    await asyncio.sleep(0.2)
    try:
        assert any(e.request_id == f"{CONTAINER}:2" for e in received)
    finally:
        with contextlib.suppress(Exception):
            await c.stop()
        with contextlib.suppress(Exception):
            await hub_2.stop()


@pytest.mark.asyncio
async def test_poke_reconnect_is_noop_when_healthy(
    client: tuple[ClearanceClient, list],
) -> None:
    """Poking a connected client must not disturb the live Subscribe() stream."""
    c, _received = client
    c.poke_reconnect()
    await asyncio.sleep(0.05)
    # The internal event was set, but nothing awaits it while the stream
    # loop is inside Subscribe() — verify the client is still serviceable
    # by issuing a verdict.  Any malformed teardown would surface here.
    ok = await c.verdict(CONTAINER, f"{CONTAINER}:unknown", DOMAIN, "allow")
    assert ok is False  # unknown request_id refusal


@pytest.mark.asyncio
async def test_event_callback_exception_is_logged_but_stream_survives(
    hub: ClearanceHub, client: tuple[ClearanceClient, list]
) -> None:
    """A raising callback doesn't kill the stream — next event still arrives."""
    c, received = client
    calls = {"n": 0}

    async def flaky(event) -> None:  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first event blows up")
        received.append(event)

    c._on_event = flaky
    _emit_reader_event(
        hub._reader_socket,
        {
            "type": "pending",
            "container": CONTAINER,
            "id": f"{CONTAINER}:1",
            "dest": DEST_IP,
            "port": 443,
            "proto": 6,
            "domain": "a.example",
        },
    )
    await asyncio.sleep(0.1)
    _emit_reader_event(
        hub._reader_socket,
        {
            "type": "pending",
            "container": CONTAINER,
            "id": f"{CONTAINER}:2",
            "dest": DEST_IP,
            "port": 443,
            "proto": 6,
            "domain": "b.example",
        },
    )
    await asyncio.sleep(0.1)
    assert any(e.request_id == f"{CONTAINER}:2" for e in received)


@pytest.mark.asyncio
async def test_start_rollback_on_partial_connect_failure(tmp_path: Path) -> None:
    """If the second transport dial fails, no live socket leaks behind."""
    import terok_clearance.client.client as client_mod

    sock = tmp_path / "clearance.sock"
    first_transport = None
    calls = {"n": 0}

    async def flaky_connect(*args, **kwargs):  # noqa: ANN002,ANN003
        nonlocal first_transport
        calls["n"] += 1
        if calls["n"] == 1:
            # Succeed — the test wants this transport rolled back on failure.
            # MagicMock for the transport because _close_transports calls
            # ``t.close()`` synchronously; AsyncMock would produce an
            # un-awaited coroutine warning.
            first_transport = MagicMock()
            return (first_transport, MagicMock())
        raise OSError("simulated second-connect failure")

    c = ClearanceClient(socket_path=sock)
    with patch.object(client_mod, "connect_unix_varlink", flaky_connect):
        with pytest.raises(OSError, match="simulated second-connect failure"):
            await c.start(lambda _: None)

    assert c._sub_transport is None
    assert c._rpc_transport is None
    assert first_transport is not None
    first_transport.close.assert_called()


# ── Mute: identity, read-back and failure semantics ───────────────────


def _pending(request_id: str, container: str = CONTAINER_SHORT_ID) -> dict:
    """A reader ``pending`` line keyed the way the shield reader keys it: by short id."""
    return {
        "type": "pending",
        "container": container,
        "id": request_id,
        "dest": DEST_IP,
        "port": 443,
        "proto": 6,
        "domain": DOMAIN,
    }


def _blocked_ids(received: list) -> list[str]:
    """The request ids of every prompt a subscriber was shown."""
    return [e.request_id for e in received if e.type == "connection_blocked"]


@pytest.fixture
async def container_hub(private_runtime_dir: Path) -> AsyncIterator[ClearanceHub]:
    """A started hub built for one container, as the supervisor builds it, seeded muted."""
    h = ClearanceHub(
        clearance_socket=private_runtime_dir / "clearance.sock",
        reader_socket=private_runtime_dir / "reader.sock",
        container=CONTAINER_SHORT_ID,
        aliases=(CONTAINER_FULL_ID, CONTAINER_NAME),
        muted=[CONTAINER_SHORT_ID],
    )
    h._verdict_client.apply = _stub_shield_ok
    await h.start()
    try:
        yield h
    finally:
        with contextlib.suppress(Exception):
            await h.stop()


@pytest.mark.asyncio
async def test_set_mute_stops_the_prompts_end_to_end(
    hub: ClearanceHub, client: tuple[ClearanceClient, list]
) -> None:
    """A muted container's refusal never reaches the subscriber; unmuting restores it."""
    c, received = client

    assert await c.set_mute(CONTAINER_SHORT_ID, True) is True
    _emit_reader_event(hub._reader_socket, _pending(f"{CONTAINER_SHORT_ID}:1"))
    await asyncio.sleep(0.1)
    assert _blocked_ids(received) == []

    assert await c.set_mute(CONTAINER_SHORT_ID, False) is False
    _emit_reader_event(hub._reader_socket, _pending(f"{CONTAINER_SHORT_ID}:2"))
    await _eventually(lambda: bool(_blocked_ids(received)))
    assert _blocked_ids(received) == [f"{CONTAINER_SHORT_ID}:2"]


@pytest.mark.asyncio
async def test_client_reads_the_mute_set_back(
    hub: ClearanceHub, client: tuple[ClearanceClient, list]
) -> None:
    """``mutes()`` reports the hub's state in the keys events carry."""
    c, _ = client

    assert await c.mutes() == frozenset()
    await c.set_mute(CONTAINER_SHORT_ID, True)

    assert await c.mutes() == frozenset({CONTAINER_SHORT_ID})


@pytest.mark.asyncio
async def test_set_mute_without_a_connection_is_unknown(tmp_path: Path) -> None:
    """With no connection the hub heard nothing, so the answer is "unknown", not a guess."""
    c = ClearanceClient(socket_path=tmp_path / "absent.sock")

    assert await c.set_mute(CONTAINER_SHORT_ID, True) is None
    assert await c.mutes() is None


@pytest.mark.asyncio
async def test_set_mute_refused_by_the_hub_is_unknown(tmp_path: Path) -> None:
    """A refused mute must not be reported as applied — the prompts are still coming."""
    from asyncvarlink import GenericVarlinkErrorReply

    c = ClearanceClient(socket_path=tmp_path / "absent.sock")
    c._rpc_proxy = SimpleNamespace(
        SetMute=AsyncMock(
            side_effect=GenericVarlinkErrorReply(
                UnknownContainer.name, {"container": OTHER_SHORT_ID, "expected": CONTAINER_SHORT_ID}
            )
        )
    )

    assert await c.set_mute(CONTAINER_SHORT_ID, True) is None


@pytest.mark.asyncio
async def test_set_mute_on_a_dropped_connection_is_unknown(tmp_path: Path) -> None:
    """A hub that went away mid-call leaves the state unknown, not unchanged."""
    c = ClearanceClient(socket_path=tmp_path / "absent.sock")
    c._rpc_proxy = SimpleNamespace(SetMute=AsyncMock(side_effect=ConnectionResetError()))

    assert await c.set_mute(CONTAINER_SHORT_ID, True) is None


@pytest.mark.asyncio
async def test_mutes_from_a_hub_that_predates_it_is_unknown(tmp_path: Path) -> None:
    """An old supervisor answers MethodNotFound; that is "unknown", never "not muted"."""
    from asyncvarlink import GenericVarlinkErrorReply
    from asyncvarlink.serviceinterface import MethodNotFound

    c = ClearanceClient(socket_path=tmp_path / "absent.sock")
    c._rpc_proxy = SimpleNamespace(
        Mutes=AsyncMock(
            side_effect=GenericVarlinkErrorReply(MethodNotFound.name, {"method": "Mutes"})
        )
    )

    assert await c.mutes() is None


@pytest.mark.asyncio
async def test_one_shots_need_no_subscription(hub: ClearanceHub) -> None:
    """Mute and read-back each run over their own connection, with no event stream open."""
    sock = hub._clearance_socket

    assert await set_container_mute(sock, CONTAINER_SHORT_ID, True) is True
    assert await container_mutes(sock) == frozenset({CONTAINER_SHORT_ID})
    assert await set_container_mute(sock, CONTAINER_SHORT_ID, False) is False
    assert await container_mutes(sock) == frozenset()


@pytest.mark.asyncio
async def test_one_shots_raise_when_the_hub_is_unreachable(private_runtime_dir: Path) -> None:
    """A one-shot caller has nothing else to show, so an absent hub is an error."""
    absent = private_runtime_dir / "absent.sock"

    with pytest.raises(OSError):
        await set_container_mute(absent, CONTAINER_SHORT_ID, True)
    with pytest.raises(OSError):
        await container_mutes(absent)


@pytest.mark.asyncio
async def test_launch_default_mutes_from_the_first_event(
    container_hub: ClearanceHub,
) -> None:
    """A hub seeded muted drops the first refusal, and says so when asked."""
    received: list = []

    async def on_event(event) -> None:  # noqa: ANN001
        received.append(event)

    c = ClearanceClient(socket_path=container_hub._clearance_socket)
    await c.start(on_event)
    try:
        await _eventually(lambda: bool(container_hub._subscribers))
        _emit_reader_event(container_hub._reader_socket, _pending(f"{CONTAINER_SHORT_ID}:1"))
        await asyncio.sleep(0.1)  # absence cannot be polled for: give a leak time to surface

        assert _blocked_ids(received) == []
        assert await container_mutes(container_hub._clearance_socket) == frozenset(
            {CONTAINER_SHORT_ID}
        )
    finally:
        with contextlib.suppress(Exception):
            await c.stop()


@pytest.mark.asyncio
async def test_one_shot_mute_by_alias_lands_on_the_short_id(
    container_hub: ClearanceHub,
) -> None:
    """The podman name is a registered alias: it unmutes the short id, the key events carry."""
    sock = container_hub._clearance_socket

    assert await set_container_mute(sock, CONTAINER_NAME, False) is False

    assert await container_mutes(sock) == frozenset()


@pytest.mark.asyncio
async def test_one_shot_mute_of_a_foreign_container_raises(
    container_hub: ClearanceHub,
) -> None:
    """A key this hub cannot match comes back as UnknownContainer, not as a quiet success."""
    from asyncvarlink import VarlinkErrorReply

    sock = container_hub._clearance_socket

    with pytest.raises(VarlinkErrorReply) as excinfo:
        await set_container_mute(sock, OTHER_SHORT_ID, True)

    assert excinfo.value.name == UnknownContainer.name
    assert await container_mutes(sock) == frozenset({CONTAINER_SHORT_ID})


@pytest.mark.asyncio
async def test_host_side_events_reach_subscribers_under_the_short_id(
    container_hub: ClearanceHub,
) -> None:
    """A shield drop sent under the podman name arrives keyed like every reader event."""
    received: list = []

    async def on_event(event) -> None:  # noqa: ANN001
        received.append(event)

    c = ClearanceClient(socket_path=container_hub._clearance_socket)
    await c.start(on_event)
    try:
        await _eventually(lambda: bool(container_hub._subscribers))
        _emit_reader_event(
            container_hub._reader_socket, {"type": "shield_down", "container": CONTAINER_NAME}
        )
        await _eventually(lambda: bool(received))
        await asyncio.sleep(0.1)  # absence cannot be polled for: give a duplicate time to land

        assert [(e.type, e.container) for e in received] == [("shield_down", CONTAINER_SHORT_ID)]
    finally:
        with contextlib.suppress(Exception):
            await c.stop()


@pytest.mark.asyncio
async def test_event_subscriber_hands_mute_calls_to_its_client() -> None:
    """``EventSubscriber`` owns one hub connection, so both mute calls go straight through it."""
    from terok_clearance import EventSubscriber

    client = SimpleNamespace(
        set_mute=AsyncMock(return_value=True),
        mutes=AsyncMock(return_value=frozenset({CONTAINER_SHORT_ID})),
    )
    subscriber = EventSubscriber(MagicMock(), client=client)  # type: ignore[arg-type]

    assert await subscriber.set_mute(CONTAINER_SHORT_ID, True) is True
    assert await subscriber.mutes() == frozenset({CONTAINER_SHORT_ID})
    client.set_mute.assert_awaited_once_with(CONTAINER_SHORT_ID, True)
