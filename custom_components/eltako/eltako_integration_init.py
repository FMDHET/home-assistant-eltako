"""Support for Eltako devices."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.helpers.typing import ConfigType
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, device_registry as dr, entity_platform as pl

from .const import *
from .schema import CONFIG_SCHEMA
from . import config_helpers
from .gateway import *

LOG_PREFIX = "Eltako Integration Setup"

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Eltako component."""
    return True

def print_config_entry(config_entry: ConfigEntry) -> None:
    LOGGER.debug("ConfigEntry")
    LOGGER.debug("- tilte: %s", config_entry.title)
    LOGGER.debug("- domain: %s", config_entry.domain)
    LOGGER.debug("- unique_id: %s", config_entry.unique_id)
    LOGGER.debug("- version: %s", config_entry.version)
    LOGGER.debug("- entry_id: %s", config_entry.entry_id)
    LOGGER.debug("- state: %s", config_entry.state)
    for k in config_entry.data.keys():
        LOGGER.debug("- data %s - %s", k, config_entry.data.get(k, ''))

# relevant for higher than v.1.3.4: removed 'ESP2' from GATEWAY_DEFAULT_NAME which is still in OLD_GATEWAY_DEFAULT_NAME
def migrate_old_gateway_descriptions(hass: HomeAssistant):
    LOGGER.debug(f"[{LOG_PREFIX}] Provide new and old gateway descriptions/id for smooth version upgrades.")
    migration_dict:dict = {}
    for key in hass.data[DATA_ELTAKO].keys():
        # LOGGER.debug(f"[{LOG_PREFIX}] Check description: {key}")
        if GATEWAY_DEFAULT_NAME in key:
            old_key = key.replace(GATEWAY_DEFAULT_NAME, OLD_GATEWAY_DEFAULT_NAME)
            LOGGER.info(f"[{LOG_PREFIX}] Support downwards compatibility => from new gatewy description '{key}' to old description '{old_key}'")
            migration_dict[old_key] = hass.data[DATA_ELTAKO][key]
            # del hass.data[DATA_ELTAKO][key]
        if OLD_GATEWAY_DEFAULT_NAME in key:
            new_key = key.replace(OLD_GATEWAY_DEFAULT_NAME, GATEWAY_DEFAULT_NAME)
            LOGGER.info(f"[{LOG_PREFIX}] Migrate gatewy from old description '{key}' to new description '{new_key}'")
            migration_dict[new_key] = hass.data[DATA_ELTAKO][key]
    # prvide either new or old key in parallel
    for key in migration_dict:
        hass.data[DATA_ELTAKO][key] = migration_dict[key]


def get_gateway_from_hass(hass: HomeAssistant, config_entry: ConfigEntry) -> EnOceanGateway:

    # Migrage existing gateway configs / ESP2 was removed in the name
    migrate_old_gateway_descriptions(hass)

    return hass.data[DATA_ELTAKO][config_entry.data[CONF_GATEWAY_DESCRIPTION]]


def set_gateway_to_hass(hass: HomeAssistant, gateway_enity: EnOceanGateway) -> None:

    # Migrage existing gateway configs / ESP2 was removed in the name
    migrate_old_gateway_descriptions(hass)

    hass.data[DATA_ELTAKO][gateway_enity.dev_name] = gateway_enity

def get_device_config_for_gateway(hass: HomeAssistant, config_entry: ConfigEntry, gateway: EnOceanGateway) -> ConfigType:
    return config_helpers.get_device_config(hass.data[DATA_ELTAKO][ELTAKO_CONFIG], gateway.dev_id)


def cleanup_unavailable_entities(hass: HomeAssistant):
    # for p in pl.async_get_platforms(hass, DOMAIN):
    # cur_pl = pl.async_get_current_platform()
    # for e in cur_pl.entities:
    #     LOGGER.debug(f"ENTITY {e} IN PLATFORM {cur_pl.platform_name} {cur_pl.}")

    device_reg = dr.async_get(hass)
    for key, d in device_reg.devices.items():
        LOGGER.debug(f"DEVICE ===>>> key: {key}, id: {d.id}, name: {d.name}, area id: {d.area_id} domain: {d.identifiers[0][0]}")

    entity_registry = er.async_get(hass)
    for key, e in entity_registry.entities.items():
        if DOMAIN == e.platform:
            LOGGER.debug(f"ENTITY ===>>> key: {key}, id: {e.entity_id}, name: {e.name}, platform: {e.platform}, domain: {e.domain}")


    for e in hass.config_entries.async_entries():
        LOGGER.debug(f"CONFIG ENTRIES: entry_id {e.entry_id}, unique_id: {e.unique_id}")

    # dr.async_cleanup(hass, device_reg, entity_registry)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up an Eltako gateway for the given entry."""
    LOGGER.info(f"[{LOG_PREFIX}] Start gateway setup.")
    # print_config_entry(config_entry)

    # Check domain
    if config_entry.domain != DOMAIN:
        raise Exception(f"[{LOG_PREFIX}] Ooops, received configuration entry of wrong domain '%s' (expected: '')!", config_entry.domain, DOMAIN)

    
    # Read the config
    config = await config_helpers.async_get_home_assistant_config(hass, CONFIG_SCHEMA)

    # Check if gateway ids are unique
    if not config_helpers.config_check_gateway(config):
        raise Exception("Gateway Ids are not unique.")


    # set config for global access
    eltako_data = hass.data.setdefault(DATA_ELTAKO, {})
    eltako_data[ELTAKO_CONFIG] = config
    # print whole eltako configuration
    LOGGER.debug(f"config: {config}\n")

    # Migrage existing gateway configs / ESP2 was removed in the name
    migrate_old_gateway_descriptions(hass)

    general_settings = config_helpers.get_general_settings_from_configuration(hass)
    # Initialise the gateway
    # get base_id from user input
    if CONF_GATEWAY_DESCRIPTION not in config_entry.data.keys():
        raise Exception("[{LOG_PREFIX}] Ooops, device information for gateway is not available. Try to delete and recreate the gateway.")
    gateway_description = config_entry.data[CONF_GATEWAY_DESCRIPTION]    # from user input
    if not ('(' in gateway_description and ')' in gateway_description):
        raise Exception("[{LOG_PREFIX}] Ooops, no base id of gateway available. Try to delete and recreate the gateway.")
    gateway_id = config_helpers.get_id_from_name(gateway_description)
    
    # get home assistant configuration section matching base_id
    gateway_config = await config_helpers.async_find_gateway_config_by_id(gateway_id, hass, CONFIG_SCHEMA)
    if not gateway_config:
        raise Exception(f"[{LOG_PREFIX}] Ooops, no gateway configuration found in '/homeassistant/configuration.yaml'.")
    
    # get serial path info
    if CONF_SERIAL_PATH not in config_entry.data.keys():
        raise Exception("[{LOG_PREFIX}] Ooops, no information about serial path available for gateway.")
    gateway_serial_path = config_entry.data[CONF_SERIAL_PATH]

    # only transceiver can send teach-in telegrams
    gateway_device_type = GatewayDeviceType.find(gateway_config[CONF_DEVICE_TYPE])    # from configuration
    if gateway_device_type is None:
        LOGGER.error(f"[{LOG_PREFIX}] USB device {gateway_config[CONF_DEVICE_TYPE]} is not supported!!!")
        return False
    general_settings[CONF_ENABLE_TEACH_IN_BUTTONS] = GatewayDeviceType.is_transceiver(gateway_device_type)

    LOGGER.info(f"[{LOG_PREFIX}] Initializes Gateway Device '{gateway_description}'")
    gateway_name = gateway_config.get(CONF_NAME, None)  # from configuration
    baud_rate= BAUD_RATE_DEVICE_TYPE_MAPPING[gateway_device_type]
    gateway_base_id = AddressExpression.parse(gateway_config[CONF_BASE_ID])
    LOGGER.debug(f"id: {gateway_id}, device type: {gateway_device_type}, serial path: {gateway_serial_path}, baud rate: {baud_rate}, base id: {gateway_base_id}")
    usb_gateway = EnOceanGateway(general_settings, hass, gateway_id, gateway_device_type, gateway_serial_path, baud_rate, gateway_base_id, gateway_name, config_entry)
    
    await usb_gateway.async_setup()
    set_gateway_to_hass(hass, usb_gateway)

    cleanup_unavailable_entities(hass)
    
    hass.data[DATA_ELTAKO][DATA_ENTITIES] = {}
    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(config_entry, platform)
        )

    return True

async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload Eltako config entry."""

    gateway = get_gateway_from_hass(hass, config_entry)

    LOGGER.info("Unload %s and all its supported devices!", gateway.dev_name)
    gateway.unload()
    del hass.data[DATA_ELTAKO][gateway.dev_name]

    return True
