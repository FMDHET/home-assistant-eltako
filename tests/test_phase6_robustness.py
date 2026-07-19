"""Regression tests for Phase 6 robustness fixes (M7, M9).

M7 - EltakoSensorEntityDescription must be a frozen dataclass.
M9 - get_id_from_gateway_name must not crash on malformed names;
     get_device_config must tolerate a config without a gateway section.
"""
import dataclasses
import unittest

from custom_components.eltako import config_helpers
from custom_components.eltako.sensor import EltakoSensorEntityDescription
from custom_components.eltako.const import CONF_GATEWAY
from homeassistant.const import CONF_ID, CONF_DEVICES


class TestPhase6Robustness(unittest.TestCase):

    # --- M7 ---------------------------------------------------------------
    def test_sensor_entity_description_is_frozen(self):
        desc = EltakoSensorEntityDescription(key="x")
        self.assertTrue(type(desc).__dataclass_params__.frozen)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            desc.key = "y"

    # --- M9: get_id_from_gateway_name ------------------------------------
    def test_get_id_from_gateway_name_valid(self):
        self.assertEqual(config_helpers.get_id_from_gateway_name('GW1 - fgw14usb (Id: 1)'), 1)
        self.assertEqual(config_helpers.get_id_from_gateway_name('GW - x (Id: 87126)'), 87126)

    def test_get_id_from_gateway_name_malformed_returns_none(self):
        # no marker at all, marker without number, None input, non-integer id
        self.assertIsNone(config_helpers.get_id_from_gateway_name('Planung'))
        self.assertIsNone(config_helpers.get_id_from_gateway_name('Living room (kitchen)'))
        self.assertIsNone(config_helpers.get_id_from_gateway_name('GW (Id: )'))
        self.assertIsNone(config_helpers.get_id_from_gateway_name('GW (Id: 1.0)'))
        self.assertIsNone(config_helpers.get_id_from_gateway_name(None))

    # --- M9: get_device_config -------------------------------------------
    def test_get_device_config_without_gateway_section(self):
        # config lacking CONF_GATEWAY must return {} instead of raising KeyError
        self.assertEqual(config_helpers.get_device_config({}, 1), {})

    def test_get_device_config_found_and_missing(self):
        config = {CONF_GATEWAY: [{CONF_ID: 1, CONF_DEVICES: {"light": []}}]}
        self.assertEqual(config_helpers.get_device_config(config, 1), {"light": []})
        # gateway without devices key -> {}
        config2 = {CONF_GATEWAY: [{CONF_ID: 2}]}
        self.assertEqual(config_helpers.get_device_config(config2, 2), {})
        # unknown id -> {}
        self.assertEqual(config_helpers.get_device_config(config, 99), {})


if __name__ == "__main__":
    unittest.main()
