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
* K9: the outgoing ESP3 command packets pass ``optional=[]`` explicitly.
  ``enocean.protocol.packet.Packet.__init__`` logs
  ``"Replacing Packet.optional with default value."`` at WARNING whenever the
  argument is not a ``list`` - and its own default is ``None``, so *every*
  upstream call that omits it warns. The application-level keep-alive builds
  one such packet per probe (every ``tcp_keep_alive_timeout`` seconds of bus
  silence, 30 s by default), which floods the HA log with ~2900 WARNING lines
  per day. The built frame is byte-identical with and without the argument -
  for a COMMON_COMMAND an empty optional field is the correct value, so this
  removes log noise only and changes nothing on the wire.
* K10: a single malformed radio telegram no longer takes the gateway down.
  ``Packet.parse_msg`` only reassigns the buffer once the packet object has
  been constructed, but the constructor already runs ``RadioPacket.parse()``.
  A frame with valid CRCs whose data part is too short for its RORG (e.g. a
  4BS telegram with 1 instead of 4 data bytes and the "EEP included" bit set)
  raises ``ValueError: invalid literal for int() with base 2: ''`` from there -
  and the broken frame stays at the head of ``self._buffer``. The exception
  propagated to ``run()``, which dropped the TCP connection and reconnected
  after ``reconnection_timeout``; the next ``recv`` appended to the very same
  buffer (R3-02) and the parse crashed again: an endless crash/reconnect loop
  in which every send failed with "Serial port ... is not available" until the
  integration was reloaded. The hardened ``parse()`` now drops exactly the
  offending frame (its boundaries are trustworthy - both CRCs have passed at
  that point) and carries on with the frames behind it; the connection stays
  up. In addition the buffer is cleared on every (re)connect, so a fragment
  from a previous connection can never be glued to the new byte stream.
  Exceptions from dispatching an already-parsed packet are logged and
  swallowed for the same reason (the buffer has advanced; a reconnect would
  achieve nothing).

The library is pinned (``==0.2.21``) in manifest.json, so the copied ``run()``
cannot drift from the actual implementation unnoticed. Re-verify this module
whenever the pin is bumped.
"""
import datetime
import socket
import select
import time

from enocean.protocol.constants import PACKET, PARSE_RESULT
from enocean.protocol.packet import Packet, UTETeachInPacket
from esp2_gateway_adapter.esp3_tcp_com import TCP2SerialCommunicator

# K10: at most one WARNING per interval for dropped malformed frames - a device
# that keeps sending broken telegrams must not flood the log (cf. K9).
MALFORMED_FRAME_WARN_INTERVAL = 600  # seconds


class HardenedTCP2SerialCommunicator(TCP2SerialCommunicator):
    """TCP2SerialCommunicator that detects a graceful remote close (EOF)."""

    _malformed_frames_dropped = 0      # K10: total since start (diagnostics)
    _malformed_frames_unreported = 0   # K10: dropped since the last WARNING
    _malformed_frame_last_warning = 0.0

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
                    # HARDENED (K10): a new connection is a new byte stream - never glue a
                    # fragment (or a broken frame) from the previous one in front of it.
                    self._buffer = []

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

    @property
    def _callback_wrapper(self):
        # Proxy for the name-mangled ESP3SerialCommunicator.__callback_wrapper.
        return self._ESP3SerialCommunicator__callback_wrapper

    def parse(self):
        # Copy of ESP3SerialCommunicator.parse() from esp2_gateway_adapter 0.2.21
        # with the K10 fix (marked "# HARDENED"). Never raises for bad input:
        # an exception here would end up in run()'s reconnect path.
        while True:
            try:
                status, self._buffer, packet = Packet.parse_msg(self._buffer)
            except Exception as e:  # HARDENED (K10): frame passed CRC but is not parseable
                self._drop_malformed_frame(e)
                continue
            # If message is incomplete -> break the loop
            if status == PARSE_RESULT.INCOMPLETE:
                return status

            # If message is OK, add it to receive queue or send to the callback method
            if status == PARSE_RESULT.OK and packet:
                try:  # HARDENED (K10): buffer already advanced - log, don't reconnect
                    packet.received = datetime.datetime.now()

                    if isinstance(packet, UTETeachInPacket) and self.teach_in:
                        response_packet = packet.create_response_packet(self.base_id)
                        self.logger.info('Sending response to UTE teach-in.')
                        self.send(response_packet)

                    if self._outside_callback is None:
                        self.receive.put(packet)
                    else:
                        self._callback_wrapper(packet)
                    self.logger.debug(packet)
                except Exception:
                    self.log.exception("Failed to process received ESP3 packet %s", packet)

    def _drop_malformed_frame(self, error: Exception) -> None:
        """K10: remove the frame at the head of the buffer that parse_msg choked on.

        parse_msg() only raises after the header/data length check and both CRC
        checks have passed, so the frame boundaries computed from the header are
        reliable: dropping exactly that frame keeps every frame behind it.
        """
        buf = list(self._buffer)
        try:
            start = buf.index(0x55)
        except ValueError:  # cannot happen after a raise from parse_msg - be safe
            self._buffer = []
            return
        try:
            frame_len = 6 + ((buf[start + 1] << 8) | buf[start + 2]) + buf[start + 3] + 1
        except (IndexError, TypeError):
            frame_len = 1  # at least skip this sync byte so the loop makes progress
        frame = buf[start:start + frame_len]
        self._buffer = buf[start + frame_len:]

        self._malformed_frames_dropped += 1
        self._malformed_frames_unreported += 1
        frame_hex = bytes(frame).hex(' ').upper()
        now = time.monotonic()
        if (self._malformed_frame_last_warning == 0.0
                or now - self._malformed_frame_last_warning >= MALFORMED_FRAME_WARN_INTERVAL):
            self.log.warning(
                "Dropped malformed ESP3 frame (%s: %s) - a device sends telegrams that do "
                "not match their RORG. Frame: %s (%d dropped since last report, %d total; "
                "further drops within %d s are logged at DEBUG level)",
                type(error).__name__, error, frame_hex, self._malformed_frames_unreported,
                self._malformed_frames_dropped, MALFORMED_FRAME_WARN_INTERVAL)
            self._malformed_frame_last_warning = now
            self._malformed_frames_unreported = 0
        else:
            self.log.debug("Dropped malformed ESP3 frame (%s: %s): %s",
                           type(error).__name__, error, frame_hex)

    def _check_timeout_on_application_level(self):
        # Copy of TCP2SerialCommunicator._check_timeout_on_application_level()
        # from esp2_gateway_adapter 0.2.21 with the K9 fix (marked "# HARDENED").
        # Note: self._ser is the proxy for the parent's name-mangled __ser - a
        # verbatim "self.__ser" would mangle to this subclass and miss the socket.
        if self._auto_reconnect:
            if time.time() - self.last_message_received > self._tcp_keep_alive_timeout:
                self.last_message_received = 0
                self._ser.close()
            elif self.transmit.empty() and time.time() - self.last_message_received > self._tcp_keep_alive_timeout - 1:
                self.log.debug("Request base id to check if connection is still alive.")
                # HARDENED (K9): optional=[] - see module docstring. Without it every
                # keep-alive probe emits an enocean WARNING; the frame is unchanged.
                self.transmit.put(self._common_command(0x08))

    @staticmethod
    def _common_command(*data) -> Packet:
        """Build an ESP3 COMMON_COMMAND packet without the K9 log noise.

        ``optional=[]`` is what enocean falls back to anyway - a common command
        carries no optional data - it just has to be passed explicitly.
        """
        return Packet(PACKET.COMMON_COMMAND, data=list(data), optional=[])

    # K9: same fix for the two requests fired once per (re)connect from
    # EltakoGateway.query_for_base_id_and_version().
    #
    # These deliberately do NOT call super().send(): upstream's implementations
    # in ESP3SerialCommunicator use super().send() to reach the *synchronous*
    # Communicator.send, but from this subclass super() resolves to the *async*
    # ESP3SerialCommunicator.send - the coroutine would never be awaited and the
    # packet never sent. transmit.put() is exactly what Communicator.send does
    # after its isinstance check.
    async def send_base_id_request(self):
        self.transmit.put(self._common_command(0x08))

    async def send_version_request(self):
        self.transmit.put(self._common_command(0x03))
