"""R3-18: a tilt task's post-sleep STOP telegram must be skipped if a newer tilt/movement
superseded it during the sleep - otherwise the stale STOP would halt the NEW movement."""
import asyncio
import unittest
from unittest import mock

from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform

from tests.mocks import *
from custom_components.eltako.cover import EltakoCover
from custom_components.eltako.config_helpers import DEFAULT_GENERAL_SETTINGS
from eltakobus import *

# mock update of Home Assistant
Entity.schedule_update_ha_state = mock.Mock(return_value=None)


class TestR3CCoverTilt(unittest.IsolatedAsyncioTestCase):

    def _cover(self) -> EltakoCover:
        gw = GatewayMock(dict(DEFAULT_GENERAL_SETTINGS))
        return EltakoCover(
            Platform.COVER, gw, AddressExpression.parse('00-00-00-01'), 'n',
            EEP.find('G5-3F-7F'), AddressExpression.parse('00-00-B1-06'), EEP.find('H5-3F-7F'),
            'shutter', 10, 10, 5)

    async def test_current_generation_tilt_sends_stop(self):
        cover = self._cover()
        sent = []
        cover.send_message = sent.append
        addr, _ = cover._sender_id
        cover._move_generation = 5

        task = asyncio.ensure_future(cover._async_run_tilt(addr, 0x01, 0, generation=5))
        cover._tilt_task = task
        await task

        # not superseded -> start AND stop telegrams are sent
        self.assertEqual(len(sent), 2)

    async def test_superseded_generation_skips_stale_stop(self):
        cover = self._cover()
        sent = []
        cover.send_message = sent.append
        addr, _ = cover._sender_id
        # a newer movement bumped the generation while this tilt was "sleeping"
        cover._move_generation = 9

        task = asyncio.ensure_future(cover._async_run_tilt(addr, 0x01, 0, generation=8))
        cover._tilt_task = task
        await task

        # only the start telegram; the stale STOP is skipped so it cannot halt the new movement
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
