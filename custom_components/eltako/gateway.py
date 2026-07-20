"""Representation of an Eltako gateway."""
import glob
import inspect
from enum import Enum

from os.path import basename, normpath
import pytz
from datetime import datetime, UTC

import serial
import asyncio

from eltakobus.serial import RS485SerialInterfaceV2
from eltakobus.message import *
from eltakobus.util import AddressExpression, b2s
from eltakobus.eep import EEP
from eltakobus.device import request_memory_of_all_devices

from esp2_gateway_adapter.esp3_serial_com import ESP3SerialCommunicator

from .tcp2serial_hardened import HardenedTCP2SerialCommunicator

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect, dispatcher_send
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceRegistry
from homeassistant.config_entries import ConfigEntry

from .const import *
from . import config_helpers

import threading


async def async_get_base_ids_of_registered_gateway(device_registry: DeviceRegistry) -> list[str]:
    base_id_list = []
    for d in device_registry.devices.values():
        if d.model and d.model.startswith(GATEWAY_DEFAULT_NAME):
            base_id_list.append( list(d.connections)[0][1] )
    return base_id_list


async def async_get_serial_path_of_registered_gateway(device_registry: DeviceRegistry) -> list[str]:
    serial_path_list = []
    for d in device_registry.devices.values():
        if d.model and d.model.startswith(GATEWAY_DEFAULT_NAME):
            serial_path_list.append( list(d.identifiers)[0][1] )
    return serial_path_list


