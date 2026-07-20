import unittest
from tests.mocks import *
from unittest import mock
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.const import Platform
from custom_components.eltako.binary_sensor import EltakoBinarySensor
from custom_components.eltako.config_helpers import *
from eltakobus import *
from eltakobus.eep import *

from tests.test_binary_sensor_generic import TestBinarySensor

# mock update of Home Assistant
Entity.schedule_update_ha_state = mock.Mock(return_value=None)
# EltakoBinarySensor.hass.bus.fire is mocked by class HassMock


class TestBinarySensor_A5_30_03(unittest.TestCase):

    def test_digital_input(self):

        for key in ["0", "1", "2", "3", "wake"]:
            bs = TestBinarySensor().create_binary_sensor(A5_30_03.eep_string, description= EntityDescription(key=key, name=key))

            msg = Regular4BSMessage(b'\00\x00\x00\x01', 0x20, b'\00\x00\x1F\x08')
            bs.value_changed(msg)

            self.assertEqual(bs.is_on, True)

            msg = Regular4BSMessage(b'\00\x00\x00\x01', 0x20, b'\00\x00\x00\x08')
            bs.value_changed(msg)

            self.assertEqual(bs.is_on, False)


    def test_inverted_digital_input(self):
        # R3-11: invert_signal applies to the contact-like digital inputs (0..3), NOT to
        # the "Status of Wake" status flag (covered separately below).
        for key in ["0", "1", "2", "3"]:
            bs = TestBinarySensor().create_binary_sensor(A5_30_03.eep_string, description= EntityDescription(key=key, name=key))
            bs.invert_signal = True

            msg = Regular4BSMessage(b'\00\x00\x00\x01', 0x20, b'\00\x00\x1F\x08')
            bs.value_changed(msg)

            self.assertEqual(bs.is_on, False)

            msg = Regular4BSMessage(b'\00\x00\x00\x01', 0x20, b'\00\x00\x00\x08')
            bs.value_changed(msg)

            self.assertEqual(bs.is_on, True)

    def test_wake_ignores_invert_signal(self):
        # R3-11: "Status of Wake" is a status flag, not a contact -> invert_signal must not flip it.
        bs = TestBinarySensor().create_binary_sensor(A5_30_03.eep_string, description=EntityDescription(key="wake", name="wake"))
        bs.invert_signal = True

        bs.value_changed(Regular4BSMessage(b'\00\x00\x00\x01', 0x20, b'\00\x00\x1F\x08'))
        self.assertIs(bs.is_on, True)    # not inverted despite invert_signal=True

        bs.value_changed(Regular4BSMessage(b'\00\x00\x00\x01', 0x20, b'\00\x00\x00\x08'))
        self.assertIs(bs.is_on, False)
        