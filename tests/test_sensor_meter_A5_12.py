"""Tests for EltakoMeterSensor (A5-12) — especially the 0x8F serial-number guard
(N9/round-2 fix, previously untested). Data byte layout (A5-12 4BS):
data[0..2] = 24-bit reading, data[3] = channel<<4 | learn<<3 | data_type<<2 | divisor.
A serial-number telegram carries data[3] == 0x8F and must NOT be read as a value."""
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform

from tests.mocks import *
from custom_components.eltako.sensor import (
    EltakoMeterSensor, SENSOR_DESC_ELECTRICITY_CURRENT, SENSOR_DESC_GAS_CURRENT,
    SENSOR_DESC_ELECTRICITY_CUMULATIVE,
)
from eltakobus import AddressExpression
from eltakobus.eep import EEP
from eltakobus.message import Regular4BSMessage

Entity.schedule_update_ha_state = mock.Mock(return_value=None)


def _meter(eep, desc, tariff):
    gw = GatewayMock(dev_id=123)
    s = EltakoMeterSensor(Platform.SENSOR, gw, AddressExpression.parse("00-00-00-01"),
                          "m", EEP.find(eep), desc, tariff=tariff)
    s.hass = HassMock()
    return s


class TestMeterSensor(unittest.TestCase):

    def test_electricity_current_normal_reading(self):
        s = _meter("A5-12-01", SENSOR_DESC_ELECTRICITY_CURRENT, tariff=0)
        # reading 100, channel 0, learn=1, data_type=1 (current), divisor 0 -> data[3]=0x0C
        s.value_changed(Regular4BSMessage(b'\x00\x00\x00\x01', 0x00, bytes([0x00, 0x00, 0x64, 0x0C])))
        self.assertEqual(s.native_value, 100)

    def test_electricity_current_ignores_serial_number_telegram(self):
        s = _meter("A5-12-01", SENSOR_DESC_ELECTRICITY_CURRENT, tariff=0)
        # data[3] == 0x8F -> meter serial number, must be ignored (guard)
        s.value_changed(Regular4BSMessage(b'\x00\x00\x00\x01', 0x00, bytes([0x12, 0x34, 0x56, 0x8F])))
        self.assertIsNone(s.native_value)

    def test_gas_current_ignores_serial_number_on_matching_channel(self):
        # gas current sensor on tariff 8 (channel nibble 8) - the serial telegram's
        # channel matches, so only the 0x8F guard prevents a bogus flow reading.
        s = _meter("A5-12-02", SENSOR_DESC_GAS_CURRENT, tariff=8)
        s.value_changed(Regular4BSMessage(b'\x00\x00\x00\x01', 0x00, bytes([0x12, 0x34, 0x56, 0x8F])))
        self.assertIsNone(s.native_value)
        # a genuine channel-8 current reading (data[3]=0x8C) IS read (l/s -> m3/h *3.6)
        s.value_changed(Regular4BSMessage(b'\x00\x00\x00\x01', 0x00, bytes([0x00, 0x00, 0x0A, 0x8C])))
        self.assertEqual(s.native_value, round(10 * 3.6, 2))

    def test_learn_telegram_ignored(self):
        s = _meter("A5-12-01", SENSOR_DESC_ELECTRICITY_CURRENT, tariff=0)
        # learn bit 0 -> teach-in, must be ignored (data[3]=0x04: learn=0, data_type=1)
        s.value_changed(Regular4BSMessage(b'\x00\x00\x00\x01', 0x00, bytes([0x00, 0x00, 0x64, 0x04])))
        self.assertIsNone(s.native_value)


class TestMeterTariffUniqueId(unittest.TestCase):
    """AS1 (roadmap wave B): the tariff must be part of the meter unique_id, but the
    FIRST configured tariff keeps the pre-fix plain id so the existing entity/history
    is preserved (no registry migration); additional tariffs (previously dropped by a
    unique_id collision) come up as distinct new entities."""

    def _cumulative(self, tariff, tariff_in_id):
        gw = GatewayMock(dev_id=123)
        return EltakoMeterSensor(Platform.SENSOR, gw, AddressExpression.parse("00-00-00-01"),
                                 "m", EEP.find("A5-12-01"), SENSOR_DESC_ELECTRICITY_CUMULATIVE,
                                 tariff=tariff, tariff_in_id=tariff_in_id)

    def test_first_tariff_keeps_plain_unique_id(self):
        s = self._cumulative(tariff=0, tariff_in_id=False)
        self.assertTrue(s.unique_id.endswith("electricity_cumulative"),
                        f"first tariff must keep the plain id, got {s.unique_id}")
        self.assertNotIn("tariff", s.unique_id, "no suffix on the first/backward-compatible tariff")

    def test_additional_tariffs_get_distinct_unique_ids(self):
        first = self._cumulative(tariff=0, tariff_in_id=False)   # config tariff 1
        second = self._cumulative(tariff=1, tariff_in_id=True)   # config tariff 2
        third = self._cumulative(tariff=2, tariff_in_id=True)    # config tariff 3
        self.assertEqual(len({first.unique_id, second.unique_id, third.unique_id}), 3,
                         "each tariff must have a distinct unique_id")
        self.assertTrue(second.unique_id.endswith("electricity_cumulative_tariff_2"))
        self.assertTrue(third.unique_id.endswith("electricity_cumulative_tariff_3"))

    def test_entity_id_tracks_unique_id_per_tariff(self):
        # entity_id derives from unique_id, so distinct ids -> distinct entities
        first = self._cumulative(tariff=0, tariff_in_id=False)
        second = self._cumulative(tariff=1, tariff_in_id=True)
        self.assertNotEqual(first.entity_id, second.entity_id)

    def test_name_still_carries_tariff(self):
        s = self._cumulative(tariff=1, tariff_in_id=True)
        self.assertIn("Tariff 2", s.name)

    def test_duplicate_tariffs_are_deduped_by_schema(self):
        # AS1: the per-tariff suffix is value-based, so a duplicate value would recreate
        # a collision; the schema dedupes (order-preserving) instead of rejecting.
        from custom_components.eltako.schema import SensorSchema
        from custom_components.eltako.const import CONF_EEP, CONF_METER_TARIFFS
        from homeassistant.const import CONF_ID
        validated = SensorSchema.ENTITY_SCHEMA(
            {CONF_ID: "00-00-00-01", CONF_EEP: "A5-12-01", CONF_METER_TARIFFS: [1, 2, 2, 1]})
        self.assertEqual(validated[CONF_METER_TARIFFS], [1, 2],
                         "duplicate tariff values must be deduped, order preserved")


if __name__ == "__main__":
    unittest.main()
