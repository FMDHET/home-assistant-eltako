"""Tests for the Eltako button entities (A6, roadmap wave A):
teach-in telegram payload, and the K2 executor contract for reconnect/read-memory."""
import asyncio
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform, EntityCategory

from tests.mocks import *
from custom_components.eltako.button import (
    TeachInButton, GatewayReconnectButton, GatewayReadAllDevicesButton, EEP_WITH_TEACH_IN_BUTTONS,
)
from eltakobus import AddressExpression
from eltakobus.eep import EEP, A5_38_08

Entity.schedule_update_ha_state = mock.Mock(return_value=None)


class TestButtons(unittest.TestCase):

    def test_teach_in_button_payload(self):
        gw = GatewayMock(dev_id=123)
        sender = AddressExpression.parse("00-00-B0-01")
        btn = TeachInButton(Platform.BUTTON, gw, AddressExpression.parse("00-00-00-01"),
                            "n", EEP.find("A5-38-08"), sender, EEP.find("A5-38-08"))
        sent = []
        btn.send_message = sent.append
        asyncio.run(btn.async_press())

        self.assertEqual(len(sent), 1)
        msg = sent[0]
        # data == the teach-in payload for A5-38-08, sent as outgoing 4BS with status 0x80
        self.assertEqual(msg.data, EEP_WITH_TEACH_IN_BUTTONS[A5_38_08])
        self.assertEqual(msg.address, b'\x00\x00\xB0\x01')

    def test_teach_in_button_is_config_category(self):
        gw = GatewayMock(dev_id=123)
        btn = TeachInButton(Platform.BUTTON, gw, AddressExpression.parse("00-00-00-01"),
                            "n", EEP.find("A5-38-08"), AddressExpression.parse("00-00-B0-01"), EEP.find("A5-38-08"))
        self.assertEqual(btn.entity_category, EntityCategory.CONFIG)   # A3

    def test_reconnect_button_runs_in_executor(self):
        gw = GatewayMock(dev_id=123)
        gw.reconnect = mock.Mock()
        btn = GatewayReconnectButton(Platform.BUTTON, gw)
        btn.hass = HassMock()       # async_add_executor_job runs the func synchronously
        asyncio.run(btn.async_press())
        gw.reconnect.assert_called_once_with()

    def test_read_all_devices_button(self):
        gw = GatewayMock(dev_id=123)
        called = []
        async def _read():
            called.append(True)
        gw.read_memory_of_all_bus_members = _read
        btn = GatewayReadAllDevicesButton(Platform.BUTTON, gw)
        btn.hass = HassMock()
        asyncio.run(btn.async_press())
        self.assertEqual(called, [True])


if __name__ == "__main__":
    unittest.main()
