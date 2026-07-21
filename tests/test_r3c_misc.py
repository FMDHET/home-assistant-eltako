"""R3-C small/hygiene fixes: R3-26 (stdlib StrEnum), R3-24 (@callback), R3-25 (no shared
mutable settings), R3-15 (send_message publishes to the global bus only once)."""
import enum
import unittest
from unittest import mock

from homeassistant.core import is_callback

from tests.mocks import *
from custom_components.eltako.gateway import ELTAKO_GLOBAL_EVENT_BUS_ID
from custom_components.eltako.config_helpers import (
    DEFAULT_GENERAL_SETTINGS, CONF_FAST_STATUS_CHANGE, CONF_ENABLE_TEACH_IN_BUTTONS,
    get_general_settings_from_configuration,
)
from eltakobus.message import RPSMessage


class TestR3CMisc(unittest.TestCase):

    def test_r3_26_strenum_is_stdlib(self):
        from custom_components.eltako.const import StrEnum
        self.assertIs(StrEnum, enum.StrEnum)

    def test_r3_24_send_to_bus_callback_is_hass_callback(self):
        # @callback -> HA runs it inline on the loop in dispatch order (no executor reordering)
        gw = GatewayMock()
        self.assertTrue(is_callback(gw._callback_send_message_to_serial_bus))

    def test_r3_25_gateway_mocks_do_not_share_settings(self):
        before = DEFAULT_GENERAL_SETTINGS.get(CONF_FAST_STATUS_CHANGE)
        gw1 = GatewayMock()
        gw2 = GatewayMock()
        gw1.general_settings[CONF_FAST_STATUS_CHANGE] = True
        # each mock has its OWN settings copy; mutation must not leak to another mock ...
        self.assertIsNot(gw1.general_settings, gw2.general_settings)
        self.assertNotEqual(gw2.general_settings.get(CONF_FAST_STATUS_CHANGE), True)
        # ... nor to the shared module-level default
        self.assertEqual(DEFAULT_GENERAL_SETTINGS.get(CONF_FAST_STATUS_CHANGE), before)

    def test_r3_25_get_general_settings_returns_a_copy(self):
        # R3-25 (product code): get_general_settings_from_configuration must return a COPY so a
        # downstream in-place mutation (eltako_integration_init sets enable_teach_in_buttons)
        # cannot leak into the shared module-level default. The default path (hass=None) is the
        # common case (no `general_settings:` block in YAML).
        before = DEFAULT_GENERAL_SETTINGS.get(CONF_ENABLE_TEACH_IN_BUTTONS)
        settings = get_general_settings_from_configuration(None)
        self.assertIsNot(settings, DEFAULT_GENERAL_SETTINGS)
        settings[CONF_ENABLE_TEACH_IN_BUTTONS] = "MUTATED-BY-CALLER"
        self.assertEqual(DEFAULT_GENERAL_SETTINGS.get(CONF_ENABLE_TEACH_IN_BUTTONS), before)

    def test_r3_15_send_message_publishes_global_only_once(self):
        # send_message must dispatch ONLY the per-gateway send event; the single global-bus
        # publish happens centrally in _callback_send_message_to_serial_bus. Previously it
        # ALSO published to the global bus here -> service telegrams were dispatched twice.
        gw = GatewayMock()
        msg = RPSMessage(address=b'\x00\x00\x00\x01', status=b'\x30', data=b'\x70', outgoing=True)
        with mock.patch('custom_components.eltako.gateway.dispatcher_send') as ds:
            gw.send_message(msg)
        self.assertEqual(ds.call_count, 1)
        dispatched_event_id = ds.call_args[0][1]
        self.assertNotEqual(dispatched_event_id, ELTAKO_GLOBAL_EVENT_BUS_ID)


if __name__ == "__main__":
    unittest.main()
