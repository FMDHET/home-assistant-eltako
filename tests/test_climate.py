import unittest
from tests.mocks import *
from unittest import mock
from homeassistant.helpers.entity import Entity
from homeassistant.const import Platform
from homeassistant.components.climate import HVACMode
from custom_components.eltako.climate import ClimateController
from custom_components.eltako.config_helpers import *
from custom_components.eltako.device import EltakoEntity
from eltakobus.eep import *
from eltakobus import *

# mock update of Home Assistant
Entity.schedule_update_ha_state = mock.Mock(return_value=None)
ClimateController.schedule_update_ha_state = mock.Mock(return_value=None)
EltakoEntity.send_message = mock.Mock(return_value=None)
# EltakoBinarySensor.hass.bus.fire is mocked by class HassMock

class EventDataMock():
    def __init__(self,d):
        self.data = d

def create_climate_entity(thermostat:DeviceConf=None, cooling_switch:DeviceConf=None):    
    gw = GatewayMock(dev_id=12345)
    dev_id = AddressExpression.parse("00-00-00-01") # heating cooling actuator
    dev_name = "Room 1"
    dev_eep = A5_10_06
    sender_id = AddressExpression.parse("00-00-B0-01")  # home assistant
    sender_eep = A5_10_06
    temp_unit = "°C"
    min_temp = 16
    max_temp = 25
    
    cc = ClimateController(Platform.CLIMATE, gw, dev_id, dev_name, dev_eep, sender_id, sender_eep, temp_unit, min_temp, max_temp, thermostat, cooling_switch, None)
    return cc

class TestClimate(unittest.TestCase):

    def test_climate_temp_actuator(self):
        cc = create_climate_entity()
        self.assertEqual(cc.unique_id, 'eltako_gw_12345_00_00_00_01')
        self.assertEqual(cc.entity_id, 'climate.eltako_gw_12345_00_00_00_01')
        self.assertEqual(cc.dev_name, 'Room 1')
        self.assertEqual(cc.temperature_unit, '°C')
        self.assertEqual(cc.cooling_sender, None)
        self.assertEqual(cc.cooling_switch, None)
        self.assertEqual(cc.thermostat, None)
        self.assertEqual(cc.hvac_mode, HVACMode.OFF)
        self.assertEqual(cc._attr_actuator_mode, A5_10_06.HeaterMode.NORMAL)

        self.assertEqual(cc.target_temperature, 0)
        self.assertEqual(cc.current_temperature, 0)

        mode = A5_10_06.HeaterMode.NORMAL
        target_temp = 24
        current_temperature = 21
        prio = A5_10_06.ControllerPriority.AUTO
        # R3-04: model runtime - the local actuator address 00-00-00-01 arrives at value_changed
        # already externalized by the gateway (base_id FF-AA-80-00 -> FF-AA-80-01).
        msg = A5_10_06(mode, target_temp, current_temperature, prio).encode_message(b'\xFF\xAA\x80\x01')
        cc.value_changed(msg)
        self.assertEqual(cc.hvac_mode, HVACMode.HEAT)
        self.assertEqual( cc._attr_actuator_mode, mode)
        self.assertEqual( round(cc.current_temperature), current_temperature)
        self.assertEqual( round(cc.target_temperature), target_temp)
        # priority is handled in select entity
        self.assertEqual( A5_10_06.decode_message(msg).priority, prio)
        

    def test_climate_thermostat(self):
        thermostat = DeviceConf({
            CONF_ID: 'FF-FF-FF-01',
            CONF_EEP: 'A5-10-06',
        })
        cc = create_climate_entity(thermostat)
        self.assertEqual(cc.unique_id, 'eltako_gw_12345_00_00_00_01')
        self.assertEqual(cc.entity_id, 'climate.eltako_gw_12345_00_00_00_01')
        self.assertEqual(cc.dev_name, 'Room 1')
        self.assertEqual(cc.temperature_unit, '°C')
        self.assertEqual(cc.cooling_sender, None)
        self.assertEqual(cc.cooling_switch, None)
        self.assertIsNotNone(cc.thermostat)
        self.assertEqual(cc.hvac_mode, HVACMode.OFF)
        self.assertEqual(cc._attr_actuator_mode, A5_10_06.HeaterMode.NORMAL)

        self.assertEqual(cc.target_temperature, 0)
        self.assertEqual(cc.current_temperature, 0)

        mode = A5_10_06.HeaterMode.NORMAL
        target_temp = 24
        current_temperature = 21
        prio = A5_10_06.ControllerPriority.AUTO
        msg = A5_10_06(mode, target_temp, current_temperature, prio).encode_message(b'\xFF\xFF\xFF\x01')
        cc.value_changed(msg)
        self.assertEqual(cc.hvac_mode, HVACMode.HEAT)
        self.assertEqual( cc._attr_actuator_mode, mode)
        self.assertEqual( round(cc.current_temperature), current_temperature)
        self.assertEqual( round(cc.target_temperature), target_temp)


    def test_climate_cooling_switch(self):
        cooling_switch = DeviceConf({
            CONF_ID: 'FF-FF-FF-01',
            CONF_EEP: 'A5-10-06',
        })
        cc = create_climate_entity(cooling_switch=cooling_switch)
        self.assertEqual(cc.unique_id, 'eltako_gw_12345_00_00_00_01')
        self.assertEqual(cc.entity_id, 'climate.eltako_gw_12345_00_00_00_01')
        self.assertEqual(cc.dev_name, 'Room 1')
        self.assertEqual(cc.temperature_unit, '°C')
        self.assertEqual(cc.cooling_sender, None)
        self.assertIsNotNone(cc.cooling_switch)
        self.assertEqual(cc.thermostat, None)
        self.assertEqual(cc.hvac_mode, HVACMode.OFF)
        self.assertEqual(cc._attr_actuator_mode, A5_10_06.HeaterMode.NORMAL)

        self.assertEqual(cc.target_temperature, 0)
        self.assertEqual(cc.current_temperature, 0)

        #0x70 = 3
        msg = F6_02_01(3, 1, 0, 0).encode_message(b'\xFF\xFF\xFF\x01')
        cc.value_changed(msg)
        ##TODO:
        # self.assertEqual(cc.hvac_mode, HVACMode.HEAT)
        # self.assertEqual(cc._actuator_mode, A5_10_06.Heater_Mode.NORMAL);
        # self.assertEqual( round(cc.current_temperature), current_temperature)
        # self.assertEqual( round(cc.target_temperature), target_temp)


    def test_initial_loading(self):
        cc = create_climate_entity()

        cc.load_value_initially(LatestStateMock('heat', 
                                                attributes={'hvac_modes': ['heat', 'off'], 
                                                            'min_temp': 17, 
                                                            'max_temp': 25, 
                                                            'current_temperature': 19.8, 
                                                            'temperature': 22.5, 
                                                            'friendly_name': 'Bad Room', 
                                                            'supported_features': 385}))
        self.assertEqual(cc.current_temperature, 19.8)
        self.assertEqual(cc.target_temperature, 22.5)
        self.assertEqual(cc.state, 'heat')


    def test_initial_loading_None(self):
        cc = create_climate_entity()

        cc.load_value_initially(LatestStateMock(None))
        self.assertEqual(cc.current_temperature, None)
        self.assertEqual(cc.target_temperature, None)
        self.assertEqual(cc.state, None)
    
class TestClimateAsync(unittest.IsolatedAsyncioTestCase):

    async def test_climate_cooling_switch(self):
        cooling_switch = DeviceConf({
            CONF_ID: 'FF-FF-FF-01',
            CONF_EEP: 'A5-10-06',
            CONF_SWITCH_BUTTON: 0x50
        })
        cc = create_climate_entity(cooling_switch=cooling_switch)
        self.assertEqual(cc.unique_id, 'eltako_gw_12345_00_00_00_01')
        self.assertEqual(cc.entity_id, 'climate.eltako_gw_12345_00_00_00_01')
        self.assertEqual(cc.dev_name, 'Room 1')
        self.assertEqual(cc.temperature_unit, '°C')
        self.assertEqual(cc.cooling_sender, None)
        self.assertIsNotNone(cc.cooling_switch)
        self.assertEqual(cc.thermostat, None)
        self.assertEqual(cc.hvac_mode, HVACMode.OFF)
        self.assertEqual(cc._attr_actuator_mode, A5_10_06.HeaterMode.NORMAL)

        self.assertEqual(cc.target_temperature, 0)
        self.assertEqual(cc.current_temperature, 0)

        #0x70 = 3
        msg = F6_02_01(3, 1, 0, 0).encode_message(b'\xFF\xFF\xFF\x01')
        # cc.value_changed(msg)
        await cc.async_handle_cooling_switch_event(EventDataMock({'switch_address': cooling_switch.id, 'data': cooling_switch[CONF_SWITCH_BUTTON]}))
        self.assertEqual(cc.hvac_mode, HVACMode.COOL)

    # --- R3-04: thermostat telegrams must reach the entity --------------------

    def test_r3_04_thermostat_address_listened_as_bytes(self):
        """The receive filter (device.py) compares `adr[0] in listen_to_addresses`
        with bytes; appending the AddressExpression made every thermostat telegram
        fail the filter. Every listened address must be raw bytes."""
        thermostat = DeviceConf({CONF_ID: '05-1E-83-15', CONF_EEP: 'A5-10-06'})
        cc = create_climate_entity(thermostat=thermostat)
        self.assertTrue(all(isinstance(a, (bytes, bytearray)) for a in cc.listen_to_addresses),
                        f"listen_to_addresses must be bytes, got {cc.listen_to_addresses}")
        self.assertIn(b'\x05\x1e\x83\x15', cc.listen_to_addresses)

    def test_r3_04_thermostat_telegram_reaches_value_changed(self):
        """End-to-end (global thermostat address): a thermostat A5-10-06 telegram
        routed through the receive callback must update current_temperature."""
        thermostat = DeviceConf({CONF_ID: '05-1E-83-15', CONF_EEP: 'A5-10-06'})
        cc = create_climate_entity(thermostat=thermostat)
        self.assertEqual(cc.current_temperature, 0)
        msg = Regular4BSMessage(address=b'\x05\x1e\x83\x15', data=b'\x70\x80\x76\x0F', status=0x80)
        cc._message_received_callback({'esp2_msg': msg})
        self.assertAlmostEqual(cc.current_temperature, 21.49019607843137, places=5)

    def test_r3_04_local_thermostat_address_is_externalized(self):
        """A local thermostat address must be externalized with the gateway base id
        (GatewayMock base_id FF-AA-80-00), mirroring the actuator address handling."""
        thermostat = DeviceConf({CONF_ID: '00-00-00-05', CONF_EEP: 'A5-10-06'})
        cc = create_climate_entity(thermostat=thermostat)
        self.assertIn(b'\xff\xaa\x80\x05', cc.listen_to_addresses)

    def test_r3_04_local_thermostat_telegram_end_to_end(self):
        """End-to-end for a LOCAL thermostat address: the gateway externalizes it
        (00-00-00-05 -> FF-AA-80-05) before dispatch, so value_changed must match on
        the externalized address - otherwise the telegram passes the filter but is
        dropped in value_changed (the incomplete-fix bug found in review)."""
        thermostat = DeviceConf({CONF_ID: '00-00-00-05', CONF_EEP: 'A5-10-06'})
        cc = create_climate_entity(thermostat=thermostat)
        self.assertEqual(cc.current_temperature, 0)
        msg = Regular4BSMessage(address=b'\xff\xaa\x80\x05', data=b'\x70\x80\x76\x0F', status=0x80)
        cc._message_received_callback({'esp2_msg': msg})
        self.assertAlmostEqual(cc.current_temperature, 21.49019607843137, places=5)

    def test_r3_04_local_actuator_telegram_end_to_end(self):
        """End-to-end for the actuator's OWN status: dev_id 00-00-00-01 arrives
        externalized as FF-AA-80-01; value_changed must update from it. This guards
        the actuator branch (the pre-existing raw-vs-external comparison bug that
        kept climate from ever updating for the common local-address config)."""
        cc = create_climate_entity()   # dev_id 00-00-00-01 (local), no thermostat
        self.assertEqual(cc.current_temperature, 0)
        msg = Regular4BSMessage(address=b'\xff\xaa\x80\x01', data=b'\x70\x80\x76\x0F', status=0x80)
        cc._message_received_callback({'esp2_msg': msg})
        self.assertAlmostEqual(cc.current_temperature, 21.49019607843137, places=5)

    def test_r3_04_thermostat_block_without_id_does_not_crash(self):
        """A room_thermostat block without an `id` must not abort entity construction
        (DeviceConf is truthy but .id is None -> None.is_local_address() would raise)."""
        thermostat = DeviceConf({CONF_EEP: 'A5-10-06'})   # no CONF_ID
        cc = create_climate_entity(thermostat=thermostat)
        self.assertIsNone(cc._external_thermostat_id)
        # only the actuator's own (externalized) address is listened to
        self.assertEqual(cc.listen_to_addresses, [b'\xff\xaa\x80\x01'])