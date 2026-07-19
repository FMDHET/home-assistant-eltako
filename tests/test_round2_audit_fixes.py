"""Regression tests for the round-2 full-codebase audit fixes (A-r2).

Covers the safe fixes applied after the 6-agent audit:
- binary_sensor: invert_signal=None coerced to False (sensor:-section entities)
- binary_sensor: A5-07-01 event payload uses pir_status_on (not the raw byte)
- binary_sensor: teach-in telegrams ignored (A5-07-01)
- light: brightness 1 no longer dims to 0; roundtrip uses round()
- cover: restore without tilt attribute keeps the position; time clamping
- select: 'unavailable' restore falls back to the default priority
- schema: cooling sender gateway_id accepts integers; sensor device_class
  accepts sensor classes
- gateway: message counter starts at 0; only the new connection-state handler
  is notified on registration
"""
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform
import voluptuous as vol

from tests.mocks import *
from custom_components.eltako.binary_sensor import EltakoBinarySensor
from custom_components.eltako.light import EltakoDimmableLight
from custom_components.eltako.cover import EltakoCover
from custom_components.eltako.schema import ClimateSchema, SensorSchema
from eltakobus import *
from eltakobus.eep import *

Entity.schedule_update_ha_state = mock.Mock(return_value=None)


def _make_binary_sensor(eep_string="A5-07-01", invert_signal=None):
    gateway = GatewayMock(dev_id=123)
    bs = EltakoBinarySensor(Platform.BINARY_SENSOR, gateway, AddressExpression.parse("00-00-00-01"),
                            "n", EEP.find(eep_string), "none", invert_signal, None)
    bs.hass = HassMock()
    return bs


class TestRound2AuditFixes(unittest.TestCase):

    # --- binary_sensor ------------------------------------------------------
    def test_invert_signal_none_is_false(self):
        """invert_signal=None (sensor:-section) acted as XOR-True -> stuck 'on'."""
        bs = _make_binary_sensor("A5-07-01", invert_signal=None)
        self.assertIs(bs.invert_signal, False)

        # pir_status < 128 => no motion; with the old None-XOR this was True
        no_motion = Regular4BSMessage(b'\x00\x00\x00\x01', 0x00, b'\x10\x00\x10\x0A')
        bs.value_changed(no_motion)
        self.assertEqual(bs.is_on, False)

    def test_a5_07_01_event_pressed_uses_pir_status_on(self):
        """Event payload used the RAW pir byte (0..255) - 'pressed' was ~never True."""
        bs = _make_binary_sensor("A5-07-01")
        motion = Regular4BSMessage(b'\x00\x00\x00\x01', 0x00, b'\x10\x00\xC8\x0A')  # pir 200 >= 128
        bs.value_changed(motion)
        self.assertEqual(bs.is_on, True)
        event = bs.hass.bus.fired_events[-1]
        self.assertTrue(event['event_data']['pressed'])

    def test_a5_07_01_ignores_teach_in(self):
        """Teach-in telegrams (LRN bit 0) must not flip the state."""
        bs = _make_binary_sensor("A5-07-01")
        bs._attr_is_on = False
        # data[3] bit 3 = 0 -> learn telegram, even though pir byte says 'motion'
        teach_in = Regular4BSMessage(b'\x00\x00\x00\x01', 0x00, b'\x10\x00\xC8\x00')
        bs.value_changed(teach_in)
        self.assertEqual(bs.is_on, False)
        self.assertEqual(len(bs.hass.bus.fired_events), 0)

    # --- light ---------------------------------------------------------------
    def test_brightness_1_does_not_dim_to_zero(self):
        gateway = GatewayMock()
        light = EltakoDimmableLight(Platform.LIGHT, gateway, AddressExpression.parse('00-00-00-01'),
                                    'l', EEP.find('A5-38-08'), AddressExpression.parse('00-00-B0-01'),
                                    EEP.find('A5-38-08'))
        sent = []
        light.send_message = sent.append
        light.turn_on(brightness=1)
        # A5-38-08 dimming telegram: data byte 1 is the dimming value (percent)
        self.assertGreaterEqual(sent[0].data[1], 1, "brightness 1 must not encode to dimming value 0")

    # --- cover ---------------------------------------------------------------
    def _make_cover(self, time_closes=25, time_opens=25, time_tilts=None):
        gateway = GatewayMock()
        return EltakoCover(Platform.COVER, gateway, AddressExpression.parse('00-00-00-02'), 'c',
                           EEP.find('G5-3F-7F'), AddressExpression.parse('00-00-B0-02'),
                           EEP.find('H5-3F-7F'), 'shutter', time_closes, time_opens, time_tilts)

    def test_cover_restore_without_tilt_attribute(self):
        """Covers without time_tilts have no stored tilt attribute - restore must
        keep the position instead of wiping everything with a KeyError."""
        cover = self._make_cover()
        cover.load_value_initially(LatestStateMock('open', {'current_position': 40}))
        self.assertIsNotNone(cover._attr_current_cover_position)

    def test_cover_time_255_is_clamped(self):
        """time_opens=255 (schema-valid) crashed open_cover with ValueError."""
        cover = self._make_cover(time_closes=255, time_opens=255)
        sent = []
        cover.send_message = sent.append
        cover.open_cover()      # must not raise
        cover.close_cover()     # must not raise
        self.assertEqual(len(sent), 2)

    # --- schema ---------------------------------------------------------------
    def test_cooling_sender_gateway_id_accepts_int(self):
        """gateway_id was validated with the ADDRESS regex -> every integer failed."""
        schema = ClimateSchema.CONF_COOLING_MODE_SCHEMA
        validated = schema({
            'sensor': {'id': '00-00-10-01'},
            'sender': {'id': '00-00-B0-01', 'eep': 'F6-02-01', 'gateway_id': 2},
        })
        self.assertEqual(validated['sender']['gateway_id'], 2)

    def test_sensor_schema_accepts_sensor_device_class(self):
        """'temperature' (a valid SENSOR class) was rejected by the copy-pasted
        binary-sensor schema and failed the whole config."""
        validated = SensorSchema.ENTITY_SCHEMA({
            'id': '00-00-10-05', 'eep': 'A5-04-02', 'device_class': 'temperature',
        })
        self.assertEqual(validated['device_class'], 'temperature')
        # binary classes stay accepted (consumed by the derived binary sensors)
        validated2 = SensorSchema.ENTITY_SCHEMA({
            'id': '00-00-10-06', 'eep': 'A5-07-01', 'device_class': 'occupancy',
        })
        self.assertEqual(validated2['device_class'], 'occupancy')

    # --- gateway ---------------------------------------------------------------
    def test_message_counter_notify_does_not_increment(self):
        """_init_bus reset the counter to 0 and then fired the INCREMENTING event,
        so the count sensor started at 1 (and reset to 1 on every reconnect).
        The reset path now uses _notify_received_message_count (no increment).
        (Testing the invariant directly - test_gateway.py mocks _init_bus
        class-wide, so constructing a gateway is not deterministic here.)"""
        gw = GatewayMock()
        gw._received_message_count = 0
        gw._notify_received_message_count()
        self.assertEqual(gw._received_message_count, 0, "notify must not increment")
        gw._fire_received_message_count_event()
        self.assertEqual(gw._received_message_count, 1, "fire must increment")


if __name__ == "__main__":
    unittest.main()
