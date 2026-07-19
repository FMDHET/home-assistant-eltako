"""Tests for the VOC air-quality sensor (B3/AS2 + AS3).

AS2: device_class/unit derived from the substance's unit - only the VOC total is a
ppb value (-> VOLATILE_ORGANIC_COMPOUNDS_PARTS); individual substances are a unitless
index (-> no device_class, so HA long-term statistics no longer reject them).
AS3: the unique_id is language-independent (name_en based), while the display name
stays localized - a HA language switch no longer orphans the entity."""
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import Platform

from tests.mocks import *
from custom_components.eltako.sensor import EltakoAirQualitySensor
from custom_components.eltako.const import LANGUAGE_ABBREVIATION
from eltakobus import AddressExpression
from eltakobus.eep import EEP, VOC_SubstancesType

Entity.schedule_update_ha_state = mock.Mock(return_value=None)

EN = LANGUAGE_ABBREVIATION.LANG_ENGLISH
DE = LANGUAGE_ABBREVIATION.LANG_GERMAN


def _voc(voc_type, language):
    gw = GatewayMock(dev_id=123)
    return EltakoAirQualitySensor(Platform.SENSOR, gw, AddressExpression.parse("00-00-00-01"),
                                  "aq", EEP.find("A5-09-0C"), voc_type, language)


class TestVocSensor(unittest.TestCase):

    # --- AS2: device_class / unit ---
    def test_total_uses_parts_device_class_and_ppb(self):
        s = _voc(VOC_SubstancesType.VOCT_TOTAL, EN)
        self.assertEqual(s.device_class, SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS)
        self.assertEqual(s.native_unit_of_measurement, "ppb")

    def test_substance_has_no_device_class_and_no_unit(self):
        # unitless index -> no device_class (a VOC class with an empty unit is rejected)
        s = _voc(VOC_SubstancesType.BENZENE, EN)
        self.assertIsNone(s.device_class)
        self.assertIsNone(s.native_unit_of_measurement)

    # --- AS3: language-independent id, localized name ---
    def test_unique_id_is_language_independent(self):
        en = _voc(VOC_SubstancesType.BENZENE, EN)
        de = _voc(VOC_SubstancesType.BENZENE, DE)
        self.assertEqual(en.unique_id, de.unique_id,
                         "same substance must have the same unique_id in any language")
        self.assertTrue(en.unique_id.endswith("air_quality_sensor_benzene"))

    def test_display_name_stays_localized(self):
        self.assertEqual(_voc(VOC_SubstancesType.BENZENE, EN).name, "Benzene")
        self.assertEqual(_voc(VOC_SubstancesType.BENZENE, DE).name, "Benzol")

    def test_english_id_unchanged_from_pre_fix_scheme(self):
        # AS3 keys off name_en, which is exactly what English installs already had ->
        # no migration/orphan for them. (German BENZENE previously had ..._benzol.)
        en = _voc(VOC_SubstancesType.BENZENE, EN)
        self.assertIn("air_quality_sensor_benzene", en.unique_id)
        self.assertNotIn("benzol", en.unique_id)

    def test_value_changed_routes_by_index(self):
        # AS2/AS3 must not affect telegram routing (matches on voc_type.index)
        s = _voc(VOC_SubstancesType.BENZENE, DE)
        self.assertEqual(s.voc_type.index, VOC_SubstancesType.BENZENE.index)


if __name__ == "__main__":
    unittest.main()
