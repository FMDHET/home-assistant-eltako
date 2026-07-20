"""R3-03: the gateway 'Received Messages per Session' diagnostic sensor.

It declared suggested_unit_of_measurement="Messages" (with no device_class),
which makes SensorEntity._is_valid_suggested_unit raise ValueError - so the
entity was never added (silently missing on every install). A plain counter
needs no unit; only state_class + icon remain."""
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform

from tests.mocks import *
from custom_components.eltako.sensor import *

# mock update of Home Assistant
Entity.schedule_update_ha_state = mock.Mock(return_value=None)


class TestGatewayReceivedMessagesSensor(unittest.TestCase):

    def _mk(self):
        gw = GatewayMock(dev_id=123)
        return GatewayReceivedMessagesInActiveSession(Platform.SENSOR, gw)

    def test_unit_of_measurement_does_not_raise(self):
        s = self._mk()
        # Before the fix this access ran SensorEntity._is_valid_suggested_unit and
        # raised ValueError ("... suggest an incorrect unit of measurement: Messages").
        self.assertIsNone(s.unit_of_measurement)
        self.assertIsNone(s.entity_description.suggested_unit_of_measurement)
        self.assertIsNone(s.entity_description.native_unit_of_measurement)

    def test_counter_still_works(self):
        s = self._mk()
        self.assertEqual(s.state_class, SensorStateClass.TOTAL_INCREASING)
        s.value_changed(7)
        self.assertEqual(s.native_value, 7)


if __name__ == "__main__":
    unittest.main()
