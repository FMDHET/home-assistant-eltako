"""Tests for HardenedTCP2SerialCommunicator (K8: TCP connection hardening).

Reproduces the upstream bug of esp2_gateway_adapter (<= 0.2.21): when the
remote end closes the TCP connection gracefully (FIN), recv() returns b''
which the library treats as a message instead of a disconnect. The hardened
subclass must detect the EOF and reconnect.
"""
import socket
import threading
import time
import unittest

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


if __name__ == "__main__":
    unittest.main()
