"""Tests for HardenedTCP2SerialCommunicator (K8: TCP connection hardening).

Reproduces the upstream bug of esp2_gateway_adapter (<= 0.2.21): when the
remote end closes the TCP connection gracefully (FIN), recv() returns b''
which the library treats as a message instead of a disconnect. The hardened
subclass must detect the EOF and reconnect.
"""
import asyncio
import logging
import socket
import threading
import time
import unittest

from enocean.protocol.constants import PACKET
from enocean.protocol.packet import Packet

from custom_components.eltako.tcp2serial_hardened import HardenedTCP2SerialCommunicator


class _GracefullyClosingServer(threading.Thread):
    """TCP server that accepts connections and closes each one after a delay.

    A graceful close (FIN) is exactly what single-client serial-over-TCP
    bridges do when e.g. another client connects.
    """

    def __init__(self, close_after: float = 0.2):
        super().__init__(daemon=True)
        self.close_after = close_after
        self.connection_count = 0
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(2)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]

    def run(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            self.connection_count += 1
            time.sleep(self.close_after)
            conn.close()  # graceful FIN
        self._sock.close()

    def stop(self):
        self._stop.set()


class _StreamAfterSilentFirstServer(threading.Thread):
    """First connection stays silent, later connections stream bytes. (R3-01)

    The silent first connection makes ``last_message_received`` go stale, so the
    inherited application-level keep-alive check closes it. With the R3-01 fix the
    NEXT connection gets a fresh grace period and - because bytes now flow again -
    stays up. Without the fix the check closes every reconnect before ``recv`` is
    ever reached: a permanent connect/close livelock (bytes never get through)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.connection_count = 0
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]

    def run(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            self.connection_count += 1
            first = self.connection_count == 1
            # per-connection handler thread so a lingering silent conn never blocks accept()
            threading.Thread(target=self._serve, args=(conn, first), daemon=True).start()
        self._sock.close()

    def _serve(self, conn, first):
        end = time.time() + 8
        try:
            while not self._stop.is_set() and time.time() < end:
                if not first:
                    try:
                        conn.sendall(b"\x01")   # non-empty, non-KEEP_ALIVE -> refreshes the timestamp
                    except OSError:
                        return
                time.sleep(0.1)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def stop(self):
        self._stop.set()


class _SplitFrameServer(threading.Thread):
    """Accepts one connection and writes a single ESP3 frame in two TCP segments
    (optionally with a keep-alive between them), each in its own send with a gap
    so they arrive as separate recv() calls. (R3-02)"""

    # A valid ESP3 4BS RadioPacket (sender 05-1E-83-15), produced once via
    # enocean RadioPacket.create(rorg=BS4, rorg_func=0x02, rorg_type=0x05,
    # sender=[0x05,0x1E,0x83,0x15]).build(). 24 bytes; converts to one
    # Regular4BSMessage under esp2_translation_enabled.
    FRAME = bytes.fromhex("55000a0701eba500000008051e83150003ffffffffff00cd")

    def __init__(self, gap: float = 0.5, keepalive_between: bool = False):
        super().__init__(daemon=True)
        self.gap = gap
        self.keepalive_between = keepalive_between
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self._sock.settimeout(2)
        self.port = self._sock.getsockname()[1]

    def run(self):
        try:
            conn, _ = self._sock.accept()
        except socket.timeout:
            self._sock.close()
            return
        try:
            conn.sendall(self.FRAME[:9])
            time.sleep(self.gap)
            if self.keepalive_between:
                conn.sendall(b"IM2M")   # HardenedTCP2SerialCommunicator.KEEP_ALIVE_MESSAGES
                time.sleep(self.gap)
            conn.sendall(self.FRAME[9:])
            # hold briefly so the communicator processes the second segment before FIN
            time.sleep(1.0)
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self._sock.close()

    def stop(self):
        self._stop.set()


class TestTcpConnectionHardening(unittest.TestCase):

    def test_reconnect_after_graceful_remote_close(self):
        """EOF (recv()==b'') must be detected as disconnect and trigger a reconnect."""
        server = _GracefullyClosingServer(close_after=0.2)
        server.start()

        states = []
        com = HardenedTCP2SerialCommunicator(
            host="127.0.0.1",
            port=server.port,
            esp2_translation_enabled=True,
            auto_reconnect=True,
            reconnection_timeout=0.1,
            tcp_keep_alive_timeout=5,
        )
        com.set_status_changed_handler(lambda connected: states.append(connected))
        com.start()

        try:
            # Without the EOF fix the thread spins forever on the first dead
            # socket and the server never sees a second connection.
            deadline = time.time() + 10
            while time.time() < deadline and server.connection_count < 2:
                time.sleep(0.05)

            self.assertGreaterEqual(
                server.connection_count, 2,
                "Communicator did not reconnect after graceful remote close (EOF)",
            )
            self.assertIn(False, states, "Disconnect was never signaled")
        finally:
            com.stop()
            com.join(5)
            server.stop()
            server.join(2)
        self.assertFalse(com.is_alive(), "Communicator thread did not stop")

    def test_no_reconnect_livelock_after_stale_keepalive(self):
        """R3-01: after the app-level keep-alive closes a stale connection, the
        reconnected connection must get a fresh grace period and stay up while
        bytes flow - not flap forever. Without the fix the timestamp stays stale
        (0), so every reconnect is closed before recv() and the count explodes."""
        server = _StreamAfterSilentFirstServer()
        server.start()

        states = []
        com = HardenedTCP2SerialCommunicator(
            host="127.0.0.1",
            port=server.port,
            esp2_translation_enabled=True,
            auto_reconnect=True,
            reconnection_timeout=0.1,
            tcp_keep_alive_timeout=0.5,
        )
        com.set_status_changed_handler(lambda connected: states.append(connected))
        com.start()
        try:
            time.sleep(4.0)
            connection_count = server.connection_count
            last_state = states[-1] if states else None
            # Fixed: the first (silent) connection is closed once by the keep-alive,
            # then the streaming reconnection stays up -> very few connections.
            # Livelock (no fix): ~1 reconnect per reconnection_timeout (0.1s) => 20+.
            self.assertLessEqual(
                connection_count, 5,
                f"reconnect livelock: {connection_count} connections (expected the "
                f"connection to stabilize after the first keep-alive close)",
            )
            self.assertIn(False, states, "the stale connection was never closed")
            self.assertTrue(last_state, "connection did not stay up after recovery")
        finally:
            com.stop()
            com.join(5)
            server.stop()
            server.join(2)
        self.assertFalse(com.is_alive(), "Communicator thread did not stop")

    def test_frame_split_across_two_segments_is_reassembled(self):
        """R3-02: a frame delivered in two TCP segments must be reassembled into
        exactly one message. The buggy `self._buffer = data` dropped the first
        segment's remainder, losing the frame entirely (0 messages)."""
        server = _SplitFrameServer(gap=0.5)
        server.start()

        received = []
        com = HardenedTCP2SerialCommunicator(
            host="127.0.0.1",
            port=server.port,
            callback=lambda m: received.append(m),
            esp2_translation_enabled=True,
            auto_reconnect=True,
            reconnection_timeout=0.2,
            tcp_keep_alive_timeout=5,
        )
        com.start()
        try:
            deadline = time.time() + 5
            while time.time() < deadline and not received:
                time.sleep(0.05)
            self.assertEqual(len(received), 1,
                             "split frame was not reassembled into exactly one message")
        finally:
            com.stop()
            com.join(5)
            server.stop()
            server.join(2)

    def test_keepalive_between_fragments_preserves_buffer(self):
        """R3-02: a keep-alive message (IM2M) arriving between the two fragments
        must not clobber the reassembly remainder - the frame still reassembles."""
        server = _SplitFrameServer(gap=0.4, keepalive_between=True)
        server.start()

        received = []
        com = HardenedTCP2SerialCommunicator(
            host="127.0.0.1",
            port=server.port,
            callback=lambda m: received.append(m),
            esp2_translation_enabled=True,
            auto_reconnect=True,
            reconnection_timeout=0.2,
            tcp_keep_alive_timeout=5,
        )
        com.start()
        try:
            deadline = time.time() + 5
            while time.time() < deadline and not received:
                time.sleep(0.05)
            self.assertEqual(len(received), 1,
                             "keep-alive between fragments broke frame reassembly")
        finally:
            com.stop()
            com.join(5)
            server.stop()
            server.join(2)


class _ScriptedServer(threading.Thread):
    """Serves one scripted list of byte chunks per accepted connection.

    ``scripts[i]`` is sent on the i-th connection (chunk by chunk, with a short
    gap so every chunk arrives as its own recv()); afterwards the connection is
    held open for ``hold`` seconds and then closed (FIN). Connections beyond the
    scripts get nothing and are held open."""

    def __init__(self, scripts, hold: float = 3.0, gap: float = 0.2):
        super().__init__(daemon=True)
        self.scripts = scripts
        self.hold = hold
        self.gap = gap
        self.connection_count = 0
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]

    def run(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            idx = self.connection_count
            self.connection_count += 1
            chunks = self.scripts[idx] if idx < len(self.scripts) else []
            threading.Thread(target=self._serve, args=(conn, chunks), daemon=True).start()
        self._sock.close()

    def _serve(self, conn, chunks):
        try:
            for chunk in chunks:
                conn.sendall(chunk)
                time.sleep(self.gap)
            end = time.time() + self.hold
            while not self._stop.is_set() and time.time() < end:
                time.sleep(0.05)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def stop(self):
        self._stop.set()


class TestMalformedFrame(unittest.TestCase):
    """K10: a radio telegram with valid CRCs but a data part too short for its
    RORG made enocean's RadioPacket.parse() raise inside Packet.parse_msg(),
    before the buffer was reassigned. The broken frame stayed in the buffer, the
    exception reached run() -> reconnect -> recv appends -> crash again: an
    endless crash/reconnect loop (observed: ~every 15 s for 6+ hours, all sends
    failing with "Serial port ... is not available")."""

    # 4BS (RORG A5) telegram with ONE data byte instead of four, sender
    # 05-1E-83-16. DB0=0x80 -> LRN bit 0 (teach-in) + "EEP included" bit set,
    # so RadioPacket.parse() slices DB3 out of an 8-bit array -> '' ->
    # "ValueError: invalid literal for int() with base 2: ''" (the exact error
    # from the field log). Header and data CRC are valid.
    BAD = bytes.fromhex("55000707017aa580051e83160001ffffffff2d0093")
    GOOD = _SplitFrameServer.FRAME

    def _make_com(self, received, logger=None, **kwargs):
        kw = dict(host="127.0.0.1", port=1, callback=received.append,
                  esp2_translation_enabled=True, auto_reconnect=True,
                  reconnection_timeout=0.1, tcp_keep_alive_timeout=30)
        if logger is not None:
            kw["logger"] = logger
        kw.update(kwargs)
        return HardenedTCP2SerialCommunicator(**kw)

    def test_bad_frame_really_crashes_upstream_parse_msg(self):
        """Guard: the fixture reproduces the field error, so the tests below
        cannot pass vacuously."""
        with self.assertRaisesRegex(ValueError, "invalid literal for int"):
            Packet.parse_msg(list(self.BAD))
        status, rest, packet = Packet.parse_msg(list(self.GOOD))
        self.assertIsNotNone(packet)
        self.assertEqual(rest, [])

    def test_parse_drops_bad_frame_and_keeps_the_following_one(self):
        received = []
        com = self._make_com(received)
        com._buffer = list(self.GOOD + self.BAD + self.GOOD)

        com.parse()   # must not raise

        self.assertEqual(len(received), 2, "a valid frame next to the broken one was lost")
        self.assertEqual(com._buffer, [], "broken frame was not removed from the buffer")
        self.assertEqual(com._malformed_frames_dropped, 1)

    def test_parse_keeps_incomplete_tail_after_bad_frame(self):
        """Dropping must be exactly one frame: a partial frame behind it stays
        for reassembly with the next recv (R3-02 still holds)."""
        received = []
        com = self._make_com(received)
        com._buffer = list(self.BAD + self.GOOD[:9])
        com.parse()
        self.assertEqual(received, [])
        com._buffer = list(com._buffer) + list(self.GOOD[9:])
        com.parse()
        self.assertEqual(len(received), 1)

    def test_drop_warning_is_throttled(self):
        logger = logging.getLogger("test.k10.throttle")
        received = []
        com = self._make_com(received, logger=logger)
        with self.assertLogs(logger, level=logging.DEBUG) as ctx:
            com._buffer = list(self.BAD + self.BAD)
            com.parse()
            com._buffer = list(self.BAD)
            com.parse()
        warnings = [r for r in ctx.records if r.levelno == logging.WARNING]
        self.assertEqual(len(warnings), 1, f"expected one throttled WARNING, got {ctx.output}")
        self.assertIn("05 1E 83 16", warnings[0].getMessage(), "frame (sender) not in warning")
        self.assertEqual(com._malformed_frames_dropped, 3)

    def test_callback_exception_does_not_escape_parse(self):
        """An exception while dispatching an already-parsed packet must not
        reach run() (it would force a pointless reconnect)."""
        calls = []

        def callback(msg):
            calls.append(msg)
            if len(calls) == 1:
                raise RuntimeError("boom")

        com = self._make_com([], callback=callback, logger=logging.getLogger("test.k10.cb"))
        com._buffer = list(self.GOOD + self.GOOD)
        with self.assertLogs("test.k10.cb", level=logging.ERROR):
            com.parse()
        self.assertEqual(len(calls), 2, "second packet was not delivered after a callback error")
        self.assertEqual(com._buffer, [])

    def _run_against(self, server, received, settle: float):
        com = self._make_com(received, port=server.port)
        states = []
        com.set_status_changed_handler(states.append)
        com.start()
        try:
            time.sleep(settle)
            return list(states)   # snapshot before stop() appends its own False
        finally:
            com.stop()
            com.join(5)
            server.stop()
            server.join(2)

    def test_bad_frame_on_the_wire_keeps_connection_up(self):
        """End-to-end: broken frame followed by a valid one on the same TCP
        connection -> valid frame delivered, no reconnect. Without K10 the
        valid frame is never delivered and the communicator reconnects every
        reconnection_timeout."""
        server = _ScriptedServer([[self.BAD, self.GOOD]], hold=3.0)
        server.start()
        received = []
        states = self._run_against(server, received, settle=2.0)
        self.assertEqual(len(received), 1, "valid frame after the broken one was not delivered")
        self.assertEqual(server.connection_count, 1, "broken frame forced a reconnect")
        # (startup may report False more than once; what matters is: connected
        # once and never disconnected again)
        self.assertIn(True, states, "never connected")
        self.assertNotIn(False, states[states.index(True):], f"connection dropped: {states}")

    def test_buffer_is_cleared_on_reconnect(self):
        """A fragment left over from a dropped connection must not be glued to
        the next connection's stream: it would corrupt (CRC error) and swallow
        the first valid frame of the new connection."""
        frag = _SplitFrameServer.FRAME[:9]
        server = _ScriptedServer([[frag], [self.GOOD]], hold=0.3)
        server.start()
        received = []
        self._run_against(server, received, settle=2.0)
        self.assertGreaterEqual(server.connection_count, 2, "no reconnect happened")
        self.assertEqual(len(received), 1,
                         "first frame of the new connection was lost to a stale fragment")


class TestKeepAliveLogNoise(unittest.TestCase):
    """K9: outgoing COMMON_COMMAND packets must not trigger enocean's
    "Replacing Packet.optional with default value." WARNING.

    enocean's Packet.__init__ logs that whenever `optional` is not a list - and
    its own default is None, so every upstream call site that omits the argument
    warns. The app-level keep-alive builds one such packet per probe (every
    tcp_keep_alive_timeout seconds of bus silence), flooding the HA log.
    """

    ENOCEAN_LOGGER = "enocean.protocol.packet"

    def _make_com(self, keep_alive_timeout=30):
        # Not started: run() never executes, so no socket is opened. Only the
        # attributes _check_timeout_on_application_level() touches are needed;
        # last_message_received is normally initialized in run().
        com = HardenedTCP2SerialCommunicator(
            host="127.0.0.1",
            port=1,
            auto_reconnect=True,
            reconnection_timeout=0.1,
            tcp_keep_alive_timeout=keep_alive_timeout,
        )
        com.last_message_received = time.time()
        return com

    def _drain(self, com):
        packets = []
        while not com.transmit.empty():
            packets.append(com.transmit.get_nowait())
        return packets

    def test_upstream_call_style_really_warns(self):
        """Guard for the two tests below: prove the WARNING exists at all, so a
        passing assertNoLogs cannot be a false negative from a wrong logger name."""
        with self.assertLogs(self.ENOCEAN_LOGGER, level=logging.WARNING) as ctx:
            Packet(PACKET.COMMON_COMMAND, data=[0x08])   # upstream: no optional=
        self.assertTrue(
            any("Replacing Packet.optional" in m for m in ctx.output),
            f"expected the enocean optional-fallback warning, got {ctx.output}",
        )

    def test_keepalive_probe_emits_no_warning(self):
        com = self._make_com(keep_alive_timeout=30)
        # Silence inside (timeout-1, timeout] -> the probe branch, not the close branch.
        com.last_message_received = time.time() - 29.5

        with self.assertNoLogs(self.ENOCEAN_LOGGER, level=logging.WARNING):
            com._check_timeout_on_application_level()

        packets = self._drain(com)
        self.assertEqual(len(packets), 1, "keep-alive probe was not enqueued")
        self.assertEqual(packets[0].data, [0x08], "probe is not CO_RD_IDBASE")
        self.assertEqual(packets[0].optional, [])

    def test_keepalive_probe_frame_is_unchanged(self):
        """The fix must be log-noise-only: the bytes on the wire stay identical
        to what the upstream (warning-emitting) call produces."""
        com = self._make_com(keep_alive_timeout=30)
        com.last_message_received = time.time() - 29.5
        com._check_timeout_on_application_level()

        fixed = self._drain(com)[0].build()
        upstream = Packet(PACKET.COMMON_COMMAND, data=[0x08]).build()
        self.assertEqual(fixed, upstream, "K9 changed the frame - it must not")

    def test_stale_connection_still_closes(self):
        """The copied method must keep the other branch intact: past the full
        timeout the socket is closed and the timestamp zeroed (-> reconnect)."""
        com = self._make_com(keep_alive_timeout=30)
        com.last_message_received = time.time() - 31

        closed = []
        com._ser = type("_FakeSock", (), {"close": lambda self: closed.append(True)})()
        com._check_timeout_on_application_level()

        self.assertEqual(closed, [True], "stale connection was not closed")
        self.assertEqual(com.last_message_received, 0)
        self.assertTrue(com.transmit.empty(), "no probe may be sent on the close path")

    def test_base_id_and_version_requests_emit_no_warning(self):
        """The two requests fired per (re)connect from
        EltakoGateway.query_for_base_id_and_version(). Also a regression guard
        for the super().send() trap: these must actually enqueue a Packet, not
        leave an un-awaited coroutine behind."""
        com = self._make_com()

        with self.assertNoLogs(self.ENOCEAN_LOGGER, level=logging.WARNING):
            asyncio.run(com.send_base_id_request())
            asyncio.run(com.send_version_request())

        packets = self._drain(com)
        self.assertEqual(len(packets), 2, "requests were not enqueued")
        for p in packets:
            self.assertIsInstance(p, Packet)
            self.assertEqual(p.packet_type, PACKET.COMMON_COMMAND)
            self.assertEqual(p.optional, [])
        self.assertEqual([p.data for p in packets], [[0x08], [0x03]])


if __name__ == "__main__":
    unittest.main()
