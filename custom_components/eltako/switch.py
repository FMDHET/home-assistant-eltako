"""Support for Eltako switches."""
from __future__ import annotations

from typing import Any

from eltakobus.util import AddressExpression
from eltakobus.eep import *

from homeassistant import config_entries
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType

from . import config_helpers, get_gateway_from_hass, get_device_config_for_gateway
from .config_helpers import DeviceConf
from .device import *
from .gateway import EnOceanGateway
from .const import *


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eltako switch platform."""
    gateway: EnOceanGateway = get_gateway_from_hass(hass, config_entry)
    config: ConfigType = get_device_config_for_gateway(hass, config_entry, gateway)

    entities: list[EltakoEntity] = []
    
    platform = Platform.SWITCH
    if platform in config:
        for entity_config in config[platform]:
            try:
                dev_conf = DeviceConf(entity_config)
                _area_start = len(entities)
                sender_config = config_helpers.get_device_conf(entity_config, CONF_SENDER)

                entities.append(EltakoSwitch(platform, gateway, dev_conf.id, dev_conf.name, dev_conf.eep, sender_config.id, sender_config.eep))
                apply_area_to_entities(entities, _area_start, dev_conf)   # F1

            except Exception as e:
                LOGGER.warning("[%s] Could not load configuration", platform)
                LOGGER.critical(e, exc_info=True)
                
    
    validate_actuators_dev_and_sender_id(entities)
    log_entities_to_be_added(entities, platform)
    async_add_entities(entities)


class EltakoSwitch(EltakoEntity, SwitchEntity, RestoreEntity):
    """Representation of an Eltako switch device."""

    def __init__(self, platform:str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name: str, dev_eep: EEP, sender_id: AddressExpression, sender_eep: EEP):
        """Initialize the Eltako switch device."""
        super().__init__(platform, gateway, dev_id, dev_name, dev_eep)
        self._sender_id = sender_id
        self._sender_eep = sender_eep
        
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
            # H2: never raise from load_value_initially - it would prevent the entity from being added
            LOGGER.warning("[%s %s] Could not restore last state '%s': %s", Platform.SWITCH, self.dev_id, latest_state.state, str(e))
            self._attr_is_on = None

        self.schedule_update_ha_state()

        LOGGER.debug(f"[{Platform.SWITCH} {str(self.dev_id)}] value initially loaded: [is_on: {self.is_on}, state: {self.state}]")


    def turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        address, discriminator = self._sender_id
        
        if self._sender_eep in [F6_02_01, F6_02_02]:
            # in PCT14 function 02 'direct  pushbutton top on' needs to be configured
            if discriminator == "left":
                action = 1  # 0x30
            elif discriminator == "right":
                action = 3  # 0x70
            else:
                action = 1
                
            pressed_msg = F6_02_01(action, 1, 0, 0).encode_message(address)
            self.send_message(pressed_msg)
            
            released_msg = F6_02_01(action, 0, 0, 0).encode_message(address)
            self.send_message(released_msg)
        
        elif self._sender_eep == A5_38_08:
            switching = CentralCommandSwitching(0, 1, 0, 0, 1)
            msg = A5_38_08(command=0x01, switching=switching).encode_message(address)
            self.send_message(msg)

        else:
            LOGGER.warning("[%s %s] Sender EEP %s not supported.", Platform.SWITCH, str(self.dev_id), self._sender_eep.eep_string)
            return
        
        if self.general_settings[CONF_FAST_STATUS_CHANGE]:
            self._attr_is_on = True
            self.schedule_update_ha_state()


    def turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        address, discriminator = self._sender_id
        
        if self._sender_eep in [F6_02_01, F6_02_02]:
            # in PCT14 function 02 'direct  pushbutton top on' needs to be configured
            if discriminator == "left":
                action = 0  # 0x10
            elif discriminator == "right":
                action = 2  # 0x50
            else:
                action = 0
                
            pressed_msg = F6_02_01(action, 1, 0, 0).encode_message(address)
            self.send_message(pressed_msg)
            
            released_msg = F6_02_01(action, 0, 0, 0).encode_message(address)
            self.send_message(released_msg)

        elif self._sender_eep == A5_38_08:
            switching = CentralCommandSwitching(0, 1, 0, 0, 0)
            msg = A5_38_08(command=0x01, switching=switching).encode_message(address)
            self.send_message(msg)

        else:
            LOGGER.warning("[%s %s] Sender EEP %s not supported.", Platform.SWITCH, str(self.dev_id), self._sender_eep.eep_string)
            return

        if self.general_settings[CONF_FAST_STATUS_CHANGE]:
            self._attr_is_on = False
            self.schedule_update_ha_state()


    def value_changed(self, msg: ESP2Message):
        """Update the internal state of the switch."""
        # R3-13: the switch EEPs (M5-38-08, F6-02-xx) are all RPS (org 0x05). Filter other
        # RORGs BEFORE decoding, so the documented FSR14M-2x dual config (same address listed
        # as switch AND A5-12-01 meter, org 0x07) no longer raises WrongOrgError and logs a
        # WARNING per telegram. Mirrors the M4 org filter in EltakoDimmableLight.
        if msg.org != 0x05:
            LOGGER.debug("[%s %s] Ignoring non-RPS telegram (org=%s).", Platform.SWITCH, str(self.dev_id), msg.org)
            return
        try:
            decoded = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("[%s %s] Could not decode message: %s", Platform.SWITCH, str(self.dev_id), str(e))
            return

        if self.dev_eep in [M5_38_08]:
            self._attr_is_on = decoded.state
            self.schedule_update_ha_state()

        elif self.dev_eep in [F6_02_01, F6_02_02]:
            # only if button pushed down / ignore button release message

            button_filter = self.dev_id[1] is None
            button_filter |= self.dev_id[1] is not None and self.dev_id[1] == 'left' and decoded.rocker_first_action == 1
            button_filter |= self.dev_id[1] is not None and self.dev_id[1] == 'right' and decoded.rocker_first_action == 3
            
            if button_filter and decoded.energy_bow:
                self._attr_is_on = not self._attr_is_on
                self.schedule_update_ha_state()

        else:
            LOGGER.warning("[%s %s] Device EEP %s not supported.", Platform.SWITCH, str(self.dev_id), self.dev_eep.eep_string)