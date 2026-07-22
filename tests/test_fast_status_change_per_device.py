"""Per-device `fast_status_change` override (single toggle instead of on/off buttons).

A fire-and-forget F6 rocker sender gives an actuator no status feedback, so the light/
switch entity stays `unknown` and Home Assistant renders two separate on/off buttons
instead of a single toggle. The global `fast_status_change` general setting makes the
entity optimistic (sets its own state on turn_on/turn_off) so it always reports a
definite on/off. These tests cover making that flag configurable PER DEVICE, plus the
cold-start fallback that gives the entity a defined `off` state when nothing could be
restored.
"""
import asyncio
import unittest
from unittest import mock

import voluptuous as vol

from tests.mocks import *
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.restore_state import RestoreEntity

from eltakobus import *
from custom_components.eltako.const import CONF_FAST_STATUS_CHANGE
from custom_components.eltako.config_helpers import DeviceConf
from custom_components.eltako.schema import LightSchema, SwitchSchema
from custom_components.eltako.switch import EltakoSwitch
from custom_components.eltako.light import EltakoDimmableLight


_LIGHT_CFG = {"id": "01-02-03-04", "eep": "M5-38-08",
              "sender": {"id": "00-00-B0-01", "eep": "F6-02-01"}}
_SWITCH_CFG = {"id": "01-02-03-04", "eep": "M5-38-08",
               "sender": {"id": "00-00-B0-01", "eep": "F6-02-01"}}


# mock update of Home Assistant
Entity.schedule_update_ha_state = mock.Mock(return_value=None)


def _gateway(global_fast: bool) -> GatewayMock:
    # R3-25: copy - never mutate the shared module-level DEFAULT_GENERAL_SETTINGS in place.
    settings = dict(DEFAULT_GENERAL_SETTINGS)
    settings[CONF_FAST_STATUS_CHANGE] = global_fast
    return GatewayMock(settings)


def _make_switch(per_device=None, global_fast=False, sender_eep="A5-38-08",
                 sender_id="00-00-B0-01") -> EltakoSwitch:
    return EltakoSwitch(
        Platform.SWITCH, _gateway(global_fast),
        AddressExpression.parse("00-00-00-01"), "sw", EEP.find("M5-38-08"),
        AddressExpression.parse(sender_id), EEP.find(sender_eep), per_device,
    )


def _make_light(per_device=None, global_fast=False) -> EltakoDimmableLight:
    return EltakoDimmableLight(
        Platform.LIGHT, _gateway(global_fast),
        AddressExpression.parse("00-00-00-01"), "li", EEP.find("A5-38-08"),
        AddressExpression.parse("00-00-B0-01"), EEP.find("A5-38-08"), per_device,
    )


class TestFastStatusChangeResolution(unittest.TestCase):
    """The property resolves per-device value first, global setting as fallback."""

    def test_none_inherits_global_false(self):
        self.assertFalse(_make_switch(per_device=None, global_fast=False).fast_status_change)
        self.assertFalse(_make_light(per_device=None, global_fast=False).fast_status_change)

    def test_none_inherits_global_true(self):
        self.assertTrue(_make_switch(per_device=None, global_fast=True).fast_status_change)
        self.assertTrue(_make_light(per_device=None, global_fast=True).fast_status_change)

    def test_true_overrides_global_false(self):
        self.assertTrue(_make_switch(per_device=True, global_fast=False).fast_status_change)
        self.assertTrue(_make_light(per_device=True, global_fast=False).fast_status_change)

    def test_false_overrides_global_true(self):
        self.assertFalse(_make_switch(per_device=False, global_fast=True).fast_status_change)
        self.assertFalse(_make_light(per_device=False, global_fast=True).fast_status_change)

    def test_missing_general_settings_key_defaults_false(self):
        # defensive .get(): a hand-built general_settings without the key must not KeyError
        sw = _make_switch(per_device=None, global_fast=False)
        sw.general_settings = {}
        self.assertFalse(sw.fast_status_change)


class TestOptimisticStateGatedByPerDevice(unittest.TestCase):
    """turn_on/turn_off set an optimistic state exactly when the RESOLVED flag is True."""

    def setUp(self):
        self.sent = []

    def _sender(self, msg):
        self.sent.append(msg)

    def test_per_device_true_sets_optimistic_state(self):
        sw = _make_switch(per_device=True, global_fast=False)   # global off, device on
        sw.send_message = self._sender
        sw._attr_is_on = None

        sw.turn_on()
        self.assertTrue(sw.is_on)
        sw.turn_off()
        self.assertFalse(sw.is_on)

    def test_per_device_false_suppresses_optimistic_state(self):
        sw = _make_switch(per_device=False, global_fast=True)   # global on, device off
        sw.send_message = self._sender
        sw._attr_is_on = None

        sw.turn_on()
        self.assertIsNone(sw.is_on, "device opted out -> no optimistic state, telegram still sent")
        self.assertTrue(len(self.sent) >= 1, "the switch command must still be sent")

    def test_light_per_device_true_sets_optimistic_state(self):
        li = _make_light(per_device=True, global_fast=False)
        li.send_message = self._sender
        li._attr_is_on = None

        li.turn_on()
        self.assertTrue(li.is_on)
        li.turn_off()
        self.assertFalse(li.is_on)


class TestColdStartToggleFallback(unittest.TestCase):
    """async_added_to_hass gives an optimistic device a defined `off` when nothing
    could be restored, so HA shows a toggle from the first boot - but never overwrites
    a genuinely restored on/off."""

    def _run_added(self, e, last_state=None):
        e._attr_is_on = None
        e.async_get_last_state = mock.AsyncMock(return_value=last_state)
        with mock.patch("custom_components.eltako.device.async_dispatcher_connect", return_value=(lambda: None)), \
             mock.patch.object(RestoreEntity, "async_added_to_hass", mock.AsyncMock()):
            asyncio.run(e.async_added_to_hass())

    def test_no_restore_defaults_off_when_optimistic(self):
        sw = _make_switch(per_device=True)
        self._run_added(sw, last_state=None)
        self.assertIs(sw._attr_is_on, False)
        self.assertEqual(sw.state, "off")

    def test_light_no_restore_defaults_off_when_optimistic(self):
        li = _make_light(per_device=True)
        self._run_added(li, last_state=None)
        self.assertIs(li._attr_is_on, False)

    def test_unknown_restore_defaults_off_when_optimistic(self):
        # the user's actual symptom: a previously `unknown` state -> load_value_initially
        # sets None -> fallback pins it to off.
        sw = _make_switch(per_device=True)
        self._run_added(sw, last_state=LatestStateMock("unknown"))
        self.assertIs(sw._attr_is_on, False)

    def test_restored_on_is_not_overwritten(self):
        sw = _make_switch(per_device=True)
        self._run_added(sw, last_state=LatestStateMock("on"))
        self.assertTrue(sw._attr_is_on, "a restored ON must survive the fallback")

    def test_not_optimistic_stays_unknown(self):
        # without fast_status_change the fallback must NOT invent a state (unchanged behavior)
        sw = _make_switch(per_device=False, global_fast=False)
        self._run_added(sw, last_state=None)
        self.assertIsNone(sw._attr_is_on)


class TestSchemaWiring(unittest.TestCase):
    """The YAML key validates on light/switch, is optional (no default), rejects
    non-booleans, and flows through DeviceConf to the platform setup."""

    def test_light_accepts_flag(self):
        r = LightSchema.ENTITY_SCHEMA({**_LIGHT_CFG, "fast_status_change": True})
        self.assertTrue(r[CONF_FAST_STATUS_CHANGE])

    def test_switch_accepts_flag(self):
        r = SwitchSchema.ENTITY_SCHEMA({**_SWITCH_CFG, "fast_status_change": False})
        self.assertFalse(r[CONF_FAST_STATUS_CHANGE])

    def test_absent_key_has_no_default(self):
        # no schema default -> key stays absent -> DeviceConf.get() returns None -> inherit global
        r = LightSchema.ENTITY_SCHEMA(_LIGHT_CFG)
        self.assertNotIn(CONF_FAST_STATUS_CHANGE, r)
        self.assertIsNone(DeviceConf(r).get(CONF_FAST_STATUS_CHANGE))

    def test_non_boolean_rejected(self):
        with self.assertRaises(vol.Invalid):
            LightSchema.ENTITY_SCHEMA({**_LIGHT_CFG, "fast_status_change": "yesish"})

    def test_flows_through_device_conf(self):
        r = SwitchSchema.ENTITY_SCHEMA({**_SWITCH_CFG, "fast_status_change": True})
        self.assertTrue(DeviceConf(r).get(CONF_FAST_STATUS_CHANGE))


if __name__ == "__main__":
    unittest.main()