class EnOceanGateway:
    """Representation of an Eltako gateway.

    The gateway is responsible for receiving the Eltako frames,
    creating devices if needed, and dispatching messages to platforms.
    """

    def __init__(self, general_settings:dict, hass: HomeAssistant,
                 dev_id: int, dev_type: GatewayDeviceType, serial_path: str, baud_rate: int, port: int, base_id: AddressExpression, dev_name: str, auto_reconnect: bool=True, message_delay:float=None,
                 config_entry: ConfigEntry = None,
                 reconnection_timeout: float = 15, tcp_keep_alive_timeout: float = 30):

        """Initialize the Eltako gateway."""

        self._loop = asyncio.get_event_loop()
        self._bus_task = None
        self.baud_rate = baud_rate
        self._auto_reconnect = auto_reconnect
        self._message_delay = message_delay
        # TCP stability (LAN gateways): shorter than the library defaults (60s each) so a dropped
        # TCP connection recovers in seconds instead of up to a minute.
        self._reconnection_timeout = reconnection_timeout
        self._tcp_keep_alive_timeout = tcp_keep_alive_timeout
        self.port = port
        self._attr_dev_type = dev_type
        self._attr_serial_path = serial_path
        self._attr_identifier = basename(normpath(serial_path))
        self.hass: HomeAssistant = hass
        self.dispatcher_disconnect_handle = None
        self.general_settings = general_settings
        self._attr_dev_id = dev_id
        self._attr_base_id = base_id
        self.config_entry_id = config_entry.entry_id

        self._last_message_received_handler = None
        self._connection_state_handlers = []
        self._base_id_change_handlers = []
        self._received_message_count_handler = None

        # R3-05: serialize reconnect() and _unload_blocking(). Two overlapping lifecycle
        # operations (double reconnect button press, or a reconnect racing the unload on
        # reload) could otherwise start one bus, then have the other reassign self._bus and
        # start a second one - leaving the first bus running orphaned, holding a single-client
        # gateway's only connection slot while self._bus points at a bus that never connects.
        # The VNG subclass keeps its own copy of this lock for its TCP server threads.
        self._lifecycle_lock = threading.Lock()
        self._shutdown = False   # set on unload; reconnect() becomes a no-op afterwards

        self._attr_model = GATEWAY_DEFAULT_NAME + " - " + self.dev_type.upper()

        if GatewayDeviceType.is_esp2_gateway(self.dev_type):
            self.native_protocol = 'ESP2'
        else:
            self.native_protocol = 'ESP3'
        self._original_dev_name = dev_name
        self._attr_dev_name = config_helpers.get_gateway_name(self._original_dev_name, self.dev_type.value, self.dev_id)

        self._reading_memory_of_devices_is_running = threading.Event()

        self._init_bus()

        self._register_device()

        self.add_connection_state_changed_handler(self.query_for_base_id_and_version)
        self.add_connection_state_changed_handler(self.connection_state_changed)


    async def connection_state_changed(self, connected):
        self._reading_memory_of_devices_is_running.clear()

    async def query_for_base_id_and_version(self, connected):
        if connected:
            if not GatewayDeviceType.is_esp2_gateway(self.dev_type) or self.dev_type == GatewayDeviceType.GatewayEltakoFAM14:
                LOGGER.debug("[Gateway] [Id: %d] Query for base id and version info.", self.dev_id)
                await self._bus.send_base_id_request()
                await self._bus.send_version_request()

            # elif self.dev_type == GatewayDeviceType.GatewayEltakoFAM14:
            #     await asyncio.to_thread(asyncio.run, self.get_fam14_base_id())



    def add_base_id_change_handler(self, handler):
        self._base_id_change_handlers.append(handler)

    def remove_base_id_change_handler(self, handler):
        # H7: allow entities to deregister on removal so handlers don't leak / fire on dead entities
        if handler in self._base_id_change_handlers:
            self._base_id_change_handlers.remove(handler)

    def _schedule_handler(self, coro):
        """Schedule a handler coroutine on the HA event loop. (A-r2)

        The _fire_* methods are invoked from the serial-bus thread (receive
        callback) and from executor threads (reconnect). Behaviorally equivalent
        to hass.create_task (which current HA implements via
        call_soon_threadsafe itself); kept explicit so the cross-thread contract
        of these callers is visible in one place.
        """
        self.hass.loop.call_soon_threadsafe(self.hass.async_create_task, coro)

    def _fire_base_id_change_handlers(self, base_id: AddressExpression):
        # B1: snapshot - handlers may be added/removed on the loop thread while this
        # iterates on the bus/reconnect thread (see _fire_connection_state_changed_event).
        for handler in list(self._base_id_change_handlers):
            self._schedule_handler(handler(base_id))

    def add_connection_state_changed_handler(self, handler):
        self._connection_state_handlers.append(handler)
        # A-r2: notify only the NEWLY added handler with the current state.
        # Previously ALL handlers re-fired on every registration: O(N^2) handler
        # invocations at setup, redundant base-id/version queries per entity, and
        # each burst reset the memory-read guard via connection_state_changed.
        self._schedule_handler(handler(self._bus.is_active()))

    def remove_connection_state_changed_handler(self, handler):
        # H7: allow entities to deregister on removal
        if handler in self._connection_state_handlers:
            self._connection_state_handlers.remove(handler)


    def _fire_connection_state_changed_event(self, status):
        # B1: iterate a SNAPSHOT. This runs on the bus/reconnect thread, but B1 now
        # registers a connection-state handler per device entity, added/removed on
        # the loop thread (entity add/remove/reload). Iterating the live list could
        # let a concurrent mutation silently skip a handler, stranding that entity's
        # availability until the next connection transition.
        for handler in list(self._connection_state_handlers):
            self._schedule_handler(handler(status))


    def set_last_message_received_handler(self, handler):
        self._last_message_received_handler = handler

    def remove_last_message_received_handler(self, handler):
        # H7: compare with == (NOT is): a bound method is created fresh on every attribute
        # access, so `is` would never match and the deregistration would be a no-op.
        # Distinct entity instances still compare unequal (different __self__), so this only
        # clears the slot if it is still the given entity's handler.
        if self._last_message_received_handler == handler:
            self._last_message_received_handler = None


    def _fire_last_message_received_event(self):
        if self._last_message_received_handler:
            self._schedule_handler(
                self._last_message_received_handler( datetime.now(UTC).replace(tzinfo=pytz.UTC) )
            )


    def set_received_message_count_handler(self, handler):
        self._received_message_count_handler = handler

    def remove_received_message_count_handler(self, handler):
        # H7: compare with == (NOT is) - see remove_last_message_received_handler above.
        if self._received_message_count_handler == handler:
            self._received_message_count_handler = None


    def _fire_received_message_count_event(self):
        self._received_message_count += 1
        self._notify_received_message_count()

    def _notify_received_message_count(self):
        if self._received_message_count_handler:
            self._schedule_handler(
                self._received_message_count_handler( self._received_message_count ),
            )

    def report_message_stats(self, data=None):
        """Update message statistics. Called from the serial-bus receive thread -
        the _fire_* helpers schedule the handlers onto the HA loop thread-safely."""
        self._fire_received_message_count_event()
        self._fire_last_message_received_event()


    def _init_bus(self):
        self._received_message_count = 0
        # A-r2: notify without incrementing - the counter previously started at 1
        # (and reset to 1 on every reconnect) because the reset called the
        # incrementing fire method.
        self._notify_received_message_count()

        if GatewayDeviceType.is_esp2_gateway(self.dev_type):
            self._bus = RS485SerialInterfaceV2(self.serial_path, 
                                               baud_rate=self.baud_rate, 
                                               callback=self._callback_receive_message_from_serial_bus, 
                                               delay_message=self._message_delay,
                                               auto_reconnect=self._auto_reconnect)
            
        elif GatewayDeviceType.is_lan_gateway(self.dev_type) and not GatewayDeviceType.is_esp2_gateway(self.dev_type):
            # HardenedTCP2SerialCommunicator: detects graceful remote close (EOF)
            # and enables kernel TCP keep-alive (see tcp2serial_hardened.py, K8).
            self._bus = HardenedTCP2SerialCommunicator(host=self.serial_path,
                                               port=self.port,
                                               callback=self._callback_receive_message_from_serial_bus,
                                               esp2_translation_enabled=True,
                                               auto_reconnect=self._auto_reconnect,
                                               # faster recovery from dropped TCP connections (library defaults are 60s)
                                               reconnection_timeout=self._reconnection_timeout,
                                               tcp_keep_alive_timeout=self._tcp_keep_alive_timeout)
        else:
            self._bus = ESP3SerialCommunicator(filename=self.serial_path, 
                                               callback=self._callback_receive_message_from_serial_bus, 
                                               esp2_translation_enabled=True, 
                                               auto_reconnect=self._auto_reconnect)
        
        # R3-06: bind the status handler to THIS bus generation. join(10) can be shorter than
        # the bus's reconnection sleep, so a superseded bus thread may still be alive and fire
        # its exit `connected=False` AFTER the new generation reported `connected=True`. The
        # per-generation adapter drops such stale callbacks (see _on_bus_status). This also
        # fixes the B1 follow-up at the root: GatewayConnectionState.value_changed trusts the
        # pushed value, so a stale False would strand the "Connected" sensor on "Disconnected".
        bus = self._bus
        bus.set_status_changed_handler(lambda status, b=bus: self._on_bus_status(b, status))


    def _on_bus_status(self, bus, status):
        """Forward a bus connection-state change only if it comes from the CURRENT bus
        generation. Runs on the bus/reconnect thread. (R3-06)"""
        if bus is not self._bus:
            LOGGER.debug("[Gateway] [Id: %d] Ignoring connection state %s from a superseded bus generation.", self.dev_id, status)
            return
        self._fire_connection_state_changed_event(status)


    def _register_device(self) -> None:
        device_registry = dr.async_get(self.hass)
        device_registry.async_get_or_create(
            config_entry_id=self.config_entry_id,
            identifiers={(DOMAIN, self.serial_path)},
            manufacturer=MANUFACTURER,
            name= self.dev_name,
            model=self.model,
        )
        

    ### address validation functions

    def validate_sender_id(self, sender_id: AddressExpression, device_name: str = "") -> bool:
        if GatewayDeviceType.is_transceiver(self.dev_type):
            return self.sender_id_validation_by_transmitter(sender_id, device_name)
        elif GatewayDeviceType.is_bus_gateway(self.dev_type):
            return self.sender_id_validation_by_bus_gateway(sender_id, device_name)
        return False
    

    def sender_id_validation_by_transmitter(self, sender_id: AddressExpression, device_name: str = "") -> bool:
        result = config_helpers.compare_enocean_ids(self.base_id[0], sender_id[0])
        if not result:
            LOGGER.warning(f"{device_name} ({sender_id}): Maybe have wrong sender id configured!")
        return result
    

    def sender_id_validation_by_bus_gateway(self, sender_id: AddressExpression, device_name: str = "") -> bool:
        return True # because no sender telegram is leaving the bus into wireless, only status update of the actuators and those ids are bease on the baseId.
    

    def validate_dev_id(self, dev_id: AddressExpression, device_name: str = "") -> bool:
        if GatewayDeviceType.is_transceiver(self.dev_type):
            return self.dev_id_validation_by_transmitter(dev_id, device_name)
        elif GatewayDeviceType.is_bus_gateway(self.dev_type):
            return self.dev_id_validation_by_bus_gateway(dev_id, device_name)
        return False


    def dev_id_validation_by_transmitter(self, dev_id: AddressExpression, device_name: str = "") -> bool:
        result = 0xFF == dev_id[0][0]
        if not result:
            LOGGER.warning(f"{device_name} ({dev_id}): Maybe have wrong device id configured!")
        return result
    

    def dev_id_validation_by_bus_gateway(self, dev_id: AddressExpression, device_name: str = "") -> bool:
        result = config_helpers.compare_enocean_ids(b'\x00\x00\x00\x00', dev_id[0], len=2)
        if not result:
            LOGGER.warning(f"{device_name} ({dev_id}): Maybe have wrong device id configured!")
        return result
    


    async def read_memory_of_all_bus_members(self):
        if not self._reading_memory_of_devices_is_running.is_set():
            # A-r2: set SYNCHRONOUSLY before spawning the worker - previously the
            # flag was set inside the worker thread, so a double button press passed
            # this check twice; two concurrent reads interleave bus telegrams and the
            # second run restores the bus callback to None (library saves/restores
            # it around the read), leaving the gateway deaf until reconnect.
            self._reading_memory_of_devices_is_running.set()
            try:
                await asyncio.to_thread(asyncio.run, self._read_memory_of_all_bus_members())
            except BaseException:
                # if the worker could not even be spawned (executor shutdown, thread
                # OSError), the worker's finally never runs -> clear here so the
                # button does not stay dead
                self._reading_memory_of_devices_is_running.clear()
                raise


    async def _read_memory_of_all_bus_members(self):
        try:
            await request_memory_of_all_devices(self._bus)
        except Exception as e:
            LOGGER.exception(f"[Gateway] [Id: {self.dev_id}] {e}")
        finally:
            self._reading_memory_of_devices_is_running.clear()




    def reconnect(self):
        """Restart the bus connection. BLOCKING - never call directly from the event loop,
        use `await hass.async_add_executor_job(gateway.reconnect)` instead. (K2)"""
        # R3-05: serialize against a concurrent reconnect()/_unload_blocking() so overlapping
        # calls cannot orphan a running bus. If unload already ran, do not resurrect the bus.
        with self._lifecycle_lock:
            if self._shutdown:
                LOGGER.debug("[Gateway] [Id: %d] Gateway is unloaded - not reconnecting.", self.dev_id)
                return
            try:
                LOGGER.info("[Gateway] [Id: %d] Connection Restart", self.dev_id)
                self._bus.stop()
                if self._bus.is_alive():
                    self._bus.join(10)    # wait until thread is really stopped
                    if self._bus.is_alive():
                        LOGGER.warning("[Gateway] [Id: %d] Bus thread did not stop within 10s. Starting new connection anyway.", self.dev_id)
                LOGGER.debug("[Gateway] [Id: %d] Connection stopped", self.dev_id)
                self._init_bus()
                self._bus.start()
            except Exception as e:
                LOGGER.exception(f"[Gateway] [Id: {self.dev_id}] {e}")


    async def async_setup(self):
        """Initialized serial bus and register callback function on HA event bus."""
        self._bus.start()

        LOGGER.debug(f"[Gateway] [Id: {self.dev_id}] Was started.")

        # receive messages from HA event bus
        event_id = config_helpers.get_bus_event_type(gateway_id=self.dev_id, function_id=SIGNAL_SEND_MESSAGE)
        LOGGER.debug(f"[Gateway] [Id: {self.dev_id}] Register gateway bus for message event_id {event_id}")
        self.dispatcher_disconnect_handle = async_dispatcher_connect(
            self.hass, event_id, self._callback_send_message_to_serial_bus
        )

        # Register home assistant service for sending arbitrary telegrams.
        #
        # The service will be registered for each gateway, as the user
        # might have different gateways that cause the eltako relays
        # only to react on them.
        service_name = config_helpers.get_bus_event_type(gateway_id=self.dev_id, function_id=SIGNAL_SEND_MESSAGE_SERVICE)
        LOGGER.debug(f"[Gateway] [Id: {self.dev_id}] Register send message service {service_name}")
        self.hass.services.async_register(DOMAIN, service_name, self.async_service_send_message)


    # Command Section
    async def async_service_send_message(self, event, raise_exception=False) -> None:
        """Send an arbitrary message with the provided eep."""
        LOGGER.debug(f"[Service Send Message: {event.service}] Received event data: {event.data}")

        sender_id_str = event.data.get("id", None)
        try:
            sender_id:AddressExpression = AddressExpression.parse(sender_id_str)
        except Exception:
            LOGGER.error(f"[Service Send Message: {event.service}] No valid sender id defined. (Given sender id: {sender_id_str})")
            return

        sender_eep_str = event.data.get("eep", None)
        try:
            sender_eep:EEP = EEP.find(sender_eep_str)
        except Exception:
            LOGGER.error(f"[Service Send Message: {event.service}] No valid eep defined. (Given eep: {sender_eep_str})")
            return

        # prepare all arguements for eep constructor
        sig = inspect.signature(sender_eep.__init__)
        eep_init_params = [param for param in sig.parameters.values() if param.kind == param.POSITIONAL_OR_KEYWORD and param.name != 'self']
        knargs = {param.name:event.data[param.name] for param in eep_init_params if param.name in event.data}
        LOGGER.debug(f"[Service Send Message: {event.service}] Provided EEP ({sender_eep.__name__}) args: {knargs})")
        # fill missing parameters with 0, EXCEPT enum-typed defaults (e.g. 'priority', 'mode'):
        # replacing an enum default with 0 crashes later in encode_message ('int' has no attribute 'code')
        uknargs = {param.name:(param.default if isinstance(param.default, Enum) else 0)
                   for param in eep_init_params if param.name not in event.data}
        # NOTE: log enum members by name - DefaultEnum.__repr__ in eltako14bus 0.0.73 is broken (UnboundLocalError)
        uknargs_log = {k:(v.name if isinstance(v, Enum) else v) for k,v in uknargs.items()}
        LOGGER.debug(f"[Service Send Message: {event.service}] Missing EEP ({sender_eep.__name__}) args: {uknargs_log})")
        eep_args = knargs
        eep_args.update(uknargs)

        try:
            eep:EEP = sender_eep(**eep_args)
            # create message
            msg = eep.encode_message(sender_id[0])
            LOGGER.debug(f"[Service Send Message: {event.service}] Generated message: {msg} Serialized: {msg.serialize().hex()}")
            # send message
            self.send_message(msg)
        except Exception as e:
            LOGGER.error(f"[Service Send Message: {event.service}] Cannot send message.", exc_info=True, stack_info=True)
            if raise_exception:
                raise e



    def send_message(self, msg: ESP2Message):
        """Put message on RS485 bus. First the message is put onto HA event bus so that other automations can react on messages."""
        event_id = config_helpers.get_bus_event_type(gateway_id=self.dev_id, function_id=SIGNAL_SEND_MESSAGE)
        dispatcher_send(self.hass, event_id, msg)
        dispatcher_send(self.hass, ELTAKO_GLOBAL_EVENT_BUS_ID, {'gateway':self, 'esp2_msg': msg})


    async def async_unload(self):
        """Disconnect callbacks and stop the bus thread without blocking the event loop. (K2)"""
        if self.dispatcher_disconnect_handle:
            self.dispatcher_disconnect_handle()
            self.dispatcher_disconnect_handle = None
        self._reading_memory_of_devices_is_running.clear()
        # joining the bus thread is blocking => run in executor so that HA does not freeze
        await self.hass.async_add_executor_job(self._unload_blocking)
        LOGGER.debug("[Gateway] [Id: %d] Was stopped.", self.dev_id)

    def _unload_blocking(self):
        """Stop the bus thread. BLOCKING - called via executor from async_unload."""
        # R3-05: mark shutdown and stop under the lifecycle lock so a concurrent reconnect()
        # either runs fully before us or becomes a no-op afterwards - never resurrects the bus.
        with self._lifecycle_lock:
            self._shutdown = True
            self._bus.stop()
            if self._bus.is_alive():
                self._bus.join(10)    # never wait forever: a hanging bus thread would block HA shutdown/reload
                if self._bus.is_alive():
                    LOGGER.warning("[Gateway] [Id: %d] Bus thread did not stop within 10s.", self.dev_id)

    def unload(self):
        """Deprecated synchronous variant of async_unload, kept for compatibility.
        BLOCKING - do not call from the event loop."""
        if self.dispatcher_disconnect_handle:
            self.dispatcher_disconnect_handle()
            self.dispatcher_disconnect_handle = None
        self._reading_memory_of_devices_is_running.clear()
        self._unload_blocking()
        LOGGER.debug("[Gateway] [Id: %d] Was stopped.", self.dev_id)


    def _callback_send_message_to_serial_bus(self, msg):
        """Callback method call from HA when receiving events from serial bus."""
        if self._bus.is_active():
            if isinstance(msg, ESP2Message):
                LOGGER.debug("[Gateway] [Id: %d] Send message: %s - Serialized: %s", self.dev_id, msg, msg.serialize().hex())

                # put message on serial bus
                self.hass.create_task(
                    self._bus.send(msg)
                )
                dispatcher_send(self.hass, ELTAKO_GLOBAL_EVENT_BUS_ID, {'gateway':self, 'esp2_msg': msg})
        else:
            LOGGER.warning("[Gateway] [Id: %d] Serial port %s is not available!!! message (%s) was not sent.", self.dev_id, self.serial_path, msg)


    def _callback_receive_message_from_serial_bus(self, message:ESP2Message):
        """Handle Eltako device's callback.

        This is the callback function called by python-enocan whenever there
        is an incoming message.

        IMPORTANT: This runs inside the serial bus thread. The eltakobus library only
        handles SerialException/IOError in its receiver loop - any other exception
        would terminate the receiver thread and the integration would stop receiving
        messages entirely until Home Assistant is restarted. (K1)
        """
        try:
            if type(message) not in [EltakoPoll]:
                LOGGER.debug("[Gateway] [Id: %d] Received message: %s", self.dev_id, message)
                self.report_message_stats()

                if message.body[:2] == b'\x8b\x98':
                    LOGGER.debug("[Gateway] [Id: %d] Received base id: %s", self.dev_id, b2s(message.body[2:6]))
                    self._attr_base_id = AddressExpression( (message.body[2:6], None) )
                    self._fire_base_id_change_handlers(self.base_id)


                # only send messages to HA when base id is known
                if int.from_bytes(self.base_id[0]) != 0:

                    # Send message on local bus. Only devices configure to this gateway will receive those message.
                    event_id = config_helpers.get_bus_event_type(gateway_id=self.dev_id, function_id=SIGNAL_RECEIVE_MESSAGE)
                    dispatcher_send(self.hass, event_id, {'gateway':self, 'esp2_msg': message})

                    if type(message) not in [EltakoDiscoveryRequest]:
                        # Send message on global bus with external/outside address
                        global_msg = prettify(message)
                        # do not change discovery and memory message addresses, base id will be sent upfront so that the receive known to whom the message belong
                        if type(message) in [EltakoWrappedRPS, EltakoWrapped4BS, RPSMessage, Regular1BSMessage, Regular4BSMessage, EltakoMessage]:
                            byte_adr = message.body[-5:-1]
                            # LOGGER.debug(f"[====>>> address: adr: {b2s(byte_adr)}")
                            address = AddressExpression((byte_adr, None))
                            if address.is_local_address():
                                address = address.add(self.base_id)
                                ba = bytearray(global_msg.body)
                                ba[-5:-1] = bytearray(address[0])
                                # LOGGER.debug(f"[====>>> old byte array: {b2s(message.body)}")
                                # LOGGER.debug(f"[====>>> new byte array: {b2s(ba)}")
                                global_msg = prettify(ESP2Message( ba ))


                        LOGGER.debug("[Gateway] [Id: %d] Forwared message (%s) in global bus", self.dev_id, global_msg)
                        dispatcher_send(self.hass, ELTAKO_GLOBAL_EVENT_BUS_ID, {'gateway':self, 'esp2_msg': global_msg})
        except Exception:
            LOGGER.exception("[Gateway] [Id: %d] Unhandled error while processing received message: %s", self.dev_id, message)
            
    
    @property
    def unique_id(self) -> str:
        """Return the unique id of the gateway."""
        return self.serial_path
    

    @property
    def serial_path(self) -> str:
        """Return the serial path of the gateway."""
        return self._attr_serial_path
    

    @property
    def dev_name(self) -> str:
        """Return the device name of the gateway."""
        return self._attr_dev_name
    

    @property
    def dev_id(self) -> int:
        """Return the device id of the gateway."""
        return self._attr_dev_id
    
    @property
    def dev_type(self) -> GatewayDeviceType:
        """Return the device type of the gateway."""
        return self._attr_dev_type
    

    @property
    def base_id(self) -> AddressExpression:
        """Return the base id of the gateway."""
        return self._attr_base_id
    

    @property
    def model(self) -> str:
        """Return the model of the gateway."""
        return self._attr_model
    

    @property
    def identifier(self) -> str:
        """Return the identifier of the gateway."""
        return self._attr_identifier
    
    @property
    def message_delay(self) -> str:
        """Return the message delay of single telegrams to be sent."""
        return str(self._message_delay)
    
    @property
    def is_auto_reconnect_enabled(self) -> str:
        """Return if auto connected is enabled."""
        return str(self._auto_reconnect)

    @property
    def is_connected(self) -> bool:
        """Return whether the underlying bus/TCP connection is currently active. (B1)

        Cheap, non-blocking flag read - used to seed entity availability without
        waiting for the first connection-state notification."""
        try:
            return bool(self._bus.is_active())
        except Exception:
            return False


def detect() -> list[str]:
    """Return a list of candidate paths for USB Eltako gateways.

    This method is currently a bit simplistic, it may need to be
    improved to support more configurations and OS.
    """
    globs_to_test = ["/dev/serial/by-id/*", "/dev/serial/by-path/*"]
    found_paths = []
    for current_glob in globs_to_test:
        found_paths.extend(glob.glob(current_glob))

    return found_paths


def validate_path(path: str, baud_rate: int):
    """Return True if the provided path points to a valid serial port, False otherwise."""
    # H9: close the port again - leaving it open kept the serial device busy so the
    # gateway could not connect afterwards.
    try:
        with serial.serial_for_url(path, baud_rate, timeout=0.1):
            return True
    except serial.SerialException as exception:
        LOGGER.warning("Gateway path %s is invalid: %s", path, str(exception))
        return False
