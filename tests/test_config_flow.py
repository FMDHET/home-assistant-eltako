"""Tests for the config flow (A6, roadmap wave A) — previously 0% coverage.

Tests the decision logic of EltakoFlowHandler.manual_selection_routine by replacing
the three result methods with recorders and patching the config_helpers/gateway
lookups, independent of HA's DataEntryFlow internals. Covers the A4/AF1 unique_id
guard, the empty-gateway abort, and the validation-failure path."""
import asyncio
import unittest
from unittest import mock

from tests.mocks import *
from custom_components.eltako.config_flow import EltakoFlowHandler
from custom_components.eltako.const import (
    DATA_ELTAKO, CONF_GATEWAY_DESCRIPTION, CONF_SERIAL_PATH,
    ERROR_INVALID_GATEWAY_PATH, ABORT_NO_CONFIGURATION_AVAILABLE,
)

CH = "custom_components.eltako.config_flow.config_helpers"
GW = "custom_components.eltako.config_flow.gateway"
DR = "custom_components.eltako.config_flow.dr"


def _flow():
    flow = EltakoFlowHandler()
    flow.hass = HassMock(data={DATA_ELTAKO: {}})
    flow.async_show_form = lambda **k: {"type": "form", **k}
    flow.async_abort = lambda **k: {"type": "abort", **k}
    flow.async_create_entry = lambda **k: {"type": "entry", **k}
    flow.async_set_unique_id = mock.AsyncMock()
    flow._abort_if_unique_id_configured = mock.Mock()
    return flow


class TestConfigFlow(unittest.TestCase):

    def test_abort_when_no_gateway_configured(self):
        flow = _flow()
        with mock.patch(f"{CH}.async_get_home_assistant_config", mock.AsyncMock(return_value={})), \
             mock.patch(f"{CH}.async_get_list_of_gateway_descriptions", mock.AsyncMock(return_value={})), \
             mock.patch(f"{GW}.detect", return_value=[]):
            result = asyncio.run(flow.async_step_detect(None))
        self.assertEqual(result["type"], "abort")
        self.assertEqual(result["reason"], ABORT_NO_CONFIGURATION_AVAILABLE)

    def test_successful_selection_creates_entry_with_unique_id(self):
        flow = _flow()
        user_input = {CONF_GATEWAY_DESCRIPTION: "GW - fam-usb (Id: 2)", CONF_SERIAL_PATH: "/dev/ttyUSB0"}
        with mock.patch.object(flow, "validate_eltako_conf", mock.AsyncMock(return_value=True)), \
             mock.patch(f"{CH}.async_get_home_assistant_config", mock.AsyncMock(return_value={})):
            result = asyncio.run(flow.async_step_detect(user_input))
        self.assertEqual(result["type"], "entry")
        self.assertEqual(result["title"], "GW - fam-usb (Id: 2)")
        # AF1: unique id derived from the gateway id, dedup guard invoked
        flow.async_set_unique_id.assert_awaited_once_with("eltako_gateway_2")
        flow._abort_if_unique_id_configured.assert_called_once()

    def test_validation_failure_shows_error_no_entry(self):
        flow = _flow()
        created = []
        flow.async_create_entry = lambda **k: created.append(k) or {"type": "entry", **k}
        user_input = {CONF_GATEWAY_DESCRIPTION: "GW - fam-usb (Id: 2)", CONF_SERIAL_PATH: "bad"}
        with mock.patch.object(flow, "validate_eltako_conf", mock.AsyncMock(return_value=False)), \
             mock.patch(f"{CH}.async_get_home_assistant_config", mock.AsyncMock(return_value={})), \
             mock.patch(f"{CH}.async_get_list_of_gateway_descriptions", mock.AsyncMock(return_value={2: "GW - fam-usb (Id: 2)"})), \
             mock.patch(f"{CH}.find_gateway_config_by_id", return_value={CONF_SERIAL_PATH: "/dev/ttyUSB0"}), \
             mock.patch(f"{GW}.detect", return_value=["/dev/ttyUSB0"]), \
             mock.patch(f"{GW}.async_get_serial_path_of_registered_gateway", mock.AsyncMock(return_value=[])), \
             mock.patch(f"{DR}.async_get", return_value=mock.Mock()):
            result = asyncio.run(flow.async_step_detect(user_input))
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"].get(CONF_SERIAL_PATH), ERROR_INVALID_GATEWAY_PATH)
        self.assertEqual(created, [], "no entry must be created on validation failure")


if __name__ == "__main__":
    unittest.main()
