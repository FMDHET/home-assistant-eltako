"""Tests for the hardened Virtual Network Gateway TCP server (H8, M11).

Covers:
- server starts, accepts clients, sends keep-alives and forwards messages
- H8a: restart on the same port works (SO_REUSEADDR); a failed bind does not
  leave a dead server behind that still claims to be running
- H8b: _forward_message survives clients that are disconnecting concurrently
- H8c: bounded per-client queues drop messages instead of growing unbounded
- H8d: stop_tcp_server() closes client connections so handler threads exit
"""
import asyncio
import queue
import socket
import time
import unittest

from tests.mocks import *
from custom_components.eltako.virtual_network_gateway import VirtualNetworkGateway
from eltakobus import *


def get_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class VNGMock(VirtualNetworkGateway):

    def __init__(self, port: int):
        super().__init__(DEFAULT_GENERAL_SETTINGS, HassMock(), 123, port, ConfigEntryMock())
        self.host = "127.0.0.1"     # do not open a real 0.0.0.0 listener in tests

    def _register_device(self) -> None:
        pass

    def _fire_connection_state_changed_event(self, status):
        pass

    def add_connection_state_changed_handler(self, handler):
        pass


def connect_when_listening(port: int, timeout: float = 5.0) -> socket.socket:
    """Connect to the server, retrying until the listener is up."""
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            return socket.create_connection(("127.0.0.1", port), timeout=1)
        except OSError as e:
            last_error = e
            time.sleep(0.05)
    raise AssertionError(f"server did not accept connections within {timeout}s: {last_error}")


