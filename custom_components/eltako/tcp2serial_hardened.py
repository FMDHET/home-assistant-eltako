"""Hardened TCP communicator for LAN gateways (K8 follow-up).

esp2_gateway_adapter (verified up to and including 0.2.21) treats
``recv() == b''`` — the remote closing the TCP connection gracefully (FIN) —
as a regular "message": the empty buffer is parsed (a no-op) and
``last_message_received`` is refreshed on every loop iteration. As a result
neither the exception-based reconnect path nor the application-level
keep-alive ever fires and the receive thread spins forever on a dead socket.
Home Assistant keeps showing the gateway as connected although nothing is
received anymore; only a HA restart recovers the connection.

``HardenedTCP2SerialCommunicator`` overrides ``run()`` (a copy of the 0.2.21
implementation) with these fixes (all marked ``# HARDENED``):

* ``recv() == b''`` raises ``ConnectionResetError`` so the upstream reconnect
  logic kicks in (reconnect after ``reconnection_timeout`` seconds).
* Kernel-level TCP keep-alive (``SO_KEEPALIVE``) is enabled on the socket so
  silently dropped connections (NAT idle timeout, gateway power loss, ...)
  are detected by the OS as well.
* R3-01: ``last_message_received`` is reset to ``time.time()`` in the connect
  block, giving every (re)connection a fresh grace period. Upstream only sets
  it at thread start and after ``recv``; the inherited application-level
  keep-alive check runs *before* the first ``recv`` and, after an outage longer
  than ``tcp_keep_alive_timeout``, would immediately close the freshly
  established connection (and set the timestamp to 0). The result was a
  permanent connect/close livelock after every outage > 30s: ``recv`` was
  never reached, so nothing could ever refresh the timestamp again, until a
  HA restart or a manual reconnect rebuilt the bus.
* R3-02: partial ESP3 frames spread across two TCP segments are no longer
  lost. ``Packet.parse_msg`` deliberately puts an incomplete frame's remainder
  back into ``self._buffer``; upstream then *replaces* the buffer on the next
  ``recv`` instead of appending, discarding the remainder (the serial variant
  gets this right with ``extend``). The hardened copy appends.

The library is pinned (``==0.2.21``) in manifest.json, so the copied ``run()``
cannot drift from the actual implementation unnoticed. Re-verify this module
whenever the pin is bumped.
"""
import socket
import select
import time

from esp2_gateway_adapter.esp3_tcp_com import TCP2SerialCommunicator


class HardenedTCP2SerialCommunicator(TCP2SerialCommunicator):
    """TCP2SerialCommunicator that detects a graceful remote close (EOF)."""

    # Proxies for the name-mangled private attributes of the parent class so
    # the copied run() below stays readable and keeps operating on the same
    # attributes as the untouched parent helpers (stop(),
    # _check_timeout_on_application_level(), ...).
    @property
    def _ser(self):
        return self._TCP2SerialCommunicator__ser

    @_ser.setter
    def _ser(self, value):
        self._TCP2SerialCommunicator__ser = value

    @property
    def _recon_time(self):
        return self._TCP2SerialCommunicator__recon_time

    def _enable_tcp_keepalive(self, sock: socket.socket) -> None:
        """Enable kernel TCP keep-alive: probe after 10s idle, then every 3s."""
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, 'SIO_KEEPALIVE_VALS'):  # Windows
                sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10_000, 3_000))
            else:  # Linux / macOS (constants exist only where supported)
                for opt, value in (('TCP_KEEPIDLE', 10), ('TCP_KEEPINTVL', 3), ('TCP_KEEPCNT', 5)):
                    if hasattr(socket, opt):
                        sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), value)
        except OSError as e:
            self.log.warning("Could not enable TCP keep-alive: %s", e)

    def run(self):
        # Copy of TCP2SerialCommunicator.run() from esp2_gateway_adapter 0.2.21
        # with the EOF fix and kernel keep-alive (marked with "# HARDENED").
        self.last_message_received = time.time()
        self.log.info('TCP2SerialCommunicator started')
        self._fire_status_change_handler(connected=False)
        while not self._stop_flag.is_set():
            try:
                # Initialize serial port
                if self._ser is None:

                    self._ser = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self._ser.connect((self._host, self._port))
                    if self._auto_reconnect:
                        self._ser.settimeout(self._tcp_connection_timeout)
                    else:
                        self._ser.settimeout(None)

                    self._enable_tcp_keepalive(self._ser)  # HARDENED: detect silent drops
                    # HARDENED (R3-01): fresh grace period per connection. Without this
                    # the inherited _check_timeout_on_application_level() (runs before the
                    # first recv) closes the just-established connection whenever the last
                    # message predates this connect by more than tcp_keep_alive_timeout -
                    # a permanent reconnect livelock after any outage > that timeout.
                    self.last_message_received = time.time()

                    self.log.info(f"Established TCP connection to {self._host}:{self._port} (blocking: {not self._auto_reconnect}, tcp timeout: {self._tcp_connection_timeout} sec, serial timeout: {self._tcp_keep_alive_timeout} sec)")

                    self.is_serial_connected.set()
                    self._fire_status_change_handler(connected=True)

                self._check_timeout_on_application_level()

                # If there's messages in transmit queue
                # send them
                while True:
                    packet = self._get_from_send_queue()
                    if not packet:
                        break
                    self.log.debug("send msg: %s", packet)
                    self._ser.sendall( bytearray(packet.build()) )


                # Read chars from serial port as hex numbers
                # prevent to block recv operation
                ready_to_read, _, _ = select.select([self._ser], [], [], 1) # timeout 1sec

                if ready_to_read:
                    data = self._ser.recv(1024)
                    if not data:  # HARDENED: EOF = remote closed gracefully (FIN)
                        raise ConnectionResetError("Remote closed the TCP connection (EOF). Reconnecting.")
                    if data not in self.KEEP_ALIVE_MESSAGES:
                        # HARDENED (R3-02): append, don't overwrite. parse_msg() leaves an
                        # incomplete frame's remainder in self._buffer; overwriting it drops
                        # every frame split across two TCP segments.
                        self._buffer = list(self._buffer) + list(data)
                        self.parse()
                    self.last_message_received = time.time()

                time.sleep(0)

            except Exception as e:
                self._fire_status_change_handler(connected=False)
                self.is_serial_connected.clear()
                self.log.exception(e)
                if self._ser is not None:
                    self._ser.close()
                self._ser = None
                if self._auto_reconnect:
                    self.log.info("TCP2Serial communication crashed. Wait %s seconds for reconnection.", self._recon_time)
                    time.sleep(self._recon_time)
                else:
                    self.log.debug(f"auto-reconnect is disabled ({self._auto_reconnect})")
                    self._stop_flag.set()

        if self._ser is not None:
            self._ser.close()
            self._ser = None
        self.is_serial_connected.clear()
        self._fire_status_change_handler(connected=False)
        self.logger.info('TCP2SerialCommunicator stopped')
