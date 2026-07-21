import socket
import select
import threading
import queue
import time
from typing import Dict, List

from zeroconf import Zeroconf, ServiceInfo

from eltakobus.message import ESP2Message
from eltakobus.util import b2s, AddressExpression
from eltakobus.serial import RS485SerialInterfaceV2

from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.config_entries import ConfigEntry

from .const import *
from .gateway import EnOceanGateway, ELTAKO_GLOBAL_EVENT_BUS_ID

VIRT_GW_PORT = 12345
VIRT_GW_DEVICE_NAME = "ESP2 Netowrk Reverse Bridge"
BUFFER_SIZE = 1024
MAX_MESSAGE_DELAY = 5
CLIENT_SEND_TIMEOUT = 10        # s, sendall() to a hung client aborts after this (H8c)
CLIENT_QUEUE_MAX_SIZE = 1000    # bounded per-client queue, messages are dropped when full (H8c)
LOGGING_PREFIX_VIRT_GW = "VirtGw"

class VirtualNetworkGateway(EnOceanGateway):

    def __init__(self, general_settings:dict, hass: HomeAssistant, 
                 dev_id: int, port:int, config_entry: ConfigEntry):
        
        if port is None:
            port = VIRT_GW_PORT

        self.host = "0.0.0.0"
        super().__init__(general_settings, hass,
                         dev_id, GatewayDeviceType.VirtualNetworkAdapter, "homeassistant.local", -2, port, AddressExpression.parse('00-00-00-00'), VIRT_GW_DEVICE_NAME, True, None,
                           config_entry  )

        # _running is the CURRENT server generation's stop token. Every
        # start_tcp_server() creates a fresh Event and hands it to the new
        # server thread, so a zombie thread from a timed-out stop can only
        # ever clear its OWN (stale) token and cannot tear down its successor.
        self._running = threading.Event()
        self._running.clear()
        # serializes start/stop/restart across executor threads (unload vs. reconnect button)
        self._lifecycle_lock = threading.Lock()
        self._shutdown = False      # set on unload: no new server may start afterwards
        self.tcp_thread = None
        self.hass = hass
        self.zeroconf:Zeroconf = None
        self.connected_clients = []
        self.incoming_message_queues:Dict[socket.socket, List[queue.Queue]] = {}
        self.sending_gateways:list[EnOceanGateway] = []
        

        self._register_device()

    
    @property
    def dev_name(self):
        return VIRT_GW_DEVICE_NAME
    
    @property
    def dev_type(self):
        return GatewayDeviceType.VirtualNetworkAdapter

    @property
    def model(self):
        return GATEWAY_DEFAULT_NAME + " - " + self.dev_type.upper()


    def _current_connection_state(self) -> bool:
        # R3-16: the VNG has no started bus (the base builds a dummy RS485 bus that is never
        # started); its real "connected" state is whether the TCP server is running. Without
        # this override, is_connected and the "Connected" sensor reported the dummy bus's
        # is_active() == False permanently, even while clients were being served.
        # getattr guard: the base __init__ registers connection-state handlers (which
        # evaluate this) BEFORE VirtualNetworkGateway.__init__ assigns self._running.
        running = getattr(self, "_running", None)
        return running is not None and running.is_set()


    def get_service_info(self, hostname:str, ip_address:str):
        info = ServiceInfo(
            "_bsc-sc-socket._tcp.local.",
            "Virtual-Network-Gateway-Adapter._bsc-sc-socket._tcp.local.",
            addresses = [self.convert_ip_to_bytes(ip_address)],
            port=self.port,
            server=f"{hostname}.local."
        )

        return info        


    async def _forward_message(self, data:dict):
        gateway:EnOceanGateway = data['gateway']
        msg: ESP2Message = data['esp2_msg']

        # printf-style args: formatting an ESP2Message per telegram is wasted work when debug is off
        LOGGER.debug("[%s] received message: %s from gateway: %s", LOGGING_PREFIX_VIRT_GW, msg, gateway.dev_name)

        # A-r2: dedupe by gateway id and replace stale objects. The list previously
        # only ever grew: a reloaded config entry left its dead gateway object in
        # here forever (leak) and send_gateway_info replayed outdated base-id info
        # to every new client alongside the current one.
        existing = next((g for g in self.sending_gateways if g.dev_id == gateway.dev_id), None)
        if existing is None:
            self.sending_gateways.append(gateway)
        elif existing is not gateway:
            self.sending_gateways[self.sending_gateways.index(existing)] = gateway

        # iterate over a snapshot: client handler threads add/remove entries concurrently (H8b)
        for cc in list(self.connected_clients):
            q = self.incoming_message_queues.get(cc)
            if q is None:
                continue    # client is disconnecting right now
            try:
                q.put_nowait((time.time(), msg))
            except queue.Full:
                # client does not consume (hung/slow) => drop message instead of growing unbounded (H8c)
                LOGGER.debug("[%s] Message queue of a client is full. Dropping message.", LOGGING_PREFIX_VIRT_GW)


    def convert_bus_address_to_external_address(self, gateway, msg):
        address = msg.body[6:10]
        if address[0] == 0 and address[1] == 0:
            LOGGER.debug("TODO: create external id")
        
        return msg


    def send_gateway_info(self, conn: socket.socket):
        for gw in self.sending_gateways:
            try:
                ## send base id and put gateway type as well into it
                msg = RS485SerialInterfaceV2.create_base_id_info_message(gw.base_id, GatewayDeviceType.indexOf(gw.dev_type)+1)
                LOGGER.debug(f"[{LOGGING_PREFIX_VIRT_GW}] Send gateway info {gw} (id: {gw.dev_id}, base id: {b2s(gw.base_id)}, type: {gw.dev_type}) ")
                conn.sendall( msg.serialize() )

                ## request gateway version
                #TODO: ...
            except OSError:
                # socket-level errors are fatal for the connection -> let handle_client abort
                raise
            except Exception as e:
                # only message-construction errors are recoverable per gateway
                LOGGER.exception(e)


    def handle_client(self, conn: socket.socket, addr: socket.AddressInfo, run_flag: threading.Event):
        LOGGER.info(f"[{LOGGING_PREFIX_VIRT_GW}] Connected client by {addr}")
        # bounded queue: a client that stops consuming must not grow memory unbounded (H8c)
        self.incoming_message_queues[conn] = queue.Queue(maxsize=CLIENT_QUEUE_MAX_SIZE)
        try:
            # send timeout: a hung client must not block this thread forever in sendall() (H8c)
            conn.settimeout(CLIENT_SEND_TIMEOUT)
            self.connected_clients.append(conn)
            self.send_gateway_info(conn)

            # send messages coming in and out (run_flag = this server generation's stop token)
            while run_flag.is_set():
                # R3-23: the VNG is (for now) a one-way forwarder - a client's inbound bytes are
                # not routed yet (see AN2/Welle C). Drain and discard them non-blocking so unread
                # data cannot fill the kernel receive buffer and back-pressure the client's TCP
                # writes into a stall. An EOF (recv b'') means the client closed -> exit cleanly.
                try:
                    readable, _, _ = select.select([conn], [], [], 0)
                    if readable:
                        drained = conn.recv(4096)
                        if drained == b'':
                            LOGGER.debug("[%s] Client %s closed the connection.", LOGGING_PREFIX_VIRT_GW, addr)
                            break
                        LOGGER.debug("[%s] Discarding %d byte(s) from client %s (client->VNG routing not implemented yet).", LOGGING_PREFIX_VIRT_GW, len(drained), addr)
                except ValueError:
                    # R3-23 review: select() on a socket closed concurrently by stop_tcp_server()
                    # (fileno == -1) raises ValueError, not OSError. Treat it as a disconnect and
                    # exit quietly instead of logging an ERROR+traceback during shutdown/reload.
                    break
                except OSError:
                    raise   # socket error -> let the outer handler close the connection

                # Receive data from the client
                try:
                    t, msg = self.incoming_message_queues[conn].get(timeout=1)
                except queue.Empty:
                    # no message within timeout => send keep alive message
                    conn.sendall(b'IM2M')
                    continue

                if time.time() - t < MAX_MESSAGE_DELAY:
                    try:
                        payload = msg.serialize()
                    except Exception:
                        # one bad message must not disconnect the client (review finding)
                        LOGGER.warning("[%s] Dropping unserializable message %r", LOGGING_PREFIX_VIRT_GW, msg)
                        continue
                    conn.sendall(payload)

                    self._fire_received_message_count_event()
                    self._fire_last_message_received_event()
                else:
                    LOGGER.debug("[%s] EnOcean message %s expired (Max delay: %s)", LOGGING_PREFIX_VIRT_GW, msg, MAX_MESSAGE_DELAY)

        except socket.timeout:
            LOGGER.info(f"[{LOGGING_PREFIX_VIRT_GW}] Client {addr} did not accept data within {CLIENT_SEND_TIMEOUT}s. Closing connection.")
        except OSError as e:
            # connection reset / broken pipe / socket closed during shutdown - expected,
            # but keep the errno visible for diagnosing recurring non-shutdown errors
            LOGGER.debug("[%s] Client %s connection error (errno=%s): %s", LOGGING_PREFIX_VIRT_GW, addr, e.errno, e)
        except Exception as e:
            LOGGER.error(f"[{LOGGING_PREFIX_VIRT_GW}] An error occurred with {addr}: {e}", exc_info=True, stack_info=True)
        finally:
            # remove from connected_clients FIRST so _forward_message cannot hit a removed queue (H8b)
            if conn in self.connected_clients:
                self.connected_clients.remove(conn)
            self.incoming_message_queues.pop(conn, None)
            try:
                conn.close()
            except OSError:
                pass
            LOGGER.info(f"[{LOGGING_PREFIX_VIRT_GW}] Handler for {addr} exiting. (Thread flag running: {run_flag.is_set()})")



    def tcp_server(self, run_flag: threading.Event):
        """Basic TCP Server that listens for connections.

        run_flag is THIS server generation's stop token (see __init__). A thread
        that outlives a timed-out stop_tcp_server() join must not be able to tear
        down its successor, so all shared cleanup below is guarded by
        `run_flag is self._running` (i.e. "I am still the current generation").
        """
        service_info: ServiceInfo = None    # M11: defined even when registration fails
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                # H8a: allow immediate restart (previous socket may linger in TIME_WAIT)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((self.host, self.port))
                s.listen()
                s.settimeout(1.0)   # Set timeout so it can periodically check for shutdown

                # Get the hostname / IP address (H8a: DNS failure must not kill the server)
                hostname = socket.gethostname()
                try:
                    ip_address = socket.gethostbyname(hostname)
                except OSError as e:
                    LOGGER.warning(f"[{LOGGING_PREFIX_VIRT_GW}] Could not resolve own IP address ({e}). mDNS record will not be registered.")
                    ip_address = None

                LOGGER.info(f"[{LOGGING_PREFIX_VIRT_GW}] Virtual Network Gateway Adapter listening on {hostname}({ip_address}):{self.port}")
                self._fire_connection_state_changed_event(True)
                self._received_message_count = 0

                # Register the service (M11: zeroconf may be None when setup did not complete)
                try:
                    if self.zeroconf is not None and ip_address is not None:
                        # assign only after successful registration so the finally
                        # block can never unregister a never-registered service (M11)
                        info = self.get_service_info(hostname, ip_address)
                        self.zeroconf.register_service(info)
                        service_info = info
                        LOGGER.info(f"[{LOGGING_PREFIX_VIRT_GW}] registered mDNS service record created.")
                except Exception as e:
                    LOGGER.error(f"[{LOGGING_PREFIX_VIRT_GW}] Could not register mDNS service: {e}")

                while run_flag.is_set():
                    try:
                        # LOGGER.debug("[%s] Try to connect", LOGGING_PREFIX)
                        conn, addr = s.accept()
                        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        LOGGER.debug("[%s] Connection from: %s established", LOGGING_PREFIX_VIRT_GW, addr)

                        client_thread = threading.Thread(target=self.handle_client, args=(conn, addr, run_flag), daemon=True)
                        client_thread.start()

                    except socket.timeout:
                        # Timeout used to periodically check for shutdown
                        continue

                    except Exception as e:
                        # R3-22: a transient accept() error does NOT mean the server lost its
                        # connection - the listener socket is still open and the loop keeps
                        # running. Do not fire a (false) disconnect; back off briefly so a
                        # persistent error cannot spin the loop and flood the log.
                        LOGGER.error(f"[{LOGGING_PREFIX_VIRT_GW}] Error accepting a client connection: {e}", exc_info=True, stack_info=True)
                        time.sleep(1)

        except Exception as e:
            # H8a: bind/listen errors (e.g. port already in use) must not leave a dead
            # server behind that still claims to be running.
            LOGGER.error(f"[{LOGGING_PREFIX_VIRT_GW}] TCP server failed: {e}", exc_info=True)
        finally:
            # M11: unregister only when the registration succeeded (always ours -> no guard)
            if service_info is not None and self.zeroconf is not None:
                try:
                    self.zeroconf.unregister_service(service_info)
                except Exception as e:
                    LOGGER.debug("[%s] Could not unregister mDNS service: %s", LOGGING_PREFIX_VIRT_GW, e)
            # end THIS generation's handler threads (their loops watch run_flag)
            run_flag.clear()
            # Shared cleanup only while still the current generation: a zombie thread
            # exiting after a restart must not close the new server's clients or
            # flip the new server's connection state (review finding, CONFIRMED).
            if run_flag is self._running:
                # H8d/H8a: close clients, signal disconnected, allow a fresh start
                self._close_client_connections()
                self._fire_connection_state_changed_event(False)
            LOGGER.info(f"[{LOGGING_PREFIX_VIRT_GW}] Closed TCP Server")


    def reconnect(self):
        self.restart_tcp_server()


    def restart_tcp_server(self):
        LOGGER.debug(f"[{LOGGING_PREFIX_VIRT_GW}] Restart TCP server")
        # stop unconditionally (safe when not running) - the old "skip stop when
        # _running is unset" path could resurrect a server on an unloaded entry
        self.stop_tcp_server()
        self.start_tcp_server()


    def start_tcp_server(self):
        """Start TCP server in a separate thread."""
        with self._lifecycle_lock:
            if self._shutdown:
                LOGGER.debug(f"[{LOGGING_PREFIX_VIRT_GW}] Gateway is unloaded - not starting TCP server.")
                return
            if self._running.is_set():
                return      # current generation still running
            # fresh stop token per generation (see __init__)
            self._running = threading.Event()
            self._running.set()
            self.tcp_thread = threading.Thread(target=self.tcp_server, args=(self._running,), daemon=True)
            self.tcp_thread.start()


    def _close_client_connections(self):
        """Close all client sockets so their handler threads exit promptly. (H8d)"""
        for conn in list(self.connected_clients):
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass


    def stop_tcp_server(self):
        """Stop TCP server thread. BLOCKING - do not call from the event loop. (K2)"""
        with self._lifecycle_lock:
            self._running.clear()
            # H8d: close client sockets so handler threads blocked in sendall() return immediately;
            # the threads remove themselves from the client lists in their finally blocks.
            self._close_client_connections()
            # snapshot: concurrent stop calls are serialized by the lock, but never
            # dereference self.tcp_thread twice (review finding, CONFIRMED)
            t = self.tcp_thread
            self.tcp_thread = None
            # guard: server may never have been started (M1) and join() must never wait forever
            if t is not None and t.is_alive():
                t.join(10)
                if t.is_alive():
                    LOGGER.warning(f"[{LOGGING_PREFIX_VIRT_GW}] TCP server thread did not stop within 10s.")


    def convert_ip_to_bytes(self, ip_address_str):
        try:
            if ":" in ip_address_str:  # Check for IPv6
                return socket.inet_pton(socket.AF_INET6, ip_address_str)
            else:  # Assume IPv4
                return socket.inet_aton(ip_address_str)
            
        except socket.error as e:
            LOGGER.error(f"[{LOGGING_PREFIX_VIRT_GW}] Invalid IP address: {ip_address_str} - {e}")


    async def async_setup(self):
        """Initialized tcp server and register callback function on HA event bus."""

        # register for all incoming and outgoing messages from all gateways
        self.dispatcher_disconnect_handle = async_dispatcher_connect(
            self.hass, ELTAKO_GLOBAL_EVENT_BUS_ID, self._forward_message
        )

        self.zeroconf:Zeroconf = await zeroconf.async_get_instance(self.hass)

        self.start_tcp_server()

        LOGGER.debug(f"[{LOGGING_PREFIX_VIRT_GW}] Was started.")


    def _unload_blocking(self):
        """Stop TCP server thread. BLOCKING - called via executor from async_unload. (K2)"""
        # a reconnect racing with unload must not resurrect the server afterwards
        self._shutdown = True
        self.stop_tcp_server()
        LOGGER.debug(f"[{LOGGING_PREFIX_VIRT_GW}] Was stopped.")

    def unload(self):
        """Deprecated synchronous variant of async_unload, kept for compatibility.
        BLOCKING - do not call from the event loop."""
        if self.dispatcher_disconnect_handle:
            self.dispatcher_disconnect_handle()
            self.dispatcher_disconnect_handle = None

        self._unload_blocking()