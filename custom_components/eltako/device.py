"""Representation of an Eltako device."""

from eltakobus.message import ESP2Message, EltakoWrappedRPS, EltakoWrapped1BS, EltakoWrapped4BS, RPSMessage, Regular4BSMessage, Regular1BSMessage
from eltakobus.util import AddressExpression, b2s
from eltakobus.eep import EEP

from homeassistant.core import State
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect, dispatcher_send
from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform

from .const import *
from .gateway import EnOceanGateway
from . import config_helpers


class EltakoEntity(Entity):
    """Parent class for all entities associated with the Eltako component."""

    # F1: HA area from YAML (config_helpers.CONF_AREA). Set by the platform setup
    # after construction; None = no area configured.
    _attr_dev_area: str | None = None

    # B1: real device entities become "unavailable" when their gateway loses the
    # bus/TCP connection, so a silent drop is VISIBLE in the UI (and gates
    # automations) instead of showing stale values. Gateway-owned diagnostic/config
    # entities set this False - they must stay visible precisely WHILE disconnected
    # (the connection-state sensor, the reconnect button, the gateway info fields).
    _attr_follow_gateway_availability: bool = True
    # cached connection state; seeded in async_added_to_hass, updated by the handler.
    _gateway_connected: bool = True

    # Per-device override for the global `fast_status_change` general setting.
    # None = inherit the global value; True/False = force it for this device only.
    # Set by the light/switch platform setup from the YAML `fast_status_change:` key.
    _fast_status_change: bool | None = None


    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name: str="Device", dev_eep: EEP=None, description_key:str=None):
        """Initialize the device."""
        self._attr_has_entity_name = True
        # N2: Eltako entities are push (local_push); polling adds needless cyclic load
        self._attr_should_poll = False

        self._attr_ha_platform = platform
        self._attr_gateway = gateway
        self.hass = self.gateway.hass
        self.general_settings = self.gateway.general_settings
        self._attr_dev_id = dev_id
        self._attr_dev_name = config_helpers.get_device_name(dev_name, dev_id, self.general_settings)
        self._attr_dev_eep = dev_eep
        self.listen_to_addresses = []
        
        # calculate external address
        if self.dev_id.is_local_address():
            self._external_dev_id = self.dev_id.add(self.gateway.base_id)
        else:
            self._external_dev_id = self.dev_id

        self.listen_to_addresses.append( self._external_dev_id[0] )
        # self.listen_to_addresses.append( self.dev_id[0] )

        self.description_key = description_key
        self._attr_unique_id = config_helpers.get_device_id(gateway.dev_id, self.dev_id, self._get_description_key())
        self.entity_id = f"{self._attr_ha_platform}.{self._attr_unique_id}"

        LOGGER.debug(f"[{self._attr_ha_platform} {self.dev_id}] Added entity {self.dev_name} ({type(self).__name__}).")

    def _get_description_key(self, description_key:str=None):
        if description_key is not None:
            self.description_key = description_key

        if hasattr(self, 'entity_description') and self.entity_description is not None:
            if self.description_key is None:
                self.description_key = self.entity_description.key
                
        return self.description_key

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={
                (DOMAIN, b2s(self.dev_id) )
            },
            name=self.dev_name,
            manufacturer=MANUFACTURER,
            # M13: dev_eep can be None (e.g. gateway-owned diagnostic entities) -> AttributeError
            model=self.dev_eep.eep_string if self.dev_eep else None,
            via_device=(DOMAIN, self.gateway.serial_path),
            # F1: NOTE - deliberately NOT using DeviceInfo.suggested_area here.
            # It is deprecated (removed in HA 2026.9) and would be passed on every
            # device. Area assignment is handled entirely by _assign_area_if_unset()
            # below (non-destructive), which covers new AND existing devices.
        )
    

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        await super().async_added_to_hass()

        # Register callbacks.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, ELTAKO_GLOBAL_EVENT_BUS_ID, self._message_received_callback
            )
        )

        # B1: couple availability to the gateway connection state. Seed the current
        # value synchronously (so the very first state write is already correct),
        # then subscribe. add_connection_state_changed_handler also notifies this
        # handler once with the current state right after registration.
        if self._attr_follow_gateway_availability:
            try:
                self._gateway_connected = self.gateway.is_connected
            except Exception:
                LOGGER.debug("[%s %s] Could not seed gateway connection state.", self._attr_ha_platform, self.dev_id)
            self.gateway.add_connection_state_changed_handler(self._on_gateway_connection_state)
            self.async_on_remove(
                lambda: self.gateway.remove_connection_state_changed_handler(self._on_gateway_connection_state)
            )

        # F1: assign the configured area to devices that exist already but have NO
        # area yet (suggested_area only takes effect on creation). Deliberately
        # non-destructive: a user's manual area assignment is never overwritten.
        if self._attr_dev_area:
            self._assign_area_if_unset(self._attr_dev_area)

        # load initial value
        if isinstance(self, RestoreEntity):
            # check if value is not set
            is_value_available = getattr(self, '_attr_native_value', None)
            if is_value_available is None:
                is_value_available = getattr(self, '_attr_is_on', None)

            # update values
            if is_value_available is None:
                latest_state:State = await self.async_get_last_state()
                if latest_state is not None:
                    self.load_value_initially(latest_state)


    def _assign_area_if_unset(self, area_name: str) -> None:
        """Ensure the area exists and link this device to it - only if it has no
        area yet. Never overrides a manual user assignment. (F1)"""
        try:
            area_reg = ar.async_get(self.hass)
            area = area_reg.async_get_area_by_name(area_name)
            if area is None:
                area = area_reg.async_create(area_name)
            device_reg = dr.async_get(self.hass)
            device = device_reg.async_get_device(identifiers={(DOMAIN, b2s(self.dev_id))})
            if device is not None and device.area_id is None:
                device_reg.async_update_device(device.id, area_id=area.id)
                LOGGER.debug("[%s %s] Assigned device to area '%s'.", self._attr_ha_platform, self.dev_id, area_name)
        except Exception:
            # area assignment is best-effort; never break entity setup over it
            LOGGER.exception("[%s %s] Could not assign area '%s'.", self._attr_ha_platform, self.dev_id, area_name)

    def load_value_initially(self, latest_state:State):
        """This function is implemented in the concrete devices classes"""
        LOGGER.warning(f"[{self._attr_ha_platform} {self.dev_id}] DOES NOT HAVE AN IMPLEMENTATION FOR: load_value_initially()")
        LOGGER.debug(f"[{self._attr_ha_platform} {self.dev_id}] latest state - state: {latest_state.state}")
        LOGGER.debug(f"[{self._attr_ha_platform} {self.dev_id}] latest state - attributes: {latest_state.attributes}")
        

    def validate_dev_id(self) -> bool:
        return self.gateway.validate_dev_id(self.dev_id, self.dev_name)


    def validate_sender_id(self, sender_id=None) -> bool:

        if sender_id is None:
            # R3-12: actuators (light/switch/cover/climate) store the sender as the PRIVATE
            # `_sender_id`; only TeachInButton exposes the public `sender_id`. Without this
            # fallback the validation was a silent no-op for every actuator, so a mistyped
            # sender id never produced the intended warning. Entities without any sender
            # (sensors, gateway info fields) have neither attribute -> stays None -> True.
            sender_id = getattr(self, "sender_id", None)
            if sender_id is None:
                sender_id = getattr(self, "_sender_id", None)

        if sender_id is not None:
            # A7: use the resolved local variable - previously self.sender_id was
            # validated even when an explicit sender_id was passed (AttributeError
            # for entities without a sender_id attribute, wrong id otherwise).
            return self.gateway.validate_sender_id(sender_id, self.dev_name)
        return True

    @property
    def external_dev_id(self) -> str:
        return b2s(self._external_dev_id)

    @property
    def dev_name(self) -> str:
        """Return the name of device."""
        return self._attr_dev_name

    @property
    def dev_eep(self):
        """Return the eep of device."""
        return self._attr_dev_eep
    
    @property
    def dev_id(self) -> AddressExpression:
        """Return the id of device."""
        return self._attr_dev_id
    
    @property
    def gateway(self) -> EnOceanGateway:
        """Return the supporting gateway of device."""
        return self._attr_gateway

    # N10: duplicate dev_id property removed (identical definition exists above)

    @property
    def unique_id(self) -> str:
        """Return the unique id of device"""
        return self._attr_unique_id

    @property
    def fast_status_change(self) -> bool:
        """Return the effective fast-status-change flag for this entity.

        A per-device value (light/switch YAML `fast_status_change:`) overrides the
        global `general_settings` value; None (key absent) inherits the global one.
        When True, turn_on/turn_off set the state optimistically so the entity always
        reports a definite on/off - Home Assistant then renders a single toggle instead
        of the separate on/off buttons it shows for an `unknown` state (which is what a
        fire-and-forget F6 rocker sender otherwise leaves the entity in, as it gets no
        status feedback)."""
        if self._fast_status_change is not None:
            return self._fast_status_change
        # .get() default: DEFAULT_GENERAL_SETTINGS always carries the key, but stay
        # defensive so a hand-built general_settings dict cannot KeyError here.
        return self.general_settings.get(CONF_FAST_STATUS_CHANGE, False)

    @property
    def available(self) -> bool:
        """Return if the entity is available. (B1)

        Device entities follow the gateway connection state; gateway-owned
        diagnostic/config entities opt out via _attr_follow_gateway_availability
        and keep the default (always available)."""
        if self._attr_follow_gateway_availability:
            return self._gateway_connected
        return super().available

    async def _on_gateway_connection_state(self, connected: bool) -> None:
        """B1: gateway bus connect/disconnect -> refresh this entity's availability.

        Runs on the HA event loop (scheduled thread-safely by the gateway's
        _schedule_handler). The notified `connected` value is intentionally NOT
        trusted: the register-time immediate-notify and real connection-change
        events can be scheduled out of order relative to a concurrent flap, so
        applying the passed value last-writer-wins could strand availability at a
        stale value. Instead we reconcile to the gateway's CURRENT state
        (`is_connected` = the bus's live, cheap, non-blocking flag), which makes the
        outcome independent of delivery order and self-healing. Only writes on an
        actual change."""
        new_state = self.gateway.is_connected
        if new_state == self._gateway_connected:
            return
        self._gateway_connected = new_state
        try:
            self.async_write_ha_state()
        except Exception:
            # Best-effort: a not-yet-added entity or a raising state-calculation
            # property must never crash the connection-state fan-out or surface as an
            # unhandled-task traceback on every connection toggle. The correct
            # availability is already cached and reported on the next successful write.
            # (connected = the notified value; new_state = the reconciled live truth.)
            LOGGER.debug("[%s %s] Could not write availability state (notified=%s, reconciled=%s).",
                         self._attr_ha_platform, self.dev_id, connected, new_state, exc_info=True)

    def _message_received_callback(self, data: dict) -> None:
        """Handle incoming messages."""
        # H1: error barrier - a single faulty value_changed() (unexpected telegram,
        # decode error, ...) must not abort message processing or produce a traceback
        # per telegram. Log it and keep the entity alive.
        try:
            msg = data.get('esp2_msg')
            if msg is None:
                return
            msg_types = [EltakoWrappedRPS, EltakoWrapped1BS, EltakoWrapped4BS, RPSMessage, Regular1BSMessage, Regular4BSMessage]

            if type(msg) in msg_types:
                adr = AddressExpression((msg.address, None))
                if adr.is_local_address():
                    adr = adr.add(self.gateway.base_id)

                # LOGGER.debug(f"[Device ID: {self.dev_id}] check if message address {b2s(msg.address)} is in registered list {', '.join([b2s(a) for a in self.listen_to_addresses])}")
                if adr[0] in self.listen_to_addresses:
                    ## TODO: filter out message sent twice through other gateways
                    self.value_changed(msg)
        except Exception:
            LOGGER.exception("[%s %s] Error while processing received message.", self._attr_ha_platform, self.dev_id)


    def value_changed(self, msg: ESP2Message):
        """Update the internal state of the device when a message arrives."""
    
    def send_message(self, msg: ESP2Message):
        """Put message on RS485 bus. First the message is put onto HA event bus so that other automations can react on messages."""
        event_id = config_helpers.get_bus_event_type(self.gateway.dev_id, SIGNAL_SEND_MESSAGE)
        dispatcher_send(self.hass, event_id, msg)
        

def apply_area_to_entities(entities:list[EltakoEntity], start_index:int, dev_conf) -> None:
    """F1: set the configured HA area on every entity appended for one device
    config (entities[start_index:]). No-op when no area is configured."""
    area = getattr(dev_conf, 'area', None)
    if not area:
        return
    for e in entities[start_index:]:
        e._attr_dev_area = area

def validate_actuators_dev_and_sender_id(entities:list[EltakoEntity]):
    """Only call it for actuators."""
    for e in entities:
        e.validate_dev_id()
        e.validate_sender_id()

def log_entities_to_be_added(entities:list[EltakoEntity], platform:Platform) -> None:
    for e in entities:
        temp_eep = ""
        if e.dev_eep:
             temp_eep = f"eep: {e.dev_eep.eep_string}),"
        LOGGER.debug(f"[{platform} {e.dev_id}] Add entity {e.dev_name} (id: {e.dev_id},{temp_eep} gw: {e.gateway.dev_name}, listens to: {', '.join([b2s(a) for a in e.listen_to_addresses])}) to Home Assistant.")

# N10: get_entity_from_hass removed - unused and relied on the internal
# hass.data[DATA_ENTITY_PLATFORM] structure (also crashed on dev_eep=None).