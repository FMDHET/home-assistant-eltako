"""R3-10: EltakoSensor restores the UNIT-INDEPENDENT native value (RestoreSensor) instead of
parsing the display-converted state string (which double-converts on imperial installs)."""
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform

from tests.mocks import *
from custom_components.eltako.device import EltakoEntity
from custom_components.eltako.sensor import EltakoTemperatureSensor, RestoreSensor
from eltakobus import AddressExpression
from eltakobus.eep import EEP

# mock update of Home Assistant
Entity.schedule_update_ha_state = mock.Mock(return_value=None)


class TestR3CSensorRestore(unittest.IsolatedAsyncioTestCase):

    def _sensor(self):
        gw = GatewayMock(dev_id=123)
        return EltakoTemperatureSensor(Platform.SENSOR, gw, AddressExpression.parse("05-00-00-01"), "s", EEP.find("A5-04-02"))

    def test_eltako_sensor_is_restore_sensor(self):
        self.assertTrue(issubclass(EltakoTemperatureSensor, RestoreSensor))

    async def test_prefers_native_value(self):
        s = self._sensor()
        s._attr_native_value = None
        last = mock.Mock()
        last.native_value = 22.9   # native (metric) value, not a converted display string
        s.async_get_last_sensor_data = mock.AsyncMock(return_value=last)
        # stub the base (EltakoEntity) so we isolate the native-restore preference
        with mock.patch.object(EltakoEntity, 'async_added_to_hass', new=mock.AsyncMock()):
            await s.async_added_to_hass()
        self.assertEqual(s._attr_native_value, 22.9)

    async def test_falls_back_to_string_path_when_no_native_data(self):
        s = self._sensor()
        s._attr_native_value = None
        s.async_get_last_sensor_data = mock.AsyncMock(return_value=None)
        with mock.patch.object(EltakoEntity, 'async_added_to_hass', new=mock.AsyncMock()) as base:
            await s.async_added_to_hass()
        # native stays None -> the base restore (string parser) remains the fallback, and the
        # base async_added_to_hass is still invoked
        self.assertIsNone(s._attr_native_value)
        base.assert_awaited()


if __name__ == "__main__":
    unittest.main()
