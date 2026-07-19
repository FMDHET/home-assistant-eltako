"""Support for Eltako binary sensors."""
from __future__ import annotations
from typing import Dict

from eltakobus.util import AddressExpression
from eltakobus.eep import *

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant import config_entries
from homeassistant.const import CONF_DEVICE_CLASS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.typing import ConfigType

import time

from .device import *
from .const import *
from .gateway import EnOceanGateway
from .schema import CONF_EEP_SUPPORTED_BINARY_SENSOR
from . import config_helpers, get_gateway_from_hass, get_device_config_for_gateway


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Binary Sensor platform for Eltako."""
    gateway: EnOceanGateway = get_gateway_from_hass(hass, config_entry)
    config: ConfigType = get_device_config_for_gateway(hass, config_entry, gateway)
    
    entities: list[EltakoEntity] = []
    
    platform = Platform.BINARY_SENSOR

    for platform_id in [Platform.BINARY_SENSOR, Platform.SENSOR]:
        if platform_id in config:
            for entity_config in config[platform_id]:
                try:
                    dev_conf = config_helpers.DeviceConf(entity_config, [CONF_DEVICE_CLASS, CONF_INVERT_SIGNAL])
                    _area_start = len(entities)
                    if dev_conf.eep.eep_string in CONF_EEP_SUPPORTED_BINARY_SENSOR:
                        if dev_conf.eep == A5_30_03:
                            name = "Digital Input 0"
                            entities.append(EltakoBinarySensor(platform_id, gateway, dev_conf.id, name, dev_conf.eep, 
                                                                dev_conf.get(CONF_DEVICE_CLASS), dev_conf.get(CONF_INVERT_SIGNAL),
                                                                EntityDescription(key="0", name=name) ))
                            name = "Digital Input 1"
                            entities.append(EltakoBinarySensor(platform_id, gateway, dev_conf.id, name, dev_conf.eep, 
                                                                dev_conf.get(CONF_DEVICE_CLASS), dev_conf.get(CONF_INVERT_SIGNAL),
                                                                EntityDescription(key="1", name=name) ))
                            name = "Digital Input 2"
                            entities.append(EltakoBinarySensor(platform_id, gateway, dev_conf.id, name, dev_conf.eep, 
                                                                dev_conf.get(CONF_DEVICE_CLASS), dev_conf.get(CONF_INVERT_SIGNAL),
                                                                EntityDescription(key="2", name=name) ))
                            name = "Digital Input 3"
                            entities.append(EltakoBinarySensor(platform_id, gateway, dev_conf.id, name, dev_conf.eep, 
                                                                dev_conf.get(CONF_DEVICE_CLASS), dev_conf.get(CONF_INVERT_SIGNAL),
                                                                EntityDescription(key="3", name=name) ))
                            name = "Status of Wake"
                            entities.append(EltakoBinarySensor(platform_id, gateway, dev_conf.id, name, dev_conf.eep, 
                                                                dev_conf.get(CONF_DEVICE_CLASS), dev_conf.get(CONF_INVERT_SIGNAL),
                                                                EntityDescription(key="wake", name=name) ))
                        elif dev_conf.eep == A5_30_01:
                            name = "Digital Input"
                            entities.append(EltakoBinarySensor(platform_id, gateway, dev_conf.id, name, dev_conf.eep, 
                                                                dev_conf.get(CONF_DEVICE_CLASS), dev_conf.get(CONF_INVERT_SIGNAL),
                                                                EntityDescription(key="0", name=name) ))
                            name = "Low Battery"
                            entities.append(EltakoBinarySensor(platform_id, gateway, dev_conf.id, name, dev_conf.eep, 
                                                                dev_conf.get(CONF_DEVICE_CLASS), dev_conf.get(CONF_INVERT_SIGNAL),
                                                                EntityDescription(key="low_battery", name=name) ))
                        else:
                            entities.append(EltakoBinarySensor(platform_id, gateway, dev_conf.id, dev_conf.name, dev_conf.eep,
                                                                dev_conf.get(CONF_DEVICE_CLASS), dev_conf.get(CONF_INVERT_SIGNAL)))

                        apply_area_to_entities(entities, _area_start, dev_conf)   # F1

                except Exception as e:
                    LOGGER.warning("[%s] Could not load configuration for platform_id %s", platform, platform_id)
                    LOGGER.critical(e, exc_info=True)

    # is connection active sensor for gateway (serial connection)
    entities.append(GatewayConnectionState(platform, gateway))

    # dev_id validation not possible because there can be bus sensors as well as decentralized sensors.
    log_entities_to_be_added(entities, platform)
    async_add_entities(entities)

    
class AbstractBinarySensor(EltakoEntity, RestoreEntity, BinarySensorEntity):

    def load_value_initially(self, latest_state:State):
        try:
            if 'unknown' == latest_state.state:
                self._attr_is_on = None
            else:
                if latest_state.state in ['on', 'off']:
                    self._attr_is_on = 'on' == latest_state.state
                else:
                    self._attr_is_on = None
                
        except Exception as e:
            self._attr_is_on = None
            raise e
        
        self.schedule_update_ha_state()

        LOGGER.debug(f"[{Platform.BINARY_SENSOR} {self.dev_id}] value initially loaded: [is_on: {self.is_on}, state: {self.state}]")

class EltakoBinarySensor(AbstractBinarySensor):
    """Representation of Eltako binary sensors such as wall switches.

    Supported EEPs (EnOcean Equipment Profiles):
    - F6-02-01 (Light and Blind Control - Application Style 2)
    - F6-02-02 (Light and Blind Control - Application Style 1)
    - F6-10-00
    - D5-00-01
    """

    # N8: per-instance, not a shared class dict. As a class attribute it was shared
    # across ALL binary sensors of ALL gateways (same switch address on two gateways
    # overwrote each other's push/release correlation) and was never cleared (leak).
    LAST_RECEIVED_TELEGRAMS: Dict[str, Dict]

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name:str, dev_eep: EEP,
                 device_class: str, invert_signal: bool, description: EntityDescription=None):
        """Initialize the Eltako binary sensor."""
        self.LAST_RECEIVED_TELEGRAMS = {}
        if description:
            self.entity_description = EntityDescription(
                key=description.key,
                name=description.name
                )
            self._channel = description.key
        else:
            self._channel = None

        super().__init__(platform, gateway, dev_id, dev_name, dev_eep, self._channel)
        # A1: coerce to bool. Sensors created from the `sensor:` config section pass
        # None (SensorSchema has no invert_signal key) and every state assignment
        # uses `invert_signal != <bool>` as XOR - with None both comparisons are True,
        # so the entity was permanently 'on'.
        self.invert_signal = bool(invert_signal)
        self._attr_device_class = device_class

        if device_class is None or device_class == '':
            if dev_eep in [A5_07_01, A5_08_01]:
                self._attr_device_class = BinarySensorDeviceClass.OCCUPANCY
                self._attr_icon = 'mdi:motion-sensor'
            if dev_eep in [D5_00_01]:
                self._attr_device_class = BinarySensorDeviceClass.WINDOW
            if dev_eep in [F6_10_00]:
                self._attr_device_class = BinarySensorDeviceClass.WINDOW
            

    def value_changed(self, msg: ESP2Message):
        """Fire an event with the data that have changed.

        This method is called when there is an incoming message associated
        with this platform.

        Example message data:
        - 2nd button pressed
            ['0xf6', '0x10', '0x00', '0x2d', '0xcf', '0x45', '0x30']
        - button released
            ['0xf6', '0x00', '0x00', '0x2d', '0xcf', '0x45', '0x20']
        """
        
        try:
            decoded = self.dev_eep.decode_message(msg)
            # no json.dumps: runs per telegram, %s defers str() until debug is actually enabled
            LOGGER.debug("decoded : %s", decoded.__dict__)
            # LOGGER.debug("msg : %s, data: %s", type(msg), msg.data)
        except Exception:
            LOGGER.warning("[%s %s] Could not decode message for eep %s does not fit to message type %s (org %s)",
                            Platform.BINARY_SENSOR, str(self.dev_id), self.dev_eep.eep_string, type(msg).__name__, str(msg.org) )
            return

        telegram_received_time = time.time()

        event_id = config_helpers.get_bus_event_type(self.gateway.dev_id, EVENT_BUTTON_PRESSED, msg.address)
        event_data = {
            "id": event_id,
            "entity_id": self.entity_id,
            "data": int.from_bytes(msg.data, "big"),
            "eep": self.dev_eep.eep_string,
            "switch_address": b2s(msg.address),
            "pressed_buttons": [],
            "prev_pressed_buttons": [],
            "pressed": False,
            "two_buttons_pressed": False,
            "rocker_first_action": None,
            "rocker_second_action": None,
            "push_telegram_received_time_in_sec": telegram_received_time,
            "release_telegram_received_time_in_sec": -1, 
            "push_duration_in_sec": -1,
        }


        # single flag instead of re-testing the EEP list again 180 lines below:
        # keeps the two wall-switch spots in sync when an EEP is added (review finding)
        fire_button_specific_event = False

        # wall switches
        if self.dev_eep in [F6_02_01, F6_02_02]:
            fire_button_specific_event = True
            # LOGGER.debug("[Binary Sensor][%s] Received msg for processing eep %s telegram.", b2s(self.dev_id), self.dev_eep.eep_string)
            pressed_buttons = []
            pressed = decoded.energy_bow == 1
            two_buttons_pressed = decoded.second_action == 1
            fa = decoded.rocker_first_action
            sa = decoded.rocker_second_action

            push_telegram_received_time = telegram_received_time
            release_telegram_received_time = -1

            # Data is only available when button is pressed. 
            # Button cannot be identified when releasing it.
            # if at least one button is pressed
            if pressed:
                if fa == 0:
                    pressed_buttons += ["LB"]
                if fa == 1:
                    pressed_buttons += ["LT"]
                if fa == 2:
                    pressed_buttons += ["RB"]
                if fa == 3:
                    pressed_buttons += ["RT"]
            if two_buttons_pressed:
                if sa == 0:
                    pressed_buttons += ["LB"]
                if sa == 1:
                    pressed_buttons += ["LT"]
                if sa == 2:
                    pressed_buttons += ["RB"]
                if sa == 3:
                    pressed_buttons += ["RT"]

            # fire first event for the entire switch
            event_data.update({
                "pressed_buttons": pressed_buttons,
                "pressed": pressed or two_buttons_pressed,
                "two_buttons_pressed": two_buttons_pressed,
                "rocker_first_action": decoded.rocker_first_action,
                "rocker_second_action": decoded.rocker_second_action,
            })
            

            # Show status change in HA. It will only for the moment when the button is pushed down.
            # Change first button status so that automations can request it after event was fired.
            # != is XOR
            self._attr_is_on = self.invert_signal != (len(pressed_buttons) > 0)
        
        # switch / single button
        elif self.dev_eep in [F6_01_01]:

            # extend event data
            event_data['pressed'] = decoded.button_pushed
                
            # Show status change in HA. It will only for the moment when the button is pushed down.
            self._attr_is_on = self.invert_signal != ( decoded.button_pushed )
            self.schedule_update_ha_state()

            return

        elif self.dev_eep in [F6_10_00]:
            # LOGGER.debug("[Binary Sensor][%s] Received msg for processing eep %s telegram.", b2s(self.dev_id), self.dev_eep.eep_string)
            event_data['pressed'] = decoded.handle_position == 0

            # is_on == True => open
            self._attr_is_on = self.invert_signal != (decoded.handle_position > 0)

        elif self.dev_eep in [D5_00_01]:
            # LOGGER.debug("[Binary Sensor][%s] Received msg for processing eep %s telegram.", b2s(self.dev_id), self.dev_eep.eep_string)
            # learn button: 0=pressed, 1=not pressed
            if decoded.learn_button == 0:
                return
            
            event_data['pressed'] = decoded.contact == 1

            # EnOcean D5-00-01: contact bit 1 = closed, 0 = open. is_on == True => open (device class window)
            # NOTE: was `self.invert_signal != decoded.contact == 1` which Python chains to
            # `(invert != contact) and (contact == 1)` => always wrong state for closed windows.
            self._attr_is_on = self.invert_signal != (decoded.contact == 0)

        elif self.dev_eep in [A5_08_01]:
            # Occupancy Sensor
            # LOGGER.debug("[Binary Sensor][%s] Received msg for processing eep %s telegram.", b2s(self.dev_id), self.dev_eep.eep_string)
            if decoded.learn_button == 0:
                return
                
            event_data['pressed'] = decoded.pir_status == 1

            # != is XOR; parentheses required, chained comparison broke the inverted case
            self._attr_is_on = self.invert_signal != (decoded.pir_status == 1)

        elif self.dev_eep in [A5_07_01]:
            # LOGGER.debug("[Binary Sensor][%s] Received msg for processing eep %s telegram.", b2s(self.dev_id), self.dev_eep.eep_string)
            # A4: ignore teach-in telegrams (LRN bit 0) like the A5-08-01/D5-00-01 branches do
            if decoded.learn_button == 0:
                return

            # A3: pir_status is the RAW byte (0..255, occupancy = >=128); using the
            # raw value made `pressed` almost never True. Use pir_status_on like the
            # state assignment below.
            event_data['pressed'] = decoded.pir_status_on == 1

            # != is XOR; parentheses required, chained comparison broke the inverted case
            self._attr_is_on = self.invert_signal != (decoded.pir_status_on == 1)

        elif self.dev_eep in [A5_30_01]:
            # A4: ignore teach-in telegrams
            if decoded.learn_button == 0:
                return

            if self.description_key == "low_battery":
                event_data['pressed'] = decoded.low_battery
                self._attr_is_on = self.invert_signal != decoded.low_battery
            else:
                event_data['pressed'] = decoded._contact_closed
                self._attr_is_on = self.invert_signal != decoded._contact_closed


        elif self.dev_eep in [A5_30_03]:
            # A4: ignore teach-in telegrams
            if decoded.learn_button == 0:
                return

            if self.description_key == "0":
                if decoded.digital_input_0:
                    event_data['pressed_buttons'] = [self.description_key]
                    event_data['pressed'] = True
                self._attr_is_on = self.invert_signal != decoded.digital_input_0

            elif self.description_key == "1":
                if decoded.digital_input_1:
                    event_data['pressed_buttons'] = [self.description_key]
                    event_data['pressed'] = True
                self._attr_is_on = self.invert_signal != decoded.digital_input_1

            elif self.description_key == "2":
                if decoded.digital_input_2:
                    event_data['pressed_buttons'] = [self.description_key]
                    event_data['pressed'] = True
                self._attr_is_on = self.invert_signal != decoded.digital_input_2

            elif self.description_key == "3":
                if decoded.digital_input_3:
                    event_data['pressed_buttons'] = [self.description_key]
                    event_data['pressed'] = True
                self._attr_is_on = self.invert_signal != decoded.digital_input_3

            elif self.description_key == "wake":
                if decoded.status_of_wake:
                    event_data['pressed_buttons'] = [self.description_key]
                    event_data['pressed'] = True
                self._attr_is_on = self.invert_signal != decoded.status_of_wake
            else:
                # do not raise: an exception inside value_changed would abort message processing (M6)
                LOGGER.warning("[%s %s] EEP %s: Unknown description key '%s' for A5-30-03.", Platform.BINARY_SENSOR, str(self.dev_id), A5_30_03.eep_string, str(self.description_key))
                return

        else:
            LOGGER.warning("[%s %s] EEP %s not found for data processing.", Platform.BINARY_SENSOR, str(self.dev_id), self.dev_eep.eep_string)
            return
        
        self.schedule_update_ha_state()

        # prepare event data
        LOGGER.debug("Fire event for binary sensor.")
        event_data['is_on'] = self.is_on
        prev_pressed_buttons = self.LAST_RECEIVED_TELEGRAMS.get( b2s(self.dev_id), {'pressed_buttons':[]})['pressed_buttons']
        if event_data['pressed_buttons'] == [] and prev_pressed_buttons != []:
            event_data['prev_pressed_buttons'] = prev_pressed_buttons
        # when button released
        if not event_data['pressed']:
            # K4: use .get() with default (previously crashed with TypeError on every release telegram)
            push_telegram_received_time = self.LAST_RECEIVED_TELEGRAMS.get( b2s(self.dev_id), {}).get('push_telegram_received_time_in_sec', -1)
            release_telegram_received_time = telegram_received_time

            if push_telegram_received_time == -1:
                # no previous push telegram known (e.g. HA restarted in between) => push duration cannot be computed.
                # Do not raise: it would abort the event pipeline. Fire the event with the -1 defaults instead.
                LOGGER.debug("[%s %s] EEP %s: No information about previous push event. Push duration unknown.", Platform.BINARY_SENSOR, str(self.dev_id), self.dev_eep.eep_string)
                event_data['push_telegram_received_time_in_sec'] = -1
            else:
                event_data.update({
                    "push_telegram_received_time_in_sec": push_telegram_received_time,
                    "release_telegram_received_time_in_sec": release_telegram_received_time,
                    "push_duration_in_sec": float(release_telegram_received_time - push_telegram_received_time),
                })

        self.LAST_RECEIVED_TELEGRAMS[b2s(self.dev_id)] = event_data
        self.hass.bus.fire(event_id, event_data)

        # Wall switches additionally fire a button-specific event (e.g. `..._rt`,
        # `..._lt-rb` becomes `..._lt_rb`) so automations can trigger on a single
        # button without conditions. (Restored pre-v2.0.0 behavior, see changes.md.)
        # On release no button info is in the telegram => use the previously pressed buttons.
        if fire_button_specific_event:
            # sorted: F6-02 reports two-button chords in contact-closure order, so the
            # same physical combination could otherwise alternate between `..._lt_rb`
            # and `..._rb_lt` (review finding). Payload order stays untouched.
            buttons = sorted(event_data['pressed_buttons'] or event_data['prev_pressed_buttons'])
            if buttons:
                button_event_id = config_helpers.get_bus_event_type(self.gateway.dev_id, EVENT_BUTTON_PRESSED, msg.address, '-'.join(buttons))
                # copy so the button-specific id does not leak into the already fired base event
                button_event_data = dict(event_data, id=button_event_id)
                LOGGER.debug("[%s %s] Send button-specific event: %s (buttons: %s)", Platform.BINARY_SENSOR, str(self.dev_id), button_event_id, buttons)
                self.hass.bus.fire(button_event_id, button_event_data)

class GatewayConnectionState(AbstractBinarySensor):
    """Protocols last time when message received"""

    def __init__(self, platform: str, gateway: EnOceanGateway):
        key = "Gateway_Connection_State"

        self._attr_icon = "mdi:connection"
        self._attr_name = "Connected"
        
        super().__init__(platform, gateway, gateway.base_id, dev_name="Connected", description_key=key)

    async def async_added_to_hass(self) -> None:
        # H7: register in async_added_to_hass and deregister on removal (was in __init__ -> leaked on reload)
        await super().async_added_to_hass()
        self.gateway.add_connection_state_changed_handler(self.async_value_changed)
        self.async_on_remove(lambda: self.gateway.remove_connection_state_changed_handler(self.async_value_changed))

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.gateway.serial_path)},
            name= self.gateway.dev_name,
            manufacturer=MANUFACTURER,
            model=self.gateway.model,
            via_device=(DOMAIN, self.gateway.serial_path)
        )
    
    async def async_value_changed(self, connected:bool) -> None:
        try:
            self.value_changed(connected)
        except AttributeError:
            # Home Assistant is not ready yet
            pass
    
    def value_changed(self, connected: bool) -> None:
        """Update the current value."""
        LOGGER.debug("[%s] [Gateway Id %s] connected %s", Platform.BINARY_SENSOR, str(self.gateway.dev_id), str(connected) )

        self._attr_is_on = connected
        self.schedule_update_ha_state()