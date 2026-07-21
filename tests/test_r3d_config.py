"""R3-D domain-5 config fixes: schema validation (R3D-02/03/16), config_helpers (R3D-06/07),
and the config-flow hostname acceptance for LAN gateways (R3D-01)."""
import unittest
from unittest import mock

import voluptuous as vol

from tests.mocks import *
from custom_components.eltako import config_helpers
from custom_components.eltako.config_flow import EltakoFlowHandler
from custom_components.eltako.schema import GatewaySchema, ClimateSchema
from custom_components.eltako.config_helpers import (
    get_id_from_gateway_name, get_general_settings_from_configuration,
)
from custom_components.eltako.const import CONF_GATEWAY_PORT, CONF_SERIAL_PATH, CONF_GATEWAY_DESCRIPTION

CONF_DEVICE_TYPE = "device_type"

_CLIMATE = {'id': '00-00-00-01', 'eep': 'A5-10-06', 'sender': {'id': '00-00-B0-01', 'eep': 'A5-10-06'}}


class TestR3DSchema(unittest.TestCase):

    def test_r3d_02_gateway_port_has_no_schema_default(self):
        # init supplies the per-type default (VIRT_GW_PORT for VNG, 5100 for physical LAN);
        # a schema default of 5100 would override that for the VNG.
        g = GatewaySchema.ENTITY_SCHEMA({'id': 1, 'device_type': 'mgw-lan'})
        self.assertNotIn(CONF_GATEWAY_PORT, g)
        # an explicit port is still validated/accepted
        self.assertEqual(GatewaySchema.ENTITY_SCHEMA({'id': 1, 'device_type': 'mgw-lan', 'port': 2325})[CONF_GATEWAY_PORT], 2325)

    def test_r3d_03_zero_timeout_rejected(self):
        for field in ('tcp_keep_alive_timeout', 'reconnection_timeout'):
            with self.assertRaises(vol.Invalid):
                GatewaySchema.ENTITY_SCHEMA({'id': 1, 'device_type': 'mgw-lan', field: 0})

    def test_r3d_16_min_ge_max_target_temp_rejected(self):
        with self.assertRaises(vol.Invalid):
            ClimateSchema.ENTITY_SCHEMA({**_CLIMATE, 'min_target_temperature': 25, 'max_target_temperature': 17})
        with self.assertRaises(vol.Invalid):
            ClimateSchema.ENTITY_SCHEMA({**_CLIMATE, 'min_target_temperature': 20, 'max_target_temperature': 20})
        # valid ordering (and defaults) still pass
        r = ClimateSchema.ENTITY_SCHEMA(_CLIMATE)
        self.assertLess(r['min_target_temperature'], r['max_target_temperature'])


class TestR3DConfigHelpers(unittest.TestCase):

    def test_r3d_07_id_parsed_from_trailing_marker(self):
        # a gateway whose user name itself contains "(Id: N)" must resolve to the real trailing id
        self.assertEqual(get_id_from_gateway_name('Rack (Id: 99) - mgw-lan (Id: 5)'), 5)
        self.assertEqual(get_id_from_gateway_name('GW1 - fgw14usb (Id: 1)'), 1)
        self.assertIsNone(get_id_from_gateway_name('no marker here'))

    def test_r3d_06_general_settings_no_keyerror_on_empty_hass(self):
        class _H:
            data = {}
        # neither a bare hass with empty data nor hass=None may raise KeyError
        self.assertIsNotNone(get_general_settings_from_configuration(_H()))
        self.assertIsNotNone(get_general_settings_from_configuration(None))


class TestR3DConfigFlowHostname(unittest.IsolatedAsyncioTestCase):

    def _flow(self, executor_job):
        flow = EltakoFlowHandler()
        flow.hass = mock.Mock()
        flow.hass.async_add_executor_job = executor_job
        return flow

    async def _validate(self, flow, serial_path):
        with mock.patch.object(config_helpers, 'async_get_home_assistant_config', mock.AsyncMock(return_value={})), \
             mock.patch.object(config_helpers, 'find_gateway_config_by_id', return_value={CONF_DEVICE_TYPE: 'mgw-lan'}):
            return await flow.validate_eltako_conf({
                CONF_SERIAL_PATH: serial_path,
                CONF_GATEWAY_DESCRIPTION: 'GW (Id: 5)',
            })

    async def test_r3d_01_resolvable_hostname_accepted(self):
        # getaddrinfo succeeds -> hostname accepted (was rejected as "not an IP")
        flow = self._flow(mock.AsyncMock(return_value=[(2, 1, 6, '', ('1.2.3.4', 0))]))
        self.assertTrue(await self._validate(flow, 'eltako-mgw.local'))

    async def test_r3d_01_ip_literal_still_accepted(self):
        # IP literal path does not even hit getaddrinfo
        flow = self._flow(mock.AsyncMock(side_effect=AssertionError("getaddrinfo must not be called for an IP literal")))
        self.assertTrue(await self._validate(flow, '192.168.177.15'))

    async def test_r3d_01_unresolvable_hostname_rejected(self):
        flow = self._flow(mock.AsyncMock(side_effect=OSError("name or service not known")))
        self.assertFalse(await self._validate(flow, 'does-not-exist.invalid'))

    async def test_r3d_01_malformed_name_does_not_crash(self):
        # review Bug 1: getaddrinfo runs the idna codec and raises UnicodeError (NOT OSError)
        # for a leading-dot / double-dot name; it must be caught and reported invalid, not crash.
        flow = self._flow(mock.AsyncMock(side_effect=lambda func, *a: func(*a)))   # run getaddrinfo for real
        self.assertFalse(await self._validate(flow, '.local'))
        self.assertFalse(await self._validate(flow, 'host..local'))

    async def test_r3d_01_empty_address_rejected_before_resolving(self):
        # review Bug 2: getaddrinfo("") resolves to loopback, so an empty/blank address must be
        # rejected up front - before the resolver is consulted.
        resolver = mock.AsyncMock(return_value=[(2, 1, 6, '', ('127.0.0.1', 0))])
        flow = self._flow(resolver)
        self.assertFalse(await self._validate(flow, ''))
        self.assertFalse(await self._validate(flow, '   '))
        resolver.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
