"""R3-12 / R3-21 (sender-id validation) and R3-13 (RORG prefilter for switches)."""
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform

from tests.mocks import *
from custom_components.eltako.switch import EltakoSwitch
from custom_components.eltako.sensor import EltakoTemperatureSensor
from eltakobus import *
from eltakobus.eep import EEP
from eltakobus.message import Regular4BSMessage, RPSMessage

# mock update of Home Assistant
Entity.schedule_update_ha_state = mock.Mock(return_value=None)


def _switch(sender="00-00-B0-01"):
    gw = GatewayMock()
    return EltakoSwitch(Platform.SWITCH, gw, AddressExpression.parse("00-00-00-01"),
                        "n", EEP.find("M5-38-08"), AddressExpression.parse(sender), EEP.find("A5-38-08"))


class TestSenderIdValidation(unittest.TestCase):
    """R3-12: validate_sender_id resolves the actuator's private _sender_id (was a no-op)."""

    def test_actuator_sender_id_is_resolved_and_validated(self):
        sender = AddressExpression.parse("00-00-B0-01")
        sw = _switch()
        sw.gateway.validate_sender_id = mock.Mock(return_value=True)

        self.assertTrue(sw.validate_sender_id())
        sw.gateway.validate_sender_id.assert_called_once()
        # the resolved sender passed to the gateway is the actuator's _sender_id
        self.assertEqual(sw.gateway.validate_sender_id.call_args[0][0], sender)

    def test_sensor_without_sender_is_silent_true(self):
        # R3-12 side-effect preservation / R3-21: a sensor has no sender -> returns True
        # WITHOUT calling the gateway (so removing the sensor-platform validation call is safe).
        gw = GatewayMock()
        s = EltakoTemperatureSensor(Platform.SENSOR, gw, AddressExpression.parse("05-00-00-01"), "s", EEP.find("A5-04-02"))
        gw.validate_sender_id = mock.Mock()

        self.assertTrue(s.validate_sender_id())
        gw.validate_sender_id.assert_not_called()


class TestSwitchRorgFilter(unittest.TestCase):
    """R3-13: a co-located 4BS meter telegram (org 0x07) must be ignored silently."""

    def test_switch_ignores_4bs_meter_without_warning(self):
        sw = _switch()
        sw._attr_is_on = True
        meter = Regular4BSMessage(b'\x00\x00\x00\x01', 0x00, b'\x00\x00\x00\x08')   # org 0x07 (4BS)

        with self.assertNoLogs("eltako", level="WARNING"):
            sw.value_changed(meter)
        self.assertTrue(sw._attr_is_on, "state must be unchanged by the ignored telegram")

    def test_switch_still_processes_rps(self):
        sw = _switch()
        sw._attr_is_on = None
        on_msg = RPSMessage(address=b'\x00\x00\x00\x01', status=b'\x30', data=b'\x70', outgoing=False)
        sw.value_changed(on_msg)
        self.assertIsNotNone(sw._attr_is_on, "a valid RPS telegram must still be processed")


if __name__ == "__main__":
    unittest.main()
