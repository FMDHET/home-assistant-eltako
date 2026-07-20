"""R3-20: a device listed under BOTH `binary_sensor:` and `sensor:` must not create two
binary-sensor entities with the same unique_id (HA rejects the duplicate). The binary_sensor
platform dedupes by unique_id, keeping the first occurrence."""
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform

from tests.mocks import *
from custom_components.eltako.binary_sensor import EltakoBinarySensor, dedupe_entities_by_unique_id
from eltakobus import *
from eltakobus.eep import *

# mock update of Home Assistant
Entity.schedule_update_ha_state = mock.Mock(return_value=None)


class TestDualListingDedupe(unittest.TestCase):

    def _bs(self, addr):
        gw = GatewayMock(dev_id=123)
        return EltakoBinarySensor(Platform.BINARY_SENSOR, gw, AddressExpression.parse(addr),
                                  "n", EEP.find("F6-10-00"), "none", False)

    def test_duplicate_unique_id_is_dropped_keeping_first(self):
        first = self._bs("00-00-00-01")     # same device listed under binary_sensor:
        dup = self._bs("00-00-00-01")       # ... and again under sensor:
        other = self._bs("00-00-00-02")
        self.assertEqual(first.unique_id, dup.unique_id)   # precondition: they collide

        result = dedupe_entities_by_unique_id([first, dup, other], Platform.BINARY_SENSOR)

        self.assertEqual(len(result), 2)
        self.assertIs(result[0], first)        # first occurrence kept
        self.assertNotIn(dup, result)          # duplicate dropped
        self.assertIn(other, result)

    def test_no_duplicates_is_unchanged(self):
        a = self._bs("00-00-00-01")
        b = self._bs("00-00-00-02")
        result = dedupe_entities_by_unique_id([a, b], Platform.BINARY_SENSOR)
        self.assertEqual(result, [a, b])


if __name__ == "__main__":
    unittest.main()
