"""R3-14 / R3-19: EltakoDimmableLight turn_on behaviour.

R3-14 (turn_on without brightness -> plain switching-ON, cmd 0x01) is covered in
test_dimmable_light.py. Here: R3-19 - a dimmable light configured with an F6 rocker sender
cannot transport a brightness; requesting one warns (once) and does NOT fake a brightness."""
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform

from tests.mocks import *
from custom_components.eltako.light import EltakoDimmableLight
from custom_components.eltako.config_helpers import DEFAULT_GENERAL_SETTINGS, CONF_FAST_STATUS_CHANGE
from eltakobus import AddressExpression
from eltakobus.eep import EEP

# mock update of Home Assistant
Entity.schedule_update_ha_state = mock.Mock(return_value=None)


class TestR3CF6Dimmer(unittest.TestCase):

    def _f6_dimmer(self):
        settings = dict(DEFAULT_GENERAL_SETTINGS)
        settings[CONF_FAST_STATUS_CHANGE] = True   # so the optimistic fast-status path runs
        gw = GatewayMock(settings)
        light = EltakoDimmableLight(
            Platform.LIGHT, gw, AddressExpression.parse("00-00-00-01"), "n",
            EEP.find("A5-38-08"),                      # device
            AddressExpression.parse("00-00-B0-01"),    # sender id
            EEP.find("F6-02-01"),                      # rocker sender - cannot dim
        )
        light.send_message = lambda m: None
        return light

    def test_f6_dimmer_brightness_warns_once_and_not_faked(self):
        light = self._f6_dimmer()

        with self.assertLogs("eltako", level="WARNING"):
            light.turn_on(brightness=100)
        # fast-status must NOT fake a brightness for an on/off rocker sender
        self.assertIsNone(light.brightness)
        self.assertTrue(light.is_on)

        # warns only once per entity (no assertLogs -> assertNoLogs on the second call)
        with self.assertNoLogs("eltako", level="WARNING"):
            light.turn_on(brightness=200)


if __name__ == "__main__":
    unittest.main()
