"""R3-07: A5 sensors must ignore 4BS LRN teach-in telegrams (EnOcean DB0.3 == 0).

A teach-in decoded as data publishes garbage (e.g. -15.8 C / 6 %) that spikes long-term
statistics and can trip automations. Covers EEPs that expose learn_button (A5-04-02,
A5-07-01) and ones that do NOT (A5-06-01, A5-10-06) - the guard reads the raw LRN bit so it
works uniformly. Without the guard a teach-in telegram would set native_value to a (bogus)
number instead of leaving it None."""
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform

from tests.mocks import *
from custom_components.eltako.sensor import (
    EltakoTemperatureSensor, EltakoHumiditySensor, EltakoIlluminationSensor,
    EltakoPirSensor, EltakoTargetTemperatureSensor, EltakoTwilightSensor,
    EltakoDaylightSensor,
)
from eltakobus import AddressExpression
from eltakobus.eep import EEP
from eltakobus.message import Regular4BSMessage

# mock update of Home Assistant
Entity.schedule_update_ha_state = mock.Mock(return_value=None)

ADR = b'\x05\x00\x00\x01'
LRN_TEACH_IN = 0x00   # DB0.3 clear
LRN_DATA = 0x08       # DB0.3 set


class TestTeachInGuard(unittest.TestCase):

    def _mk(self, cls, eep):
        return cls(Platform.SENSOR, GatewayMock(dev_id=123), AddressExpression.parse("05-00-00-01"), "s", EEP.find(eep))

    def _assert_guarded(self, sensor, payload3):
        """payload3 = data[0:3]; data[3] carries the LRN bit."""
        sensor.value_changed(Regular4BSMessage(ADR, 0x00, bytes(payload3 + [LRN_TEACH_IN])))
        self.assertIsNone(sensor.native_value,
                          f"{type(sensor).__name__} accepted a teach-in telegram (should be ignored)")
        sensor.value_changed(Regular4BSMessage(ADR, 0x00, bytes(payload3 + [LRN_DATA])))
        self.assertIsNotNone(sensor.native_value,
                             f"{type(sensor).__name__} dropped a valid data telegram")

    def test_temperature_a5_04_02(self):
        self._assert_guarded(self._mk(EltakoTemperatureSensor, "A5-04-02"), [0x00, 0x50, 0x50])

    def test_humidity_a5_04_02(self):
        self._assert_guarded(self._mk(EltakoHumiditySensor, "A5-04-02"), [0x50, 0x00, 0x00])

    def test_pir_a5_07_01(self):
        self._assert_guarded(self._mk(EltakoPirSensor, "A5-07-01"), [0x00, 0x00, 0xFF])

    def test_illumination_a5_06_01(self):
        self._assert_guarded(self._mk(EltakoIlluminationSensor, "A5-06-01"), [0x00, 0x00, 0x50])

    def test_twilight_a5_06_01(self):
        self._assert_guarded(self._mk(EltakoTwilightSensor, "A5-06-01"), [0x00, 0x00, 0x50])

    def test_daylight_a5_06_01(self):
        self._assert_guarded(self._mk(EltakoDaylightSensor, "A5-06-01"), [0x00, 0x00, 0x50])

    def test_target_temperature_a5_10_06(self):
        self._assert_guarded(self._mk(EltakoTargetTemperatureSensor, "A5-10-06"), [0x40, 0x40, 0x40])


if __name__ == "__main__":
    unittest.main()
