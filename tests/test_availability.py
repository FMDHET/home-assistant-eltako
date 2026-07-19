"""Tests for B1 (roadmap wave B): entity availability follows the gateway
connection state.

Real device entities go "unavailable" when the gateway loses its bus/TCP
connection; gateway-owned diagnostic/config entities opt out and stay visible
precisely WHILE disconnected (connection-state sensor, reconnect button,
gateway info fields).

The connection-state handler intentionally reconciles to the gateway's LIVE
state (gateway.is_connected -> bus.is_active()) rather than trusting the notified
argument, so out-of-order delivery of the register-time immediate-notify vs. a
real connection-change event cannot strand availability at a stale value. The
tests therefore drive the connection by toggling the mock bus's is_active flag."""
import asyncio
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.const import Platform

from tests.mocks import *
from custom_components.eltako.binary_sensor import EltakoBinarySensor, GatewayConnectionState
from custom_components.eltako.button import (
    GatewayReconnectButton, GatewayReadAllDevicesButton, TeachInButton,
)
from custom_components.eltako.sensor import (
    GatewayLastReceivedMessage, GatewayInfoField, StaticInfoField, EventListenerInfoField,
)
from eltakobus import AddressExpression
from eltakobus.eep import EEP

Entity.schedule_update_ha_state = mock.Mock(return_value=None)


def _mk_gw(connected=True):
    gw = GatewayMock(dev_id=123)
    gw._bus._is_active = connected     # bus.is_active() is the source of truth
    return gw


def _device_entity(gw):
    """A plain device entity (follows gateway availability by default)."""
    e = EltakoBinarySensor(Platform.BINARY_SENSOR, gw, AddressExpression.parse("00-00-00-01"),
                           "s", EEP.find("F6-02-01"), None, False)
    # write goes through a per-instance mock so the handler never touches real HA state
    e.async_write_ha_state = mock.Mock()
    return e


