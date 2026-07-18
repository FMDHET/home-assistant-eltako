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

    def _stop(self, vng: VNGMock):
        vng.stop_tcp_server()

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
            self._stop(vng)

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
            while data not in (b'',):   # drain remaining keep-alives until EOF
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
            self._stop(vng)

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
            self._stop(vng)


if __name__ == "__main__":
    unittest.main()
