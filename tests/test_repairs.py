"""Tests for the AG1 base-id repair issue (B4).

ESP2 gateways other than the FAM14 never auto-query a base id, so a base id left at
00-00-00-00 makes the receive path silently drop every telegram. _update_base_id_repair
surfaces that as a repair issue (and clears it once a valid base id is configured)."""
import unittest
from unittest import mock

from tests.mocks import *
from custom_components.eltako.eltako_integration_init import _update_base_id_repair
from custom_components.eltako.const import GatewayDeviceType
from eltakobus import AddressExpression

IR = "homeassistant.helpers.issue_registry"
ZERO = AddressExpression((b'\x00\x00\x00\x00', None))
VALID = AddressExpression((b'\xff\xaa\x80\x00', None))


def _gw(dev_type, base_id):
    gw = GatewayMock(dev_id=123)
    gw._attr_dev_type = dev_type
    gw._attr_base_id = base_id
    return gw


class TestBaseIdRepair(unittest.TestCase):

    def _run(self, gw):
        with mock.patch(f"{IR}.async_create_issue") as create, \
             mock.patch(f"{IR}.async_delete_issue") as delete:
            _update_base_id_repair(gw.hass, gw)
        return create, delete

    def test_esp2_non_fam14_zero_base_id_creates_issue(self):
        create, delete = self._run(_gw(GatewayDeviceType.EltakoFAMUSB, ZERO))
        create.assert_called_once()
        self.assertEqual(create.call_args.kwargs["translation_key"], "missing_base_id")
        delete.assert_not_called()

    def test_esp2_non_fam14_valid_base_id_clears_issue(self):
        create, delete = self._run(_gw(GatewayDeviceType.EltakoFAMUSB, VALID))
        create.assert_not_called()
        delete.assert_called_once()

    def test_fam14_zero_base_id_no_issue(self):
        # FAM14 auto-queries its base id -> a transient zero is not a misconfiguration
        create, delete = self._run(_gw(GatewayDeviceType.EltakoFAM14, ZERO))
        create.assert_not_called()
        delete.assert_called_once()

    def test_esp3_zero_base_id_no_issue(self):
        # ESP3 LAN gateway also queries its base id
        create, delete = self._run(_gw(GatewayDeviceType.MGW_LAN, ZERO))
        create.assert_not_called()
        delete.assert_called_once()

    def test_virtual_network_gateway_no_issue(self):
        # the VNG hardcodes base_id 00-00-00-00 and forwards other gateways' telegrams;
        # it never drops via the receive gate, so it must NOT get the repair (else a
        # permanent, unfixable false positive).
        create, delete = self._run(_gw(GatewayDeviceType.VirtualNetworkAdapter, ZERO))
        create.assert_not_called()
        delete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
