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


class TestBinarySensor_A5_30_01(unittest.TestCase):

    def test_digital_input(self):
        bs = TestBinarySensor().create_binary_sensor(A5_30_01.eep_string, EntityDescription(key="0", name="Digital Input") )

        msg = Regular4BSMessage(b'\00\x00\x00\x01', 0x20, b'\00\x92\x00\x0E')
        bs.value_changed(msg)

        self.assertEqual(bs.is_on, True)

        msg = Regular4BSMessage(b'\00\x00\x00\x01', 0x20, b'\00\x92\xFF\x0E')
        bs.value_changed(msg)

        self.assertEqual(bs.is_on, False)


    def test_inverted_digital_input(self):
        bs = TestBinarySensor().create_binary_sensor(A5_30_01.eep_string, EntityDescription(key="0", name="Digital Input") )
        bs.invert_signal = True

        msg = Regular4BSMessage(b'\00\x00\x00\x01', 0x20, b'\00\x92\x00\x0E')
        bs.value_changed(msg)

        self.assertEqual(bs.is_on, False)

        msg = Regular4BSMessage(b'\00\x00\x00\x01', 0x20, b'\00\x92\xFF\x0E')
        bs.value_changed(msg)

        self.assertEqual(bs.is_on, True)
        

    def test_battery(self):
        bs = TestBinarySensor().create_binary_sensor(A5_30_01.eep_string, EntityDescription(key="low_battery", name="Low Battery") )

        msg = Regular4BSMessage(b'\00\x00\x00\x01', 0x20, b'\00\x92\xFF\x0E')
        bs.value_changed(msg)

        self.assertEqual(bs.is_on, False)

        # A-r2: was b'\FF\x92\x00\x0E' - '\F' is NOT an escape sequence, so the data
        # was 6 bytes and the assertion only passed by accident (data[1]=0x46 by
        # coincidence of the stray backslash bytes). Proper telegram: battery byte
        # 0x46 (=70 < 121 -> low battery), learn bit set in data[3].
        msg = Regular4BSMessage(b'\00\x00\x00\x01', 0x20, b'\x00\x46\x00\x0E')
        bs.value_changed(msg)

        self.assertEqual(bs.is_on, True)

    def test_r3_11_low_battery_not_inverted_and_battery_device_class(self):
        from homeassistant.components.binary_sensor import BinarySensorDeviceClass
        # description passed as keyword so description_key == "low_battery" (the low-battery entity)
        bs = TestBinarySensor().create_binary_sensor(
            A5_30_01.eep_string, description=EntityDescription(key="low_battery", name="Low Battery"))

        # R3-11: forced BATTERY device_class regardless of any configured device_class
        self.assertEqual(bs.device_class, BinarySensorDeviceClass.BATTERY)

        # the contact's invert_signal must NOT flip the battery alarm
        bs.invert_signal = True

        # battery OK (byte 0xFF = 255 >= threshold) -> low_battery False despite invert_signal=True
        bs.value_changed(Regular4BSMessage(b'\00\x00\x00\x01', 0x20, b'\x00\xFF\x00\x0E'))
        self.assertIs(bs.is_on, False)

        # battery low (byte 0x46 = 70 < 121) -> low_battery True
        bs.value_changed(Regular4BSMessage(b'\00\x00\x00\x01', 0x20, b'\x00\x46\x00\x0E'))
        self.assertIs(bs.is_on, True)