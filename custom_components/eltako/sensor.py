"""Support for Eltako sensors."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from eltakobus.util import AddressExpression, b2s
from eltakobus.eep import *
from eltakobus.message import ESP2Message

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    STATE_CLOSED,
    STATE_OPEN,
    LIGHT_LUX,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfSpeed,
    UnitOfEnergy,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
    Platform,
    PERCENTAGE,
    CONF_LANGUAGE,
    UnitOfElectricPotential,
    EntityCategory,
)
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType

from .device import *
from .config_helpers import *
from .gateway import EnOceanGateway
from .const import *
from . import get_gateway_from_hass, get_device_config_for_gateway
from . import config_helpers
from .virtual_network_gateway import VirtualNetworkGateway

DEFAULT_DEVICE_NAME_WINDOW_HANDLE = "Window handle"
DEFAULT_DEVICE_NAME_WEATHER_STATION = "Weather station"
DEFAULT_DEVICE_NAME_ELECTRICITY_METER = "Electricity meter"
DEFAULT_DEVICE_NAME_GAS_METER = "Gas meter"
DEFAULT_DEVICE_NAME_WATER_METER = "Water meter"
DEFAULT_DEVICE_NAME_HYGROSTAT = "Hygrostat"
DEFAULT_DEVICE_NAME_THERMOMETER = "Thermometer"
DEFAULT_DEVICE_NAME_AIR_QUAILTY_SENSOR = "Air Quality Sensor"

SENSOR_TYPE_BATTERY_VOLTAGE = "electricity_voltage"
SENSOR_TYPE_ELECTRICITY_CUMULATIVE = "electricity_cumulative"
SENSOR_TYPE_ELECTRICITY_CURRENT = "electricity_current"
SENSOR_TYPE_GAS_CUMULATIVE = "gas_cumulative"
SENSOR_TYPE_GAS_CURRENT = "gas_current"
SENSOR_TYPE_WATER_CUMULATIVE = "water_cumulative"
SENSOR_TYPE_WATER_CURRENT = "water_current"
SENSOR_TYPE_TEMPERATURE = "temperature"
SENSOR_TYPE_TARGET_TEMPERATURE = "target_temperature"
SENSOR_TYPE_HUMIDITY = "humidity"
SENSOR_TYPE_VOLTAGE = "voltage"
SENSOR_TYPE_PIR = "pir"
SENSOR_TYPE_WINDOWHANDLE = "windowhandle"
SENSOR_TYPE_WEATHER_STATION_ILLUMINANCE_DAWN = "weather_station_illuminance_dawn"
SENSOR_TYPE_WEATHER_STATION_TEMPERATURE = "weather_station_temperature"
SENSOR_TYPE_WEATHER_STATION_WIND_SPEED = "weather_station_wind_speed"
SENSOR_TYPE_WEATHER_STATION_RAIN = "weather_station_rain"
SENSOR_TYPE_WEATHER_STATION_ILLUMINANCE_WEST = "weather_station_illuminance_west"
SENSOR_TYPE_WEATHER_STATION_ILLUMINANCE_CENTRAL = "weather_station_illuminance_central"
SENSOR_TYPE_WEATHER_STATION_ILLUMINANCE_EAST = "weather_station_illuminance_east"
SENSOR_TYPE_ILLUMINANCE = "illuminance"


# M7: HA requires EntityDescription subclasses to be frozen (kw_only matches the
# base class); the non-frozen form only worked via a transitional HA metaclass
# and would raise TypeError at module import with future HA versions.
@dataclass(frozen=True, kw_only=True)
class EltakoSensorEntityDescription(SensorEntityDescription):
    """Describes Eltako sensor entity."""

SENSOR_DESC_BATTERY_VOLTAGE = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_BATTERY_VOLTAGE,
    name="Battery Voltage",
    native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    icon="mdi:lightning-bolt",
    # N3: BATTERY device class requires unit '%'; this measures voltage in volts -> VOLTAGE
    device_class=SensorDeviceClass.VOLTAGE,
    state_class=SensorStateClass.MEASUREMENT,
)

SENSOR_DESC_ELECTRICITY_CUMULATIVE = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_ELECTRICITY_CUMULATIVE,
    name="Reading",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    icon="mdi:lightning-bolt",
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
)

SENSOR_DESC_ELECTRICITY_CURRENT = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_ELECTRICITY_CURRENT,
    name="Power",
    native_unit_of_measurement=UnitOfPower.WATT,
    icon="mdi:lightning-bolt",
    device_class=SensorDeviceClass.POWER,
    state_class=SensorStateClass.MEASUREMENT,
)

SENSOR_DESC_GAS_CUMULATIVE = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_GAS_CUMULATIVE,
    name="Reading",
    native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
    icon="mdi:fire",
    device_class=SensorDeviceClass.GAS,
    state_class=SensorStateClass.TOTAL_INCREASING,
)

SENSOR_DESC_GAS_CURRENT = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_GAS_CURRENT,
    name="Flow rate",
    native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
    icon="mdi:fire",
    state_class=SensorStateClass.MEASUREMENT,
)

SENSOR_DESC_WATER_CUMULATIVE = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_WATER_CUMULATIVE,
    name="Reading",
    native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
    icon="mdi:water",
    device_class=SensorDeviceClass.WATER,
    state_class=SensorStateClass.TOTAL_INCREASING,
)

SENSOR_DESC_WATER_CURRENT = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_WATER_CURRENT,
    name="Flow rate",
    native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
    icon="mdi:water",
    state_class=SensorStateClass.MEASUREMENT,
)

SENSOR_DESC_WINDOWHANDLE = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_WINDOWHANDLE,
    name="Window handle",
    icon="mdi:window-open-variant",
    # N3: 'window' is a BinarySensorDeviceClass, not valid for a sensor entity.
    # The handle reports a textual position (open/closed/tilted) -> no device_class.
    device_class=None,
    native_unit_of_measurement=None,
    suggested_display_precision=None,
    suggested_unit_of_measurement=None,
    state_class=None
)

SENSOR_DESC_WEATHER_STATION_ILLUMINANCE_DAWN = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_WEATHER_STATION_ILLUMINANCE_DAWN,
    name="Illuminance (dawn)",
    native_unit_of_measurement=LIGHT_LUX,
    icon="mdi:weather-sunset",
    device_class=SensorDeviceClass.ILLUMINANCE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
)

SENSOR_DESC_WEATHER_STATION_TEMPERATURE = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_WEATHER_STATION_TEMPERATURE,
    name="Temperature",
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    icon="mdi:thermometer",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
)

SENSOR_DESC_WEATHER_STATION_WIND_SPEED = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_WEATHER_STATION_WIND_SPEED,
    name="Wind speed",
    native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
    icon="mdi:windsock",
    device_class=SensorDeviceClass.WIND_SPEED,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=2,
)

SENSOR_DESC_WEATHER_STATION_RAIN = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_WEATHER_STATION_RAIN,
    name="Rain",
    # N3: "rain" is not a valid SensorDeviceClass and "" is not a valid unit.
    native_unit_of_measurement=None,
    icon="mdi:weather-pouring",
    device_class=None,
    state_class=SensorStateClass.MEASUREMENT,
)

SENSOR_DESC_WEATHER_STATION_ILLUMINANCE_WEST = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_WEATHER_STATION_ILLUMINANCE_WEST,
    name="Illuminance (west)",
    native_unit_of_measurement=LIGHT_LUX,
    icon="mdi:weather-sunny",
    device_class=SensorDeviceClass.ILLUMINANCE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
)

SENSOR_DESC_WEATHER_STATION_ILLUMINANCE_CENTRAL = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_WEATHER_STATION_ILLUMINANCE_CENTRAL,
    name="Illuminance (central)",
    native_unit_of_measurement=LIGHT_LUX,
    icon="mdi:weather-sunny",
    device_class=SensorDeviceClass.ILLUMINANCE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
)

SENSOR_DESC_WEATHER_STATION_ILLUMINANCE_EAST = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_WEATHER_STATION_ILLUMINANCE_EAST,
    name="Illuminance (east)",
    native_unit_of_measurement=LIGHT_LUX,
    icon="mdi:weather-sunny",
    device_class=SensorDeviceClass.ILLUMINANCE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
)

# A5: A5-06-01 exposes twilight (raw 0-255) + day_light (lux) separately in addition
# to the combined illumination value (the library already decodes all three).
SENSOR_TYPE_TWILIGHT = "twilight"
SENSOR_TYPE_DAYLIGHT = "daylight"

SENSOR_DESC_TWILIGHT = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_TWILIGHT,
    name="Twilight",
    icon="mdi:weather-sunset",
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
)

SENSOR_DESC_DAYLIGHT = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_DAYLIGHT,
    name="Daylight",
    native_unit_of_measurement=LIGHT_LUX,
    icon="mdi:weather-sunny",
    device_class=SensorDeviceClass.ILLUMINANCE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
)

SENSOR_DESC_ILLUMINATION = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_ILLUMINANCE,
    name="Illuminance",
    native_unit_of_measurement=LIGHT_LUX,
    icon="mdi:sun-wireless-outline",
    device_class=SensorDeviceClass.ILLUMINANCE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
)

SENSOR_DESC_TEMPERATURE = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_TEMPERATURE,
    name="Temperature",
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    icon="mdi:thermometer",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
)

SENSOR_DESC_TARGET_TEMPERATURE = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_TARGET_TEMPERATURE,
    name="Target Temperature",
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    icon="mdi:thermometer",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
)

SENSOR_DESC_HUMIDITY = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_HUMIDITY,
    name="Humidity",
    native_unit_of_measurement=PERCENTAGE,
    icon="mdi:water-percent",
    device_class=SensorDeviceClass.HUMIDITY,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
)

SENSOR_DESC_VOLTAGE = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_VOLTAGE,
    name="voltage",
    native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    icon="mdi:sine-wave",
    device_class=SensorDeviceClass.VOLTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
)

SENSOR_DESC_PIR = EltakoSensorEntityDescription(
    key=SENSOR_TYPE_PIR,
    name="pir",
    native_unit_of_measurement=None,
    icon="mdi:home-outline",
    device_class=None,
    state_class=SensorStateClass.MEASUREMENT,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up an Eltako sensor device."""
    gateway: EnOceanGateway = get_gateway_from_hass(hass, config_entry)
    config: ConfigType = get_device_config_for_gateway(hass, config_entry, gateway)

    entities: list[EltakoEntity] = []
    
    platform = Platform.SENSOR
    if platform in config:
        for entity_config in config[platform]:
            try:
                dev_conf = DeviceConf(entity_config, [CONF_METER_TARIFFS])
                dev_name = dev_conf.name
                _area_start = len(entities)

                if dev_conf.eep in [A5_13_01]:
                    # N9: only fall back to the default when no name was configured.
                    # Was `dev_name == dev_conf.name` (always True) -> user names ignored.
                    if dev_name == "":
                        dev_name = DEFAULT_DEVICE_NAME_WEATHER_STATION
                    
                    entities.append(EltakoWeatherStation(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, SENSOR_DESC_WEATHER_STATION_ILLUMINANCE_DAWN))
                    entities.append(EltakoWeatherStation(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, SENSOR_DESC_WEATHER_STATION_TEMPERATURE))
                    entities.append(EltakoWeatherStation(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, SENSOR_DESC_WEATHER_STATION_WIND_SPEED))
                    entities.append(EltakoWeatherStation(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, SENSOR_DESC_WEATHER_STATION_RAIN))
                    entities.append(EltakoWeatherStation(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, SENSOR_DESC_WEATHER_STATION_ILLUMINANCE_WEST))
                    entities.append(EltakoWeatherStation(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, SENSOR_DESC_WEATHER_STATION_ILLUMINANCE_CENTRAL))
                    entities.append(EltakoWeatherStation(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, SENSOR_DESC_WEATHER_STATION_ILLUMINANCE_EAST))
                    
                elif dev_conf.eep in [F6_10_00]:
                    if dev_name == "":
                        dev_name = DEFAULT_DEVICE_NAME_WINDOW_HANDLE
                    
                    entities.append(EltakoWindowHandle(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, SENSOR_DESC_WINDOWHANDLE))
                    
                elif dev_conf.eep in [A5_12_01]:
                    if dev_name == "":
                        dev_name = DEFAULT_DEVICE_NAME_ELECTRICITY_METER
                    
                    for i, tariff in enumerate(dev_conf.get(CONF_METER_TARIFFS, [])):
                        # AS1: first tariff keeps the plain unique_id (backward compatible), extras get a suffix
                        entities.append(EltakoMeterSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, SENSOR_DESC_ELECTRICITY_CUMULATIVE, tariff=(tariff - 1), tariff_in_id=(i > 0)))
                    _tariff_in_name = dev_conf.get(CONF_METER_TARIFFS, []) != []
                    entities.append(EltakoMeterSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, SENSOR_DESC_ELECTRICITY_CURRENT, tariff=0, tariff_in_name=_tariff_in_name))

                elif dev_conf.eep in [A5_12_02]:
                    if dev_name == "":
                        dev_name = DEFAULT_DEVICE_NAME_GAS_METER
                        
                    for i, tariff in enumerate(dev_conf.get(CONF_METER_TARIFFS, [])):
                        # AS1: first tariff keeps the plain unique_id (backward compatible), extras get a suffix
                        entities.append(EltakoMeterSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, SENSOR_DESC_GAS_CUMULATIVE, tariff=(tariff - 1), tariff_in_id=(i > 0)))
                        entities.append(EltakoMeterSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, SENSOR_DESC_GAS_CURRENT, tariff=(tariff - 1), tariff_in_id=(i > 0)))

                elif dev_conf.eep in [A5_12_03]:
                    if dev_name == "":
                        dev_name = DEFAULT_DEVICE_NAME_WATER_METER
                        
                    for i, tariff in enumerate(dev_conf.get(CONF_METER_TARIFFS, [])):
                        # AS1: first tariff keeps the plain unique_id (backward compatible), extras get a suffix
                        entities.append(EltakoMeterSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, SENSOR_DESC_WATER_CUMULATIVE, tariff=(tariff - 1), tariff_in_id=(i > 0)))
                        entities.append(EltakoMeterSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, SENSOR_DESC_WATER_CURRENT, tariff=(tariff - 1), tariff_in_id=(i > 0)))

                elif dev_conf.eep in [A5_04_01, A5_04_02, A5_04_03, A5_10_12]:
                    
                    entities.append(EltakoTemperatureSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep))
                    entities.append(EltakoHumiditySensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep))
                    if dev_conf.eep in [A5_10_12]:
                        entities.append(EltakoTargetTemperatureSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep))

                elif dev_conf.eep in [A5_10_06, A5_10_03]:
                    entities.append(EltakoTemperatureSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep))
                    entities.append(EltakoTargetTemperatureSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep))
                
                elif dev_conf.eep in [A5_09_0C]:
                ### Eltako FLGTF only supports VOCT Total
                    # N9: .get() with the schema defaults so a config not run through the
                    # schema (or a future schema change) does not KeyError here.
                    voc_type_indexes = entity_config.get(CONF_VOC_TYPE_INDEXES, [0])
                    language = entity_config.get(CONF_LANGUAGE, "en")
                    for t in VOC_SubstancesType:
                        if t.index in voc_type_indexes:
                            entities.append(EltakoAirQualitySensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep, t, language))

                elif dev_conf.eep in [A5_07_01]:
                    entities.append(EltakoPirSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep))
                    entities.append(EltakoVoltageSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep))

                elif dev_conf.eep in [A5_08_01]:
                    entities.append(EltakoTemperatureSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep))
                    entities.append(EltakoIlluminationSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep))
                    entities.append(EltakoBatteryVoltageSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep))
                    # _pir_status => as binary sensor

                elif dev_conf.eep in [A5_06_01]:
                    # A5: illumination (combined) + twilight + daylight as separate sensors
                    entities.append(EltakoIlluminationSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep))
                    entities.append(EltakoTwilightSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep))
                    entities.append(EltakoDaylightSensor(platform, gateway, dev_conf.id, dev_name, dev_conf.eep))

                apply_area_to_entities(entities, _area_start, dev_conf)   # F1

            except Exception as e:
                LOGGER.warning("[%s] Could not load configuration", platform)
                LOGGER.critical(e, exc_info=True)

    # add labels for buttons
    if Platform.BINARY_SENSOR in config:
        for entity_config in config[Platform.BINARY_SENSOR]:
            try:
                dev_conf = DeviceConf(entity_config)
                if dev_conf.eep in [F6_01_01, F6_02_01, F6_02_02]:
                    def convert_event(event):
                        # M8: foreign/manually fired events with this event id may lack the key -> no KeyError
                        return config_helpers.button_abbreviation_to_str(event.data.get('pressed_buttons', []))

                    event_id = config_helpers.get_bus_event_type(gateway.dev_id, EVENT_BUTTON_PRESSED, dev_conf.id)
                    if dev_conf.eep in [F6_02_01, F6_02_02]:
                        entities.append(EventListenerInfoField(platform, gateway, dev_conf.id, dev_conf.name, dev_conf.eep, event_id, "Pushed Buttons", convert_event, "mdi:gesture-tap-button"))

                    entities.append(StaticInfoField(platform, gateway, dev_conf.id, dev_conf.name, dev_conf.eep, "Event Id", event_id, "mdi:form-textbox"))
            
            except Exception as e:
                LOGGER.warning("[%s] Could not load configuration", Platform.BINARY_SENSOR)
                LOGGER.critical(e, exc_info=True)

    # add id field for every device
    # A-r2: dedupe - the same device address listed in several platform sections
    # (a documented pattern, e.g. F6-10-00 as sensor + binary_sensor) produced two
    # 'Id' entities with identical unique_id -> HA dropped one with an error.
    seen_id_field_devices = set()
    for pl in PLATFORMS:
        if pl in config:
            for entity_config in config[pl]:
                try:
                    dev_conf = DeviceConf(entity_config)
                    if b2s(dev_conf.id[0]) in seen_id_field_devices:
                        continue
                    seen_id_field_devices.add(b2s(dev_conf.id[0]))
                    entities.append(StaticInfoField(platform, gateway, dev_conf.id, dev_conf.name, dev_conf.eep, "Id", b2s(dev_conf.id[0]), "mdi:identifier"))

                except Exception as e:
                    LOGGER.warning("[%s] Could not load configuration", Platform.BINARY_SENSOR)
                    LOGGER.critical(e, exc_info=True)


    # add gateway information
    entities.append(GatewayInfoField(platform, gateway, "Id", str(gateway.dev_id), "mdi:identifier"))
    
    if gateway.dev_type is not GatewayDeviceType.VirtualNetworkAdapter:
        entities.append(GatewayBaseId(platform, gateway))

    if GatewayDeviceType.is_lan_gateway(gateway.dev_type):
        entities.append(GatewayInfoField(platform, gateway, "Address", f"{gateway.serial_path}:{gateway.port}", "mdi:usb"))
    else:
        entities.append(GatewayInfoField(platform, gateway, "Serial Path", gateway.serial_path, "mdi:usb"))
        entities.append(GatewayInfoField(platform, gateway, "Message Delay", gateway.message_delay, "mdi:av-timer"))
        
    entities.append(GatewayInfoField(platform, gateway, "USB Protocol", gateway.native_protocol, "mdi:usb"))
    entities.append(GatewayInfoField(platform, gateway, "Auto Connect Enabled", gateway.is_auto_reconnect_enabled, "mdi:connection"))
    entities.append(GatewayLastReceivedMessage(platform, gateway))
    entities.append(GatewayReceivedMessagesInActiveSession(platform, gateway))

    validate_actuators_dev_and_sender_id(entities)
    log_entities_to_be_added(entities, platform)
    async_add_entities(entities)


class EltakoSensor(EltakoEntity, RestoreEntity, SensorEntity):
    """Representation of an  Eltako sensor device such as a power meter."""

    def __init__(self, platform: str, gateway: EnOceanGateway,
                 dev_id: AddressExpression, dev_name: str, dev_eep: EEP, description: EltakoSensorEntityDescription,
                 description_key: str = None
    ) -> None:
        """Initialize the Eltako sensor device.

        description_key (AS1): overrides the unique_id suffix. Defaults to the
        entity_description.key; a subclass passes an explicit key when several
        entities of one device share a description but must have distinct unique_ids
        (e.g. per-tariff meters)."""
        self.entity_description = description
        self._attr_state_class = description.state_class

        super().__init__(platform, gateway, dev_id, dev_name, dev_eep, description_key=description_key)
        self._attr_native_value = None
        
    @property
    def name(self):
        """Return the default name for the sensor."""
        return self.entity_description.name

    def load_value_initially(self, latest_state:State):
        LOGGER.debug(f"[{self._attr_ha_platform} {self.dev_id}] eneity unique_id: {self.unique_id}")
        LOGGER.debug(f"[{self._attr_ha_platform} {self.dev_id}] latest state - state: {latest_state.state}")
        LOGGER.debug(f"[{self._attr_ha_platform} {self.dev_id}] latest state - attributes: {latest_state.attributes}")
        # H2: robust restore parsing. Never raise here - an exception propagates out of
        # async_added_to_hass and prevents the entity from being added ("Error adding entity").
        try:
            state_str = latest_state.state
            # unknown/unavailable/None => no restored value
            if state_str in ('unknown', 'unavailable', None):
                self._attr_native_value = None
            else:
                state_class = latest_state.attributes.get('state_class', None)
                device_class = latest_state.attributes.get('device_class', None)
                if state_class in ('measurement', 'total_increasing', 'total'):
                    # accept both '.' and ',' as decimal separator (meter values are stored
                    # rounded, e.g. '123.45' -> int() would raise). Keep genuine integers as
                    # int so the restored state string matches the live one ('42', not '42.0').
                    num = float(state_str.replace(',', '.'))
                    if num.is_integer() and '.' not in state_str and ',' not in state_str:
                        self._attr_native_value = int(num)
                    else:
                        self._attr_native_value = num
                elif device_class == 'timestamp':
                    # e.g.: 2024-02-12T23:32:44+00:00
                    self._attr_native_value = datetime.fromisoformat(state_str)
                else:
                    # fall back to the raw string (e.g. enum/text sensors)
                    self._attr_native_value = state_str

        except Exception as e:
            LOGGER.warning("[%s %s] Could not restore last state '%s': %s", self._attr_ha_platform, self.dev_id, latest_state.state, str(e))
            self._attr_native_value = None

        self.schedule_update_ha_state()

        LOGGER.debug(f"[{self._attr_ha_platform} {self.dev_id} ({type(self).__name__})] value initially loaded: [native_value: {self.native_value}, state: {self.state}]")        

class EltakoPirSensor(EltakoSensor):
    """Occupancy Sensor"""

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name:str, dev_eep:EEP, description: EltakoSensorEntityDescription=SENSOR_DESC_PIR) -> None:
        """Initialize the Eltako meter sensor device."""
        super().__init__(platform, gateway, dev_id, dev_name, dev_eep, description)

    def value_changed(self, msg: ESP2Message):
        """Update the internal state of the sensor."""
        try:
            decoded:A5_07_01 = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("[Motion Sensor %s] Could not decode message: %s", self.dev_id, str(e))
            return
        
        self._attr_native_value = decoded.pir_status

        self.schedule_update_ha_state()


class EltakoVoltageSensor(EltakoSensor):
    """Voltage Sensor"""

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name:str, dev_eep:EEP, description: EltakoSensorEntityDescription=SENSOR_DESC_VOLTAGE) -> None:
        """Initialize the Eltako meter sensor device."""
        super().__init__(platform, gateway, dev_id, dev_name, dev_eep, description)

    def value_changed(self, msg: ESP2Message):
        """Update the internal state of the sensor."""
        try:
            decoded:A5_07_01 = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("[Voltage Sensor %s] Could not decode message: %s", self.dev_id, str(e))
            return

        # A-r2: A5-07-01 declares in data[3] bit 0 whether the voltage field is valid;
        # devices without voltage measurement otherwise produced fabricated readings.
        # (getattr: the eltakobus attribute name contains a typo, guard against a future rename)
        if not getattr(decoded, 'support_volrage_availability', getattr(decoded, 'support_voltage_availability', 1)):
            return

        self._attr_native_value = decoded.support_voltage

        self.schedule_update_ha_state()


class EltakoMeterSensor(EltakoSensor):
    """Representation of an Eltako electricity sensor.

    EEPs (EnOcean Equipment Profiles):
    - A5-12-01 (Automated Meter Reading, Electricity)
    - A5-12-02 (Automated Meter Reading, Gas)
    - A5-12-03 (Automated Meter Reading, Water)
    """
    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name:str, dev_eep:EEP, description: EltakoSensorEntityDescription, *, tariff, tariff_in_name:bool=True, tariff_in_id:bool=False) -> None:
        """Initialize the Eltako meter sensor device.

        AS1: several tariffs of one meter share a description (e.g. 'electricity
        cumulative'), so without the tariff in the unique_id they collided and every
        tariff after the first was silently dropped. `tariff_in_id` appends the tariff
        to the unique_id. It is set for every tariff EXCEPT the first configured one,
        so the pre-existing entity (always the first tariff) keeps its unique_id and
        history - no entity-registry migration is needed - while additional tariffs
        (which never got created before) come up as new entities."""
        _key = description.key
        if tariff_in_id:
            _key = f"{description.key}_tariff_{tariff + 1}"
        super().__init__(platform, gateway, dev_id, dev_name, dev_eep, description, description_key=_key)
        self._tariff = tariff
        self._tariff_in_name = tariff_in_name

    @property
    def name(self):
        """Return the default name for the sensor."""
        if self._tariff_in_name:
            return f"{self.entity_description.name} (Tariff {self._tariff + 1})"
        else:
            return f"{self.entity_description.name}"

    def value_changed(self, msg: ESP2Message):
        """Update the internal state of the sensor.
        For cumulative values, we alway respect the channel.
        For current values, we respect the channel just for gas and water.
        """
        try:
            decoded = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("[Meter Sensor %s] Could not decode message: %s", self.dev_id, str(e))
            return
        
        if decoded.learn_button != 1:
            return
        
        tariff = decoded.measurement_channel
        cumulative = not decoded.data_type
        value = decoded.meter_reading
        divisor = 10 ** decoded.divisor
        calculatedValue = value / divisor
        
        if cumulative and self._tariff == tariff and (
            self.entity_description.key == SENSOR_TYPE_ELECTRICITY_CUMULATIVE or
            self.entity_description.key == SENSOR_TYPE_GAS_CUMULATIVE or
            self.entity_description.key == SENSOR_TYPE_WATER_CUMULATIVE):
            self._attr_native_value = round(calculatedValue, 2)
            self.schedule_update_ha_state()
        elif (not cumulative) and len(msg.data) > 3 and msg.data[3] != 0x8F and self.entity_description.key == SENSOR_TYPE_ELECTRICITY_CURRENT: # N9: guard index; 0x8F means it's sending the serial number of the meter
            self._attr_native_value = round(calculatedValue, 2)
            self.schedule_update_ha_state()
        elif (not cumulative) and len(msg.data) > 3 and msg.data[3] != 0x8F and self._tariff == tariff and (
            # A-r2: same 0x8F serial-number guard as the electricity branch - a serial
            # telegram whose channel nibble matches the tariff was decoded as flow rate
            self.entity_description.key == SENSOR_TYPE_GAS_CURRENT or
            self.entity_description.key == SENSOR_TYPE_WATER_CURRENT):
            # l/s -> m3/h
            self._attr_native_value = round(calculatedValue * 3.6, 2)
            self.schedule_update_ha_state()


class EltakoWindowHandle(EltakoSensor):
    """Representation of an Eltako window handle device.

    EEPs (EnOcean Equipment Profiles):
    - F6-10-00 (Mechanical handle / Hoppe AG)
    """

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name: str, dev_eep: EEP, description: EltakoSensorEntityDescription) -> None:
        """Initialize the Eltako window handle sensor device."""
        super().__init__(platform, gateway, dev_id, dev_name, dev_eep, description)

    def value_changed(self, msg: ESP2Message):
        """Update the internal state of the sensor."""
        try:
            decoded:F6_10_00 = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("[Window Handle Sensor %s] Could not decode message: %s", self.dev_id, str(e))
            return
        
        if decoded.handle_position == WindowHandlePosition.CLOSED:
            self._attr_native_value = STATE_CLOSED
        elif decoded.handle_position == WindowHandlePosition.OPEN:
            self._attr_native_value = STATE_OPEN
        elif decoded.handle_position == WindowHandlePosition.TILT:
            self._attr_native_value = "tilt"
        else:
            return

        self.schedule_update_ha_state()


class EltakoWeatherStation(EltakoSensor):
    """Representation of an Eltako weather station.
    
    EEPs (EnOcean Equipment Profiles):
    - A5-13-01 (Weather station)
    """

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name: str, dev_eep: EEP, description: EltakoSensorEntityDescription) -> None:
        """Initialize the Eltako weather station device."""
        super().__init__(platform, gateway, dev_id, dev_name, dev_eep, description)

    def value_changed(self, msg: ESP2Message):
        """Update the internal state of the sensor."""
        try:
            decoded = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("[Weather Station %s] Could not decode message: %s", self.dev_id, str(e))
            return
        
        if decoded.learn_button != 1:
            return
        
        if msg.data == bytes((0, 0, 0xFF, 0x1A)): # I don't really know why this is filtered out
            return

        if self.entity_description.key == SENSOR_TYPE_WEATHER_STATION_ILLUMINANCE_DAWN:
            if decoded.identifier != 0x01:
                return
            
            self._attr_native_value = decoded.dawn_sensor
        elif self.entity_description.key == SENSOR_TYPE_WEATHER_STATION_TEMPERATURE:
            if decoded.identifier != 0x01:
                return
            
            self._attr_native_value = decoded.temperature
        elif self.entity_description.key == SENSOR_TYPE_WEATHER_STATION_WIND_SPEED:
            if decoded.identifier != 0x01:
                return
            
            self._attr_native_value = decoded.wind_speed
        elif self.entity_description.key == SENSOR_TYPE_WEATHER_STATION_RAIN:
            if decoded.identifier != 0x01:
                return
            
            self._attr_native_value = decoded.rain_indication
        elif self.entity_description.key == SENSOR_TYPE_WEATHER_STATION_ILLUMINANCE_WEST:
            if decoded.identifier != 0x02:
                return
            
            self._attr_native_value = decoded.sun_west * 1000.0
        elif self.entity_description.key == SENSOR_TYPE_WEATHER_STATION_ILLUMINANCE_CENTRAL:
            if decoded.identifier != 0x02:
                return
            
            self._attr_native_value = decoded.sun_south * 1000.0
        elif self.entity_description.key == SENSOR_TYPE_WEATHER_STATION_ILLUMINANCE_EAST:
            if decoded.identifier != 0x02:
                return
            
            self._attr_native_value = decoded.sun_east * 1000.0

        self.schedule_update_ha_state()


class EltakoTemperatureSensor(EltakoSensor):
    """Representation of an Eltako temperature sensor.
    
    EEPs (EnOcean Equipment Profiles):
    - A5-04-02 (Temperature and Humidity)
    """

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name: str, dev_eep: EEP, description: EltakoSensorEntityDescription=SENSOR_DESC_TEMPERATURE) -> None:
        """Initialize the Eltako temperature sensor."""
        _dev_name = dev_name
        if _dev_name == "":
            _dev_name = DEFAULT_DEVICE_NAME_THERMOMETER
        super().__init__(platform, gateway, dev_id, _dev_name, dev_eep, description)

    def value_changed(self, msg: ESP2Message):
        """Update the internal state of the sensor."""
        try:
            decoded = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("[Temperature Sensor %s] Could not decode message: %s", self.dev_id, str(e))
            return
        
        self._attr_native_value = decoded.current_temperature

        self.schedule_update_ha_state()

class EltakoIlluminationSensor(EltakoSensor):
    """Brightness sensor"""

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name: str, dev_eep: EEP, description: EltakoSensorEntityDescription=SENSOR_DESC_ILLUMINATION) -> None:
        """Initialize the Eltako temperature sensor."""
        _dev_name = dev_name
        if _dev_name == "":
            _dev_name = DEFAULT_DEVICE_NAME_THERMOMETER
        super().__init__(platform, gateway, dev_id, _dev_name, dev_eep, description)

    def value_changed(self, msg: ESP2Message):
        """Update the internal state of the sensor."""
        try:
            decoded = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("[Illumination Sensor %s] Could not decode message: %s", self.dev_id, str(e))
            return
        
        self._attr_native_value = decoded.illumination

        self.schedule_update_ha_state()


class EltakoTwilightSensor(EltakoSensor):
    """A5-06-01 twilight value (raw 0-255)."""

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name: str, dev_eep: EEP, description: EltakoSensorEntityDescription=SENSOR_DESC_TWILIGHT) -> None:
        _dev_name = dev_name or DEFAULT_DEVICE_NAME_THERMOMETER
        super().__init__(platform, gateway, dev_id, _dev_name, dev_eep, description)

    def value_changed(self, msg: ESP2Message):
        try:
            decoded = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("[Twilight Sensor %s] Could not decode message: %s", self.dev_id, str(e))
            return
        self._attr_native_value = decoded.twilight
        self.schedule_update_ha_state()


class EltakoDaylightSensor(EltakoSensor):
    """A5-06-01 daylight illuminance (lux)."""

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name: str, dev_eep: EEP, description: EltakoSensorEntityDescription=SENSOR_DESC_DAYLIGHT) -> None:
        _dev_name = dev_name or DEFAULT_DEVICE_NAME_THERMOMETER
        super().__init__(platform, gateway, dev_id, _dev_name, dev_eep, description)

    def value_changed(self, msg: ESP2Message):
        try:
            decoded = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("[Daylight Sensor %s] Could not decode message: %s", self.dev_id, str(e))
            return
        self._attr_native_value = decoded.day_light
        self.schedule_update_ha_state()


class EltakoBatteryVoltageSensor(EltakoSensor):
    """Representation of an Eltako battery sensor."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC   # A3

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name: str, dev_eep: EEP, description: EltakoSensorEntityDescription=SENSOR_DESC_BATTERY_VOLTAGE) -> None:
        """Initialize the Eltako temperature sensor."""
        _dev_name = dev_name
        if _dev_name == "":
            _dev_name = "Battery Sensor"
        super().__init__(platform, gateway, dev_id, _dev_name, dev_eep, description)

    def value_changed(self, msg: ESP2Message):
        """Update the internal state of the sensor."""
        try:
            decoded = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("[Battery Voltage Sensor %s] Could not decode message: %s", self.dev_id, str(e))
            return
        
        self._attr_native_value = decoded.supply_voltage

        self.schedule_update_ha_state()


class EltakoTargetTemperatureSensor(EltakoSensor):
    """Representation of an Eltako target temperature sensor.
    
    EEPs (EnOcean Equipment Profiles):
    - A5-10-06, A5-10-12
    """

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name: str, dev_eep: EEP, description: EltakoSensorEntityDescription=SENSOR_DESC_TARGET_TEMPERATURE) -> None:
        """Initialize the Eltako temperature sensor."""
        _dev_name = dev_name
        if _dev_name == "":
            _dev_name = DEFAULT_DEVICE_NAME_THERMOMETER
        super().__init__(platform, gateway, dev_id, _dev_name, dev_eep, description)
    
    def value_changed(self, msg: ESP2Message):
        """Update the internal state of the sensor."""
        try:
            decoded = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("[Target Temperature Sensor %s] Could not decode message: %s", self.dev_id, str(e))
            return
        
        self._attr_native_value = round(2 * decoded.target_temperature, 0) / 2

        self.schedule_update_ha_state()


class EltakoHumiditySensor(EltakoSensor):
    """Representation of an Eltako humidity sensor.
    
    EEPs (EnOcean Equipment Profiles):
    - A5-04-02 (Temperature and Humidity)
    """

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name:str, dev_eep: EEP, description: EltakoSensorEntityDescription=SENSOR_DESC_HUMIDITY) -> None:
        """Initialize the Eltako humidity sensor."""
        _dev_name = dev_name
        if _dev_name == "":
            _dev_name = DEFAULT_DEVICE_NAME_HYGROSTAT
        super().__init__(platform, gateway, dev_id, _dev_name, dev_eep, description)
    
    def value_changed(self, msg: ESP2Message):
        """Update the internal state of the sensor."""
        try:
            decoded = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("[Humidity Sensor %s] Could not decode message: %s", self.dev_id, str(e))
            return
        
        self._attr_native_value = decoded.humidity

        self.schedule_update_ha_state()

class EltakoAirQualitySensor(EltakoSensor):
    """Representation of an Eltako air quality sensor.
    
    EEPs (EnOcean Equipment Profiles):
    - A5-09-0C
    """

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name: str, dev_eep: EEP, voc_type:VOC_SubstancesType, language:LANGUAGE_ABBREVIATION) -> None:
        """Initialize the Eltako air quality sensor."""
        _dev_name = dev_name
        if _dev_name == "":
            _dev_name = DEFAULT_DEVICE_NAME_THERMOMETER

        # AS3: the unique_id must be language-INDEPENDENT - the localized substance name
        # in the id orphaned the entity (and its statistics) on a HA language switch.
        # Key off the English name (stable, and byte-identical to what English installs
        # already have -> no migration for them); keep the DISPLAY name localized.
        stable_key_name = voc_type.name_en
        self.voc_type_name = voc_type.name_de if language == LANGUAGE_ABBREVIATION.LANG_GERMAN else voc_type.name_en

        # AS2: derive the device_class from the substance's unit. Only the VOC *total*
        # carries a unit (ppb); the individual substances report a unitless index, for
        # which HA rejects a VOC device_class (wrong-unit long-term-statistics error).
        # ppb pairs with VOLATILE_ORGANIC_COMPOUNDS_PARTS (NOT ...COMPOUNDS = µg/m³).
        unit = voc_type.unit or None
        device_class = SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS if unit else None

        description = EltakoSensorEntityDescription(
            key = "air_quality_sensor_" + stable_key_name,
            device_class = device_class,
            name = self.voc_type_name,
            native_unit_of_measurement = unit,
            icon="mdi:air-filter",
            state_class=SensorStateClass.MEASUREMENT,
        )

        super().__init__(platform, gateway, dev_id, _dev_name, dev_eep, description)
        self.voc_type = voc_type

        LOGGER.debug(f"entity_description: {self.entity_description}, voc_type: {voc_type}")
    
    
    def value_changed(self, msg: ESP2Message):
        """Update the internal state of the sensor."""
        try:
            decoded = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("[Air Quality Sensor %s] Could not decode message: %s", self.dev_id, str(e))
            return
        
        # A-r2: eltakobus leaves voc_type as None for VOC codes not in VOC_SubstancesType
        # (gap at 21, everything between 36 and 254) - dereferencing raised AttributeError
        # and produced a full traceback per telegram.
        if decoded.voc_type is None:
            LOGGER.debug("[Air Quality Sensor %s] Ignoring telegram with unknown VOC substance type.", self.dev_id)
            return

        if decoded.voc_type.index == self.voc_type.index:
            # LOGGER.debug(f"[EltakoAirQualitySensor] received message - concentration: {decoded.concentration}, voc_type: {decoded.voc_type}, voc_unit: {decoded.voc_unit}")
            self._attr_native_value = decoded.concentration

        self.schedule_update_ha_state()

class GatewayLastReceivedMessage(EltakoSensor):
    """Protocols last time when message received"""
    _attr_entity_category = EntityCategory.DIAGNOSTIC   # A3
    _attr_follow_gateway_availability = False   # B1: gateway diagnostic, stays visible when disconnected

    def __init__(self, platform: str, gateway: EnOceanGateway):
        super().__init__(platform, gateway,
                         dev_id=AddressExpression.parse('00-00-00-00'), 
                         dev_name="Last Message Received", 
                         dev_eep=None,
                         description=EltakoSensorEntityDescription(
                            key="Last Message Received",
                            name="Last Message Received",
                            icon="mdi:message-check-outline",
                            device_class=SensorDeviceClass.TIMESTAMP,
                            has_entity_name= True,
                        )
        )
        self._attr_name = "Last Message Received"

    async def async_added_to_hass(self) -> None:
        # H7: register in async_added_to_hass and deregister on removal (was in __init__ -> leaked on reload)
        await super().async_added_to_hass()
        self.gateway.set_last_message_received_handler(self.async_value_changed)
        self.async_on_remove(lambda: self.gateway.remove_last_message_received_handler(self.async_value_changed))

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.gateway.serial_path)},
            name= self.gateway.dev_name,
            manufacturer=MANUFACTURER,
            model=self.gateway.model,
            via_device=(DOMAIN, self.gateway.serial_path)
        )

    async def async_value_changed(self, value: datetime) -> None:
        try:
            self.value_changed(value)
        except AttributeError:
            # Home Assistant not ready yet
            pass

    def value_changed(self, value: datetime) -> None:
        """Update the current value."""
        # LOGGER.debug("[%s] Last message received", Platform.SENSOR)

        if isinstance(value, datetime):
            self._attr_native_value = value
            self.schedule_update_ha_state()

class GatewayReceivedMessagesInActiveSession(EltakoSensor):
    """Protocols amount of messages per session"""
    _attr_entity_category = EntityCategory.DIAGNOSTIC   # A3
    _attr_follow_gateway_availability = False   # B1: gateway diagnostic, stays visible when disconnected

    def __init__(self, platform: str, gateway: EnOceanGateway):
        super().__init__(platform, gateway,
                         dev_id=AddressExpression.parse('00-00-00-00'), 
                         dev_name="Received Messages per Session", 
                         dev_eep=None,
                         description=EltakoSensorEntityDescription(
                            key="Received Messages per Session",
                            name="Received Messages per Session",
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            # device_class=SensorDeviceClass.VOLUME,
                            # native_unit_of_measurement="Messages", # => raises error message
                            unit_of_measurement="count",
                            suggested_unit_of_measurement="Messages",
                            icon="mdi:chart-line",
                        )
        )
        self._attr_name="Received Messages per Session"

    async def async_added_to_hass(self) -> None:
        # H7: register in async_added_to_hass and deregister on removal (was in __init__ -> leaked on reload)
        await super().async_added_to_hass()
        self.gateway.set_received_message_count_handler(self.async_value_changed)
        self.async_on_remove(lambda: self.gateway.remove_received_message_count_handler(self.async_value_changed))

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.gateway.serial_path)},
            name= self.gateway.dev_name,
            manufacturer=MANUFACTURER,
            model=self.gateway.model,
            via_device=(DOMAIN, self.gateway.serial_path)
        )

    async def async_value_changed(self, value: int) -> None:
        try:
            self.value_changed(value)
        except AttributeError:
            # Home Assistant not ready yet
            pass

    def value_changed(self, value: int) -> None:
        """Update the current value."""
        # LOGGER.debug("[%s] received amount of messages: %s", Platform.SENSOR, str(value))

        self._attr_native_value = value
        self.schedule_update_ha_state()


class GatewayBaseId(EltakoSensor):
    """"Displays base id of gateway."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC   # A3
    _attr_follow_gateway_availability = False   # B1: gateway diagnostic, stays visible when disconnected

    def __init__(self, platform: str, gateway: EnOceanGateway):
        super().__init__(platform, gateway,
                         dev_id=AddressExpression.parse('00-00-00-00'), 
                         dev_name="Base Id", 
                         dev_eep=None,
                         description=EltakoSensorEntityDescription(
                            key="Base Id",
                            name="Base Id",
                            icon="mdi:identifier",
                            has_entity_name= True,
                        ) )
        self._attr_name = "Base Id"

    async def async_added_to_hass(self) -> None:
        # H7: register in async_added_to_hass and deregister on removal (was in __init__ -> leaked on reload)
        await super().async_added_to_hass()
        self.gateway.add_base_id_change_handler(self.async_value_changed)
        self.async_on_remove(lambda: self.gateway.remove_base_id_change_handler(self.async_value_changed))
        # reflect the base id that is already known at add time
        if isinstance(self.gateway.base_id, AddressExpression):
            await self.async_value_changed(self.gateway.base_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.gateway.serial_path)},
            name= self.gateway.dev_name,
            manufacturer=MANUFACTURER,
            model=self.gateway.model,
            via_device=(DOMAIN, self.gateway.serial_path)
        )

    async def async_value_changed(self, base_id: AddressExpression) -> None:
        """Update the current value."""

        if isinstance(base_id, AddressExpression):
            self._attr_native_value = b2s(base_id)
            self.schedule_update_ha_state()


class StaticInfoField(EltakoSensor):
    """Key value fields - used for per-DEVICE diagnostics ("Id", "Event Id").
    The gateway-owned variant is the GatewayInfoField subclass below."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC   # A3 (also covers GatewayInfoField)
    # B1: NOTE - this base class is a DEVICE entity (dev_id = the real device), so it
    # follows gateway availability (default). Only GatewayInfoField opts out.

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name: str, dev_eep: EEP, key:str, value:str, icon:str=None):
        super().__init__(platform, gateway,
                         dev_id=dev_id, 
                         dev_name=dev_name, 
                         dev_eep=dev_eep,
                         description=EltakoSensorEntityDescription(
                            key=key,
                            name=key,
                            icon=icon,
                            has_entity_name= True,
                        )
        )
        self._attr_name = key
        self._attr_native_value = value

    def value_changed(self, value) -> None:
        pass

class VirtGWInfoField(SensorEntity):
    """Key value fields for gateway information"""

    def __init__(self, platform: str, virt_gw: VirtualNetworkGateway, key:str, value:str, icon:str=None):
        super().__init__()
        self.virt_gw = virt_gw

        self._attr_name = "Address"
        self._attr_native_value = "homeassistant.local:"+str(virt_gw.port)
        
    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return self.virt_gw


class GatewayInfoField(StaticInfoField):
    """Key value fields for gateway information"""
    # B1: gateway-owned (device_info points at the gateway) - stays visible while disconnected.
    _attr_follow_gateway_availability = False

    def __init__(self, platform: str, gateway: EnOceanGateway, key:str, value:str, icon:str=None):
        super().__init__(platform, 
                         gateway,
                         dev_id=AddressExpression.parse('00-00-00-00'),
                         dev_name=key, 
                         dev_eep=None,
                         key=key,
                         value=value,
                         icon=icon
                         )
        
    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.gateway.serial_path)},
            name= self.gateway.dev_name,
            manufacturer=MANUFACTURER,
            model=self.gateway.model,
            via_device=(DOMAIN, self.gateway.serial_path)
        )
        
class EventListenerInfoField(EltakoSensor):
    """Per-DEVICE "Pushed Buttons" field for wall switches (event-driven)."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC   # A3
    # B1: NOTE - device entity (dev_id = the real switch), so it FOLLOWS gateway
    # availability (default). It is NOT gateway-owned despite the legacy docstring.

    def __init__(self, platform: str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name: str, dev_eep: EEP, event_id: str, key:str, convert_event_function, icon:str=None):
        super().__init__(platform, gateway,
                         dev_id=dev_id, 
                         dev_name=dev_name, 
                         dev_eep=dev_eep,
                         description=EltakoSensorEntityDescription(
                            key=key,
                            name=key,
                            icon=icon,
                            has_entity_name= True,
                        )
        )
        self.convert_event_function = convert_event_function
        self._attr_name = key
        self._attr_native_value = ''
        self.listen_to_addresses.clear()
        self._event_id = event_id

    async def async_added_to_hass(self) -> None:
        # H7: async_listen on the GLOBAL hass.bus was called in __init__ and never removed
        # -> leaked on every reload and fired on dead entities. Register here + async_on_remove.
        await super().async_added_to_hass()
        LOGGER.debug(f"[{self._attr_ha_platform}] [{EventListenerInfoField.__name__}] [{self.dev_name}] Register event: {self._event_id}")
        self.async_on_remove(
            self.hass.bus.async_listen(self._event_id, self.value_changed)
        )

    def value_changed(self, event) -> None:
        LOGGER.debug(f"Received event: {event}")
        self._attr_native_value = self.convert_event_function(event)

        self.schedule_update_ha_state()
            