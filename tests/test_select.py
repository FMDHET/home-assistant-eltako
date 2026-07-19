"""Tests for the climate-priority select entity (A6, roadmap wave A).

Covers the round-2 restore fix (unavailable/unknown -> default, never fired onto
the bus) and the loose select<->climate event contract."""
import asyncio
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform

from tests.mocks import *
from custom_components.eltako import config_helpers
from custom_components.eltako.select import ClimatePriority
from custom_components.eltako.const import EVENT_CLIMATE_PRIORITY_SELECTED
from eltakobus import AddressExpression
from eltakobus.eep import EEP, A5_10_06

Entity.schedule_update_ha_state = mock.Mock(return_value=None)

DEFAULT = A5_10_06.ControllerPriority.AUTO.description
THERMO = A5_10_06.ControllerPriority.THERMOSTAT.description


class TestClimatePrioritySelect(unittest.TestCase):

    def _select(self):
        gw = GatewayMock(dev_id=123)
        sel = ClimatePriority(Platform.CLIMATE, gw, AddressExpression.parse("00-00-00-01"),
                              "n", EEP.find("A5-10-06"))
        sel.hass = HassMock()
        return sel

    def test_restore_valid_option(self):
        sel = self._select()
        sel.load_value_initially(LatestStateMock(THERMO))
        self.assertEqual(sel.current_option, THERMO)
        # exactly one event fired, carrying the restored (valid) priority
        self.assertEqual(len(sel.hass.bus.fired_events), 1)
        self.assertEqual(sel.hass.bus.fired_events[-1]['event_data']['priority'], THERMO)

    def test_restore_unavailable_falls_back_to_default(self):
        for bad in ('unavailable', 'unknown', 'something_removed'):
            sel = self._select()
            sel.load_value_initially(LatestStateMock(bad))
            self.assertEqual(sel.current_option, DEFAULT, f"{bad!r} must fall back to default")
            # the bus must never receive an invalid priority
            self.assertEqual(sel.hass.bus.fired_events[-1]['event_data']['priority'], DEFAULT)

    def test_select_option_fires_event(self):
        sel = self._select()
        asyncio.run(sel.async_select_option(THERMO))
        self.assertEqual(sel.current_option, THERMO)
        self.assertEqual(sel.hass.bus.fired_events[-1]['event_data']['priority'], THERMO)

    def test_event_contract_matches_climate_subscription(self):
        """select fires on the same event id climate subscribes to (both derive it
        from gateway.base_id + EVENT_CLIMATE_PRIORITY_SELECTED + dev_id)."""
        sel = self._select()
        expected = config_helpers.get_bus_event_type(
            sel.gateway.base_id, EVENT_CLIMATE_PRIORITY_SELECTED, sel.dev_id)
        self.assertEqual(sel.event_id, expected)


if __name__ == "__main__":
    unittest.main()
