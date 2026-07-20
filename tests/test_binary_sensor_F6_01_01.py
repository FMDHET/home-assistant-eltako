"""R3-09: F6-01-01 single-button sensors must actually fire their button event.

The F6-01-01 branch used to `return` before the shared fire block, so hass.bus.fire and
the push/release duration bookkeeping never ran - even though sensor.py creates an
"Event Id" field for F6-01-01. It now falls through like the other wall-switch EEPs."""
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity

from tests.mocks import *
from eltakobus import *
from eltakobus.message import RPSMessage

from tests.test_binary_sensor_generic import TestBinarySensor

# mock update of Home Assistant
Entity.schedule_update_ha_state = mock.Mock(return_value=None)

PUSH = b'\x10'      # F6-01-01: data[0] == 0x10 -> button pushed
RELEASE = b'\x00'


class TestBinarySensor_F6_01_01(unittest.TestCase):

    def _sensor(self):
        return TestBinarySensor().create_binary_sensor("F6-01-01")

    def test_push_and_release_each_fire_an_event(self):
        bs = self._sensor()

        bs.value_changed(RPSMessage(b'\x00\x00\x00\x01', b'\x30', PUSH, outgoing=False))
        self.assertTrue(bs.is_on)

        bs.value_changed(RPSMessage(b'\x00\x00\x00\x01', b'\x30', RELEASE, outgoing=False))
        self.assertFalse(bs.is_on)

        # R3-09: both telegrams fire an event (previously: zero events fired).
        self.assertEqual(len(bs.hass.bus.fired_events), 2,
                         "F6-01-01 push/release did not fire the expected two events")

    def test_release_event_carries_push_duration(self):
        bs = self._sensor()
        bs.value_changed(RPSMessage(b'\x00\x00\x00\x01', b'\x30', PUSH, outgoing=False))
        bs.value_changed(RPSMessage(b'\x00\x00\x00\x01', b'\x30', RELEASE, outgoing=False))

        release_event = bs.hass.bus.fired_events[-1]['event_data']
        self.assertIn('push_duration_in_sec', release_event)
        self.assertGreaterEqual(release_event['push_duration_in_sec'], 0)


if __name__ == "__main__":
    unittest.main()