class TestAvailability(unittest.TestCase):

    # --- core contract: device entities follow the connection state -----------

    def test_device_entity_available_by_default(self):
        e = _device_entity(_mk_gw(connected=True))
        self.assertTrue(e._attr_follow_gateway_availability)
        self.assertTrue(e.available)

    def test_disconnect_then_reconnect_toggles_availability(self):
        gw = _mk_gw(connected=True)
        e = _device_entity(gw)
        gw._bus._is_active = False
        asyncio.run(e._on_gateway_connection_state(False))
        self.assertFalse(e.available, "device entity must be unavailable while gateway is disconnected")
        gw._bus._is_active = True
        asyncio.run(e._on_gateway_connection_state(True))
        self.assertTrue(e.available, "device entity must recover when the gateway reconnects")

    def test_reconciles_to_live_bus_state_not_notified_value(self):
        """Guards the out-of-order race (finding #2): the handler must reflect the
        LIVE bus state, ignoring a stale notified argument."""
        gw = _mk_gw(connected=True)
        e = _device_entity(gw)
        # a stale 'disconnected' notification arrives, but the bus is actually up:
        asyncio.run(e._on_gateway_connection_state(False))
        self.assertTrue(e.available, "must reflect the live bus state, not the stale arg")
        # a stale 'connected' notification arrives, but the bus is actually down:
        gw._bus._is_active = False
        asyncio.run(e._on_gateway_connection_state(True))
        self.assertFalse(e.available)

    def test_state_write_only_on_actual_change(self):
        gw = _mk_gw(connected=True)
        e = _device_entity(gw)
        asyncio.run(e._on_gateway_connection_state(True))    # no change -> no write
        self.assertEqual(e.async_write_ha_state.call_count, 0)
        gw._bus._is_active = False
        asyncio.run(e._on_gateway_connection_state(False))   # change -> one write
        self.assertEqual(e.async_write_ha_state.call_count, 1)
        asyncio.run(e._on_gateway_connection_state(False))   # still no change -> no extra write
        self.assertEqual(e.async_write_ha_state.call_count, 1)

    def test_teach_in_button_follows_availability(self):
        """A device-representing config entity (teach-in) follows the gateway:
        teaching in requires an active connection."""
        gw = _mk_gw(connected=True)
        btn = TeachInButton(Platform.BUTTON, gw, AddressExpression.parse("00-00-00-01"),
                            "n", EEP.find("A5-38-08"), AddressExpression.parse("00-00-B0-01"), EEP.find("A5-38-08"))
        btn.async_write_ha_state = mock.Mock()
        self.assertTrue(btn._attr_follow_gateway_availability)
        gw._bus._is_active = False
        asyncio.run(btn._on_gateway_connection_state(False))
        self.assertFalse(btn.available)

    def test_device_diagnostic_info_fields_follow_availability(self):
        """Semantics fix: StaticInfoField ("Id"/"Event Id") and EventListenerInfoField
        ("Pushed Buttons") are per-DEVICE diagnostics, so they must FOLLOW the gateway -
        NOT opt out like the gateway-owned GatewayInfoField subclass."""
        gw = _mk_gw(connected=True)
        idf = StaticInfoField(Platform.SENSOR, gw, AddressExpression.parse("00-00-00-01"),
                              "n", EEP.find("F6-02-01"), "Id", "00-00-00-01", "mdi:identifier")
        elf = EventListenerInfoField(Platform.SENSOR, gw, AddressExpression.parse("00-00-00-01"),
                                     "n", EEP.find("F6-02-01"), "evt", "Pushed Buttons", lambda e: "", "mdi:x")
        gw._bus._is_active = False
        for e in (idf, elf):
            self.assertTrue(e._attr_follow_gateway_availability,
                            f"{type(e).__name__} is a per-device diagnostic and must follow availability")
            e.async_write_ha_state = mock.Mock()
            asyncio.run(e._on_gateway_connection_state(False))
            self.assertFalse(e.available, f"{type(e).__name__} must go unavailable when the gateway drops")

    # --- opt-out: gateway-owned entities stay visible while disconnected ------

    def test_gateway_owned_entities_stay_available_when_disconnected(self):
        gw = _mk_gw(connected=True)
        owned = [
            GatewayConnectionState(Platform.BINARY_SENSOR, gw),
            GatewayReconnectButton(Platform.BUTTON, gw),
            GatewayReadAllDevicesButton(Platform.BUTTON, gw),
            GatewayLastReceivedMessage(Platform.SENSOR, gw),
            GatewayInfoField(Platform.SENSOR, gw, "Id", "123", "mdi:identifier"),
        ]
        gw._bus._is_active = False
        for e in owned:
            self.assertFalse(e._attr_follow_gateway_availability,
                             f"{type(e).__name__} must opt out of gateway-availability coupling")
            # even if a disconnect notification reached it, availability must not drop
            e.async_write_ha_state = mock.Mock()
            asyncio.run(e._on_gateway_connection_state(False))
            self.assertTrue(e.available, f"{type(e).__name__} must stay available while disconnected")

    # --- lifecycle: registration, seeding, deregistration ---------------------

    def _run_added_to_hass(self, e):
        """Run the real async_added_to_hass with the heavy HA machinery stubbed."""
        e._attr_is_on = False   # short-circuits the RestoreEntity branch for binary sensors
        with mock.patch("custom_components.eltako.device.async_dispatcher_connect", return_value=(lambda: None)), \
             mock.patch.object(RestoreEntity, "async_added_to_hass", mock.AsyncMock()):
            asyncio.run(e.async_added_to_hass())

    def test_added_to_hass_registers_and_seeds_from_connected_gateway(self):
        gw = _mk_gw(connected=True)
        e = _device_entity(gw)
        self._run_added_to_hass(e)
        self.assertIn(e._on_gateway_connection_state, gw._connection_state_handlers)
        self.assertTrue(e._gateway_connected, "must be seeded from gateway.is_connected")
        self.assertTrue(e.available)

    def test_seed_reflects_disconnected_gateway(self):
        gw = _mk_gw(connected=False)     # gateway is down at add time
        e = _device_entity(gw)
        self._run_added_to_hass(e)
        self.assertFalse(e._gateway_connected)
        self.assertFalse(e.available)

    def test_seeded_disconnected_then_recovers_on_connect(self):
        """Finding #5 recovery path: seeded 'unavailable' at startup (gateway down),
        then recovers to available once the bus connects and fires the event."""
        gw = _mk_gw(connected=False)
        e = _device_entity(gw)
        self._run_added_to_hass(e)
        self.assertFalse(e.available)
        gw._bus._is_active = True                            # bus comes up
        asyncio.run(e._on_gateway_connection_state(True))    # connect event fires
        self.assertTrue(e.available, "must recover to available once the gateway connects")

    def test_deregisters_handler_on_removal(self):
        gw = _mk_gw(connected=True)
        e = _device_entity(gw)
        self._run_added_to_hass(e)
        self.assertIn(e._on_gateway_connection_state, gw._connection_state_handlers)
        # simulate HA running the registered on_remove callbacks
        for cb in list(getattr(e, "_on_remove", None) or []):
            cb()
        self.assertNotIn(e._on_gateway_connection_state, gw._connection_state_handlers,
                         "handler must be removed so it can't fire on a dead entity or leak")

    def test_gateway_owned_entity_registers_own_handler_not_b1(self):
        gw = _mk_gw(connected=True)
        e = GatewayConnectionState(Platform.BINARY_SENSOR, gw)
        self._run_added_to_hass(e)
        # it registers its OWN connection-state handler (to report the state) ...
        self.assertIn(e.async_value_changed, gw._connection_state_handlers)
        # ... but NOT the B1 availability handler (it opts out).
        self.assertNotIn(e._on_gateway_connection_state, gw._connection_state_handlers)


if __name__ == "__main__":
    unittest.main()