class TestVirtualNetworkGateway(unittest.TestCase):

    def test_client_receives_keep_alive_and_forwarded_message(self):
        vng = VNGMock(get_free_port())
        vng.start_tcp_server()
        try:
            client = connect_when_listening(vng.port)
            client.settimeout(5)

            # keep-alive arrives after ~1s of queue inactivity
            received = client.recv(1024)
            self.assertIn(b'IM2M', received)

            # a message forwarded from a gateway reaches the client serialized
            msg = RPSMessage(b'\xfe\xdb\xb6\x40', status=0x30, data=b'\x70')
            asyncio.run(vng._forward_message({'gateway': GatewayMock(), 'esp2_msg': msg}))

            buffer = b''
            deadline = time.time() + 5
            while msg.serialize() not in buffer and time.time() < deadline:
                buffer += client.recv(1024)
            self.assertIn(msg.serialize(), buffer)
            client.close()
        finally:
            vng.stop_tcp_server()

    def test_r3_23_client_eof_detected_via_drain(self):
        """R3-23: the handler now drains the client socket, so a client half-close (EOF) is
        detected promptly and the handler exits. Without the drain the socket was never read,
        so a write-shut client was never noticed here (and its inbound bytes would pile up)."""
        vng = VNGMock(get_free_port())
        vng.start_tcp_server()
        try:
            client = connect_when_listening(vng.port)
            client.settimeout(5)
            self.assertIn(b'IM2M', client.recv(1024))   # connected + first keep-alive

            # client stops writing -> server's drain recv() reads EOF (b'') -> handler exits
            client.shutdown(socket.SHUT_WR)

            deadline = time.time() + 5
            while vng.connected_clients and time.time() < deadline:
                time.sleep(0.1)
            self.assertEqual(vng.connected_clients, [], "server did not detect the client EOF via the drain")
            client.close()
        finally:
            vng.stop_tcp_server()

    def test_forward_message_survives_disconnecting_client(self):
        """H8b: a client without a queue (mid-disconnect) must not crash the loop."""
        vng = VNGMock(get_free_port())
        fake_conn = object()
        vng.connected_clients.append(fake_conn)     # client listed, queue already gone
        msg = RPSMessage(b'\xfe\xdb\xb6\x40', status=0x30, data=b'\x70')
        asyncio.run(vng._forward_message({'gateway': GatewayMock(), 'esp2_msg': msg}))  # must not raise

    def test_forward_message_drops_when_queue_full(self):
        """H8c: bounded queue drops messages instead of growing unbounded."""
        vng = VNGMock(get_free_port())
        fake_conn = object()
        vng.connected_clients.append(fake_conn)
        vng.incoming_message_queues[fake_conn] = queue.Queue(maxsize=1)
        msg = RPSMessage(b'\xfe\xdb\xb6\x40', status=0x30, data=b'\x70')
        asyncio.run(vng._forward_message({'gateway': GatewayMock(), 'esp2_msg': msg}))
        asyncio.run(vng._forward_message({'gateway': GatewayMock(), 'esp2_msg': msg}))  # queue full: must not raise
        self.assertEqual(vng.incoming_message_queues[fake_conn].qsize(), 1)

    def test_stop_closes_client_connections(self):
        """H8d: stop_tcp_server() must close clients and empty the client list."""
        vng = VNGMock(get_free_port())
        vng.start_tcp_server()
        client = connect_when_listening(vng.port)
        client.settimeout(5)

        deadline = time.time() + 5
        while len(vng.connected_clients) == 0 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(len(vng.connected_clients), 1)

        vng.stop_tcp_server()

        # server closed the connection => recv unblocks with EOF (or reset)
        try:
            data = client.recv(1024)
            while data:     # drain remaining keep-alives until EOF (b'' is falsy)
                data = client.recv(1024)
        except OSError:
            pass
        client.close()

        deadline = time.time() + 5
        while len(vng.connected_clients) > 0 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(len(vng.connected_clients), 0, "client handler threads did not clean up")
        self.assertFalse(vng._running.is_set())
        self.assertIsNone(vng.tcp_thread)

    def test_restart_on_same_port(self):
        """H8a: after stop, the server must be able to rebind the same port immediately."""
        port = get_free_port()
        vng = VNGMock(port)
        vng.start_tcp_server()
        client = connect_when_listening(port)
        client.close()
        vng.stop_tcp_server()

        vng.start_tcp_server()
        try:
            client = connect_when_listening(port)
            client.close()
        finally:
            vng.stop_tcp_server()

    def test_fresh_stop_token_per_generation_and_shutdown_guard(self):
        """Review fix: each server generation gets its own stop token, so a zombie
        thread from a timed-out stop cannot tear down its successor; after unload
        no new server may start (reconnect-vs-unload race)."""
        port = get_free_port()
        vng = VNGMock(port)
        vng.start_tcp_server()
        first_token = vng._running
        vng.stop_tcp_server()
        vng.start_tcp_server()
        second_token = vng._running
        try:
            self.assertIsNot(first_token, second_token, "stop token must be per generation")
            # a zombie clearing its STALE token must not stop the current server
            first_token.clear()
            self.assertTrue(second_token.is_set())
            client = connect_when_listening(port)
            client.close()
        finally:
            vng.stop_tcp_server()

        # unload: subsequent start attempts (e.g. late reconnect) must be refused
        vng._unload_blocking()
        vng.start_tcp_server()
        self.assertIsNone(vng.tcp_thread, "server must not start after unload")
        self.assertFalse(vng._running.is_set())

    def test_failed_bind_resets_running_flag(self):
        """H8a: a bind error (port in use) must clear _running so a restart stays possible."""
        port = get_free_port()
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", port))
        blocker.listen()

        vng = VNGMock(port)
        vng.start_tcp_server()

        # server thread must die AND reset the running flag (previously it stayed True forever)
        deadline = time.time() + 5
        while vng._running.is_set() and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(vng._running.is_set(), "dead server still claims to be running")

        # after the conflict is gone a restart must succeed
        blocker.close()
        vng.start_tcp_server()
        try:
            client = connect_when_listening(port)
            client.close()
        finally:
            vng.stop_tcp_server()


class TestVngConnectionState(unittest.TestCase):
    """R3-16: is_connected / the "Connected" sensor must reflect the TCP server's running
    state (_running), not the never-started dummy bus (whose is_active() is always False)."""

    def test_is_connected_tracks_running_not_dummy_bus(self):
        vng = VNGMock(get_free_port())

        # not running yet -> not connected
        self.assertFalse(vng.is_connected)
        self.assertFalse(vng._current_connection_state())

        # server running -> connected. The VNG override reports _running directly (it never
        # reads a bus), which is exactly the fix: the base implementation would have reported
        # the never-started dummy bus's is_active() == False permanently.
        vng._running.set()
        self.assertTrue(vng.is_connected)
        self.assertTrue(vng._current_connection_state())

        vng._running.clear()
        self.assertFalse(vng.is_connected)


if __name__ == "__main__":
    unittest.main()
