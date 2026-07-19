"""Regression tests for Phase 7 fixes (N3, N8).

N3 - every sensor description's device_class must be a valid SensorDeviceClass
     (or None); the old 'window'/'rain' strings and BATTERY+volt were invalid.
N8 - LAST_RECEIVED_TELEGRAMS must be per-instance, not a shared class dict.
"""
import unittest

from unittest import mock
from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform
from homeassistant.components.sensor import SensorDeviceClass

import custom_components.eltako.sensor as sensor_module
from custom_components.eltako.sensor import EltakoSensorEntityDescription
from custom_components.eltako.binary_sensor import EltakoBinarySensor
from tests.mocks import GatewayMock, HassMock
from eltakobus import RPSMessage
from eltakobus.util import AddressExpression
from eltakobus.eep import EEP

Entity.schedule_update_ha_state = mock.Mock(return_value=None)

_VALID_DEVICE_CLASSES = {dc.value for dc in SensorDeviceClass}


class TestPhase7Robustness(unittest.TestCase):

    # --- N3 ---------------------------------------------------------------
    def test_all_sensor_descriptions_have_valid_device_class(self):
        checked = 0
        for name in dir(sensor_module):
            if not name.startswith("SENSOR_DESC_"):
                continue
            desc = getattr(sensor_module, name)
            if not isinstance(desc, EltakoSensorEntityDescription):
                continue
            checked += 1
            dc = desc.device_class
            self.assertTrue(
                dc is None or dc in _VALID_DEVICE_CLASSES or dc in SensorDeviceClass,
                f"{name} has invalid device_class {dc!r}",
            )
        self.assertGreater(checked, 0, "no sensor descriptions were checked")

    # --- N8 ---------------------------------------------------------------
    def _make_bs(self, dev_id_str: str) -> EltakoBinarySensor:
        gateway = GatewayMock(dev_id=123)
        bs = EltakoBinarySensor(
            Platform.BINARY_SENSOR, gateway, AddressExpression.parse(dev_id_str),
            "n", EEP.find("F6-02-01"), "none", False, None,
        )
        bs.hass = HassMock()
        return bs

    def test_last_received_telegrams_is_per_instance(self):
        bs1 = self._make_bs("00-00-00-01")
        bs2 = self._make_bs("00-00-00-02")

        # not the same object -> no cross-talk between entities / gateways
        self.assertIsNot(bs1.LAST_RECEIVED_TELEGRAMS, bs2.LAST_RECEIVED_TELEGRAMS)

        # a push handled by bs1 must not populate bs2's store
        push = RPSMessage(b'\x00\x00\x00\x01', status=0x30, data=b'\x70')
        bs1.value_changed(push)
        self.assertTrue(bs1.LAST_RECEIVED_TELEGRAMS)          # bs1 recorded it
        self.assertEqual(bs2.LAST_RECEIVED_TELEGRAMS, {})     # bs2 untouched


if __name__ == "__main__":
    unittest.main()
