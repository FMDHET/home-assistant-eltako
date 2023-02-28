"""Support for Eltako light sources."""
from __future__ import annotations

import math
from typing import Any

from eltakobus.util import combine_hex
from eltakobus.util import AddressExpression
import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    PLATFORM_SCHEMA,
    ColorMode,
    LightEntity,
)
from homeassistant.const import CONF_ID, CONF_NAME
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .device import EltakoEntity
from .const import CONF_ID_REGEX, CONF_EEP, DOMAIN, MANUFACTURER

CONF_EEP_SUPPORTED = ["A5-38-08", "M5-38-08"]
CONF_SENDER_ID = "sender_id"

DEFAULT_NAME = "Light"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_ID): cv.matches_regex(CONF_ID_REGEX),
        vol.Required(CONF_EEP): vol.In(CONF_EEP_SUPPORTED),
        vol.Required(CONF_SENDER_ID): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    }
)


def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Eltako light platform."""
    sender_id = config.get(CONF_SENDER_ID)
    dev_name = config.get(CONF_NAME)
    dev_eep = config.get(CONF_EEP)
    dev_id = AddressExpression.parse(config.get(CONF_ID))
    
    if dev_eep in ["A5-38-08"]:
        add_entities([EltakoDimmableLight(dev_id, dev_name, dev_eep, sender_id)])
    elif dev_eep in ["AM-38-08"]:
        add_entities([EltakoSwitchableLight(dev_id, dev_name, dev_eep, sender_id)])

class EltakoDimmableLight(EltakoEntity, LightEntity):
    """Representation of an Eltako light source."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, dev_id, dev_name, dev_eep, sender_id):
        """Initialize the Eltako light source."""
        super().__init__(dev_id, dev_name)
        self._dev_eep = dev_eep
        self._on_state = False
        self._brightness = 50
        self._sender_id = sender_id
        self._attr_unique_id = f"{DOMAIN}_{dev_id.plain_address().hex()}"
        self.entity_id = f"light.{self.unique_id}"

    @property
    def name(self):
        """Return the name of the device if any."""
        return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={
                (DOMAIN, self.unique_id)
            },
            name=self.dev_name,
            manufacturer=MANUFACTURER,
            model=self._dev_eep,
        )

    @property
    def brightness(self):
        """Brightness of the light.

        This method is optional. Removing it indicates to Home Assistant
        that brightness is not supported for this light.
        """
        return self._brightness

    @property
    def is_on(self):
        """If light is on."""
        return self._on_state

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the light source on or sets a specific dimmer value."""
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            self._brightness = brightness

        bval = math.floor(self._brightness / 256.0 * 100.0)
        if bval == 0:
            bval = 1
        command = [0xA5, 0x02, bval, 0x01, 0x09]
        command.extend(self._sender_id)
        command.extend([0x00])
        self.send_command(command, [], 0x01)
        self._on_state = True

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the light source off."""
        command = [0xA5, 0x02, 0x00, 0x01, 0x09]
        command.extend(self._sender_id)
        command.extend([0x00])
        self.send_command(command, [], 0x01)
        self._on_state = False

    def value_changed(self, msg):
        """Update the internal state of this device.

        Dimmer devices like Eltako FUD61 send telegram in different RORGs.
        We only care about the 4BS (0xA5).
        """
        if self._dev_eep in ["A5-38-08"]:
            if msg.org != 0x07 or msg.data[0] != 0x02:
                return
            
            # Bits should be data (0x08), absolute (not 0x04), don't store (not 0x02), and on or off fitting the dim value (0x01)
            expected_3 = 0x09 if msg.data[1] != 0 else 0x08
            if msg.data[3] != expected_3:
                return

            val = msg.data[1]
            self._brightness = math.floor(val / 100.0 * 256.0)
            self._on_state = bool(val != 0)
            self.schedule_update_ha_state()

class EltakoSwitchableLight(EltakoEntity, LightEntity):
    """Representation of an Eltako light source."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, dev_id, dev_name, dev_eep, sender_id):
        """Initialize the Eltako light source."""
        super().__init__(dev_id, dev_name)
        self._dev_eep = dev_eep
        self._on_state = False
        self._sender_id = sender_id
        self._attr_unique_id = f"{DOMAIN}_{dev_id.plain_address().hex()}"
        self.entity_id = f"light.{self.unique_id}"

    @property
    def name(self):
        """Return the name of the device if any."""
        return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={
                (DOMAIN, self.unique_id)
            },
            name=self.dev_name,
            manufacturer=MANUFACTURER,
            model=self._dev_eep,
        )

    @property
    def is_on(self):
        """If light is on."""
        return self._on_state

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the light source on or sets a specific dimmer value."""
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            self._brightness = brightness

        bval = math.floor(self._brightness / 256.0 * 100.0)
        if bval == 0:
            bval = 1
        command = [0xA5, 0x02, bval, 0x01, 0x09]
        command.extend(self._sender_id)
        command.extend([0x00])
        self.send_command(command, [], 0x01)
        self._on_state = True

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the light source off."""
        command = [0xA5, 0x02, 0x00, 0x01, 0x09]
        command.extend(self._sender_id)
        command.extend([0x00])
        self.send_command(command, [], 0x01)
        self._on_state = False

    def value_changed(self, msg):
        """Update the internal state of this device."""
        if self._dev_eep in ["M5-38-08"]:
            if msg.org != 0x05:
                return
                
            if msg.data[0] == 0x70:
                self._on_state = True
            elif msg.data[0] == 0x50:
                self._on_state = False
            self.schedule_update_ha_state()
