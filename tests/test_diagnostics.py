"""Tests for the diagnostics dump (B4)."""
import asyncio
import unittest
from unittest import mock

from tests.mocks import *
from custom_components.eltako.diagnostics import async_get_config_entry_diagnostics
from custom_components.eltako.const import (
    DATA_ELTAKO, ELTAKO_CONFIG, CONF_GATEWAY_DESCRIPTION, CONF_SERIAL_PATH,
)

DIAG = "custom_components.eltako.diagnostics"


def _hass_with_gateway(gw):
    hass = HassMock(data={DATA_ELTAKO: {f"gateway_{gw.dev_id}": gw, ELTAKO_CONFIG: {}}})
    return hass


def _entry():
    return ConfigEntryMock(
        data={CONF_GATEWAY_DESCRIPTION: "GW - fam14 (Id: 123)", CONF_SERIAL_PATH: "192.168.0.9"},
        entry_id="e1", title="GW - fam14 (Id: 123)", unique_id="eltako_gateway_123")


class TestDiagnostics(unittest.TestCase):

    def _run(self, hass, entry):
        with mock.patch(f"{DIAG}.get_device_config_for_gateway", return_value={"sensor": []}):
            return asyncio.run(async_get_config_entry_diagnostics(hass, entry))

    def test_gateway_state_included(self):
        gw = GatewayMock(dev_id=123)
        diag = self._run(_hass_with_gateway(gw), _entry())
        g = diag["gateway"]
        self.assertEqual(g["dev_id"], 123)
        self.assertIn("base_id", g)
        self.assertIn("is_connected", g)          # B1 property surfaced here
        self.assertIn("received_messages", g)
        self.assertEqual(g["serial_path"], "**REDACTED**")

    def test_host_is_redacted(self):
        gw = GatewayMock(dev_id=123)
        diag = self._run(_hass_with_gateway(gw), _entry())
        # the LAN address / serial path must not leak into a shared diagnostics dump
        self.assertNotIn("192.168.0.9", str(diag))
        self.assertEqual(diag["config_entry"]["data"][CONF_SERIAL_PATH], "**REDACTED**")

    def test_no_gateway_is_handled(self):
        # entry present but gateway not in hass.data (e.g. failed setup)
        hass = HassMock(data={DATA_ELTAKO: {}})
        diag = self._run(hass, _entry())
        self.assertIsNone(diag["gateway"])
        self.assertIn("config_entry", diag)

    def test_device_config_error_never_raises(self):
        gw = GatewayMock(dev_id=123)
        hass = _hass_with_gateway(gw)
        with mock.patch(f"{DIAG}.get_device_config_for_gateway", side_effect=KeyError("boom")):
            diag = asyncio.run(async_get_config_entry_diagnostics(hass, _entry()))
        self.assertIn("device_config_error", diag)

    def test_missing_gateway_description_does_not_raise(self):
        # a broken/legacy entry lacking gateway_description must still yield a dump
        # (diagnostics is the tool used to report exactly this broken state)
        hass = HassMock(data={DATA_ELTAKO: {}})
        entry = ConfigEntryMock(data={}, entry_id="e1", title="broken")
        diag = asyncio.run(async_get_config_entry_diagnostics(hass, entry))
        self.assertIsNone(diag["gateway"])
        self.assertIn("gateway_lookup_error", diag)

    def test_device_config_is_redacted(self):
        # defense-in-depth: a host/address key surfacing in device_config is redacted
        gw = GatewayMock(dev_id=123)
        hass = _hass_with_gateway(gw)
        with mock.patch(f"{DIAG}.get_device_config_for_gateway",
                        return_value={"address": "10.0.0.5", "sensor": [{"id": "00-00-00-01"}]}):
            diag = asyncio.run(async_get_config_entry_diagnostics(hass, _entry()))
        self.assertNotIn("10.0.0.5", str(diag))
        self.assertEqual(diag["device_config"]["address"], "**REDACTED**")


if __name__ == "__main__":
    unittest.main()
