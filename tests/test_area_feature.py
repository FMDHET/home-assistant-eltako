"""Regression tests for the F1 'area' feature and F9 temperature_unit default
(ported from the version2 branch during V1 branch consolidation)."""
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform
import voluptuous as vol

from tests.mocks import *
from custom_components.eltako.binary_sensor import EltakoBinarySensor
from custom_components.eltako.device import apply_area_to_entities
from custom_components.eltako.config_helpers import DeviceConf
from custom_components.eltako.schema import ClimateSchema, SensorSchema, LightSchema
from custom_components.eltako.const import CONF_AREA
from eltakobus import AddressExpression
from eltakobus.eep import EEP

Entity.schedule_update_ha_state = mock.Mock(return_value=None)


class TestAreaFeature(unittest.TestCase):

    def _bs(self):
        gw = GatewayMock(dev_id=123)
        bs = EltakoBinarySensor(Platform.BINARY_SENSOR, gw, AddressExpression.parse("00-00-00-01"),
                                "n", EEP.find("F6-02-01"), "none", False, None)
        bs.hass = HassMock()
        return bs

    # --- F1: DeviceConf parses area -------------------------------------
    def test_deviceconf_parses_area(self):
        conf = DeviceConf({"id": "00-00-00-01", "eep": "F6-02-01", "area": "Living room"})
        self.assertEqual(conf.area, "Living room")
        # absent -> None
        conf2 = DeviceConf({"id": "00-00-00-02", "eep": "F6-02-01"})
        self.assertIsNone(conf2.area)

    # --- F1: apply_area_to_entities sets _attr_dev_area on the slice -----
    def test_apply_area_sets_dev_area(self):
        e1, e2 = self._bs(), self._bs()
        entities = [e1]
        start = len(entities)          # 1 -> e2 is the "new" one
        entities.append(e2)
        conf = DeviceConf({"id": "00-00-00-02", "eep": "F6-02-01", "area": "Kitchen"})
        apply_area_to_entities(entities, start, conf)
        self.assertIsNone(e1._attr_dev_area)     # pre-existing entity untouched
        self.assertEqual(e2._attr_dev_area, "Kitchen")

    def test_apply_area_noop_without_area(self):
        e = self._bs()
        apply_area_to_entities([e], 0, DeviceConf({"id": "00-00-00-01", "eep": "F6-02-01"}))
        self.assertIsNone(e._attr_dev_area)

    # --- F1: device_info does NOT carry the deprecated suggested_area key
    def test_device_info_has_no_suggested_area(self):
        # suggested_area is deprecated (removed in HA 2026.9); area is handled
        # by _assign_area_if_unset instead.
        e = self._bs()
        e._attr_dev_area = "Bath"
        self.assertNotIn("suggested_area", e.device_info)

    # --- F1: _assign_area_if_unset is non-destructive -------------------
    def test_assign_area_only_when_unset(self):
        e = self._bs()
        with mock.patch("custom_components.eltako.device.ar") as ar_mock, \
             mock.patch("custom_components.eltako.device.dr") as dr_mock:
            area = mock.Mock(id="area_1")
            ar_mock.async_get.return_value.async_get_area_by_name.return_value = area
            device_reg = dr_mock.async_get.return_value

            # (a) device has NO area yet -> assigned
            device_reg.async_get_device.return_value = mock.Mock(id="dev_1", area_id=None)
            e._assign_area_if_unset("Kitchen")
            device_reg.async_update_device.assert_called_once_with("dev_1", area_id="area_1")

            # (b) device already has an area (user moved it) -> NOT touched
            device_reg.async_update_device.reset_mock()
            device_reg.async_get_device.return_value = mock.Mock(id="dev_1", area_id="user_area")
            e._assign_area_if_unset("Kitchen")
            device_reg.async_update_device.assert_not_called()

    def test_assign_area_creates_missing_area(self):
        e = self._bs()
        with mock.patch("custom_components.eltako.device.ar") as ar_mock, \
             mock.patch("custom_components.eltako.device.dr") as dr_mock:
            area_reg = ar_mock.async_get.return_value
            area_reg.async_get_area_by_name.return_value = None      # area does not exist
            area_reg.async_create.return_value = mock.Mock(id="new_area")
            dr_mock.async_get.return_value.async_get_device.return_value = mock.Mock(id="d", area_id=None)
            e._assign_area_if_unset("Basement")
            area_reg.async_create.assert_called_once_with("Basement")

    # --- F1: schema accepts area on every device platform ---------------
    def test_schema_accepts_area(self):
        light = LightSchema.ENTITY_SCHEMA({"id": "00-00-00-01", "eep": "A5-38-08",
                                           "sender": {"id": "00-00-B0-01", "eep": "A5-38-08"},
                                           "area": "Office"})
        self.assertEqual(light[CONF_AREA], "Office")
        sens = SensorSchema.ENTITY_SCHEMA({"id": "00-00-00-02", "eep": "A5-04-02", "area": "Hall"})
        self.assertEqual(sens[CONF_AREA], "Hall")

    # --- F9: climate temperature_unit is optional, defaults to °C -------
    def test_climate_temperature_unit_optional_default(self):
        cfg = ClimateSchema.ENTITY_SCHEMA({
            "id": "00-00-00-05", "eep": "A5-10-06",
            "sender": {"id": "00-00-B0-05", "eep": "A5-10-06"},
        })
        self.assertEqual(cfg["temperature_unit"], "°C")


if __name__ == "__main__":
    unittest.main()
