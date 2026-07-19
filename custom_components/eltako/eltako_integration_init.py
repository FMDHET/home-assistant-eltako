"""Support for Eltako devices."""
# N10: removed dead imports (os, async_register_built_in_panel, panel_custom,
# websocket_api) that only served the commented-out frontend-panel code below.
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr


from .const import *
from .virtual_network_gateway import VirtualNetworkGateway, VIRT_GW_PORT
from .schema import CONFIG_SCHEMA
from . import config_helpers
from .gateway import *
# N10: dead 'from .frontend.info_page_view import InfoPageView' removed - the view
# was never registered (its panel/static registration is gone).

LOG_PREFIX_INIT = "Eltako Integration Setup"

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Eltako component."""
    LOGGER.info(f"[{LOG_PREFIX_INIT}] Initialize Home Assistant Eltako Integration: https://github.com/grimmpp/home-assistant-eltako")

    if DATA_ELTAKO not in hass.data:
        hass.data[DATA_ELTAKO] = {}

    # Migrage existing gateway configs / ESP2 was removed in the name
    migrate_old_gateway_descriptions(hass)

    LOGGER.info(f"[{LOG_PREFIX_INIT}] Eltako Integration initiallized. ... loading device configuration")

    return True

def print_config_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    LOGGER.debug("ConfigEntry")
    LOGGER.debug("- tilte: %s", config_entry.title)
    LOGGER.debug("- domain: %s", config_entry.domain)
    LOGGER.debug("- unique_id: %s", config_entry.unique_id)
    LOGGER.debug("- version: %s", config_entry.version)
    LOGGER.debug("- entry_id: %s", config_entry.entry_id)
    LOGGER.debug("- state: %s", config_entry.state)
    for k in config_entry.data.keys():
        LOGGER.debug("- data %s - %s", k, config_entry.data.get(k, ''))

    if DATA_ELTAKO in hass.data:
        LOGGER.debug("Available Eltako Objects")
        for g in hass.data[DATA_ELTAKO]:
            LOGGER.debug(g)

# relevant for higher than v.1.3.4: removed 'ESP2' from GATEWAY_DEFAULT_NAME which is still in OLD_GATEWAY_DEFAULT_NAME
def migrate_old_gateway_descriptions(hass: HomeAssistant):
    LOGGER.debug(f"[{LOG_PREFIX_INIT}] Provide new and old gateway descriptions/id for smooth version upgrades.")
    migration_dict:dict = {}
    if DATA_ELTAKO in hass.data:
        for key in hass.data[DATA_ELTAKO].keys():
            # LOGGER.debug(f"[{LOG_PREFIX}] Check description: {key}")
            if GATEWAY_DEFAULT_NAME in key:
                old_key = key.replace(GATEWAY_DEFAULT_NAME, OLD_GATEWAY_DEFAULT_NAME)
                LOGGER.info(f"[{LOG_PREFIX_INIT}] Support downwards compatibility => from new gateway description '{key}' to old description '{old_key}'")
                migration_dict[old_key] = hass.data[DATA_ELTAKO][key]
                # del hass.data[DATA_ELTAKO][key]
            if OLD_GATEWAY_DEFAULT_NAME in key:
                new_key = key.replace(OLD_GATEWAY_DEFAULT_NAME, GATEWAY_DEFAULT_NAME)
                LOGGER.info(f"[{LOG_PREFIX_INIT}] Migrate gateway from old description '{key}' to new description '{new_key}'")
                migration_dict[new_key] = hass.data[DATA_ELTAKO][key]
        # prvide either new or old key in parallel
        for key in migration_dict:
            hass.data[DATA_ELTAKO][key] = migration_dict[key]


def get_gateway_from_hass(hass: HomeAssistant, config_entry: ConfigEntry) -> EnOceanGateway:

    g_id = "gateway_"+str(config_helpers.get_id_from_gateway_name(config_entry.data[CONF_GATEWAY_DESCRIPTION]))
    if g_id in hass.data[DATA_ELTAKO]:
        return hass.data[DATA_ELTAKO][g_id]
    else:
        return None


def set_gateway_to_hass(hass: HomeAssistant, gateway: EnOceanGateway) -> None:

    g_id = "gateway_"+str(gateway.dev_id)
    hass.data[DATA_ELTAKO][g_id] = gateway

async def async_unload_gateway(hass: HomeAssistant, config_entry: ConfigEntry) -> None:

    gateway:EnOceanGateway = get_gateway_from_hass(hass, config_entry)
    if gateway is not None:

        LOGGER.info(f"[{LOG_PREFIX_INIT}] Unload {gateway.dev_name} and all its supported devices!")

        # K3: remove the send-message service registered for this gateway
        service_name = config_helpers.get_bus_event_type(gateway_id=gateway.dev_id, function_id=SIGNAL_SEND_MESSAGE_SERVICE)
        if hass.services.has_service(DOMAIN, service_name):
            hass.services.async_remove(DOMAIN, service_name)

        # K2: stopping the gateway joins its bus thread => must not block the event loop
        await gateway.async_unload()

        gw_id = "gateway_"+str(gateway.dev_id)
        if gw_id in hass.data[DATA_ELTAKO]:
            del hass.data[DATA_ELTAKO][gw_id]
        # because of legacy
        if gateway.dev_name in hass.data[DATA_ELTAKO]:
            del hass.data[DATA_ELTAKO][gateway.dev_name]


def get_device_config_for_gateway(hass: HomeAssistant, config_entry: ConfigEntry, gateway: EnOceanGateway) -> ConfigType:
    return config_helpers.get_device_config(hass.data[DATA_ELTAKO][ELTAKO_CONFIG], gateway.dev_id)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up an Eltako gateway for the given entry."""
    LOGGER.info(f"[{LOG_PREFIX_INIT}] Start gateway setup.")
    print_config_entry(hass, config_entry)

    # Check domain
    if config_entry.domain != DOMAIN:
        LOGGER.warning(f"[{LOG_PREFIX_INIT}] Ooops, received configuration entry of wrong domain '{config_entry.domain}' (expected: '{DOMAIN}')!")
        return False


    # Read the config
    config = await config_helpers.async_get_home_assistant_config(hass, CONFIG_SCHEMA)

    # Check if gateway ids are unique
    # K6: ConfigEntryError/ConfigEntryNotReady instead of bare returns/Exceptions so that
    # HA shows a proper error message resp. retries the setup automatically.
    if not config_helpers.config_check_gateway(config):
        raise ConfigEntryError(f"[{LOG_PREFIX_INIT}] Gateway Ids are not unique.")


    # set config for global access
    eltako_data = hass.data.setdefault(DATA_ELTAKO, {})
    eltako_data[ELTAKO_CONFIG] = config
    # print whole eltako configuration
    LOGGER.debug(f"[{LOG_PREFIX_INIT}] config: {config}\n")


    general_settings = config_helpers.get_general_settings_from_configuration(hass)
    # Initialise the gateway
    # get base_id from user input
    if CONF_GATEWAY_DESCRIPTION not in config_entry.data.keys():
        raise ConfigEntryError(f"[{LOG_PREFIX_INIT}] Device information for gateway is not available. Try to delete and recreate the gateway.")
    gateway_description = config_entry.data[CONF_GATEWAY_DESCRIPTION]    # from user input

    gateway_id = config_helpers.get_id_from_gateway_name(gateway_description)
    if gateway_id is None:
        raise ConfigEntryError(f"[{LOG_PREFIX_INIT}] No id of gateway available (description: '{gateway_description}'). Try to delete and recreate the gateway.")

    # get home assistant configuration section matching base_id
    gateway_config = await config_helpers.async_find_gateway_config_by_id(gateway_id, hass, CONFIG_SCHEMA)
    if not gateway_config:
        raise ConfigEntryError(f"[{LOG_PREFIX_INIT}] No gateway configuration found in '/homeassistant/configuration.yaml' for gateway id {gateway_id}.")

    # get serial path info
    if CONF_SERIAL_PATH not in config_entry.data.keys():
        raise ConfigEntryError(f"[{LOG_PREFIX_INIT}] No information about serial path available for gateway. Try to delete and recreate the gateway.")
    gateway_serial_path = config_entry.data[CONF_SERIAL_PATH]

    # only transceiver can send teach-in telegrams
    gateway_device_type = GatewayDeviceType.find(gateway_config[CONF_DEVICE_TYPE])    # from configuration
    if gateway_device_type is None:
        raise ConfigEntryError(f"[{LOG_PREFIX_INIT}] USB device {gateway_config[CONF_DEVICE_TYPE]} is not supported!!!")
    if GatewayDeviceType.is_lan_gateway(gateway_device_type):
        if gateway_config.get(CONF_GATEWAY_ADDRESS, None) is None:
            raise ConfigEntryError(f"[{LOG_PREFIX_INIT}] Missing field '{CONF_GATEWAY_ADDRESS}' for LAN Gateway (id: {gateway_id})")

    general_settings[CONF_ENABLE_TEACH_IN_BUTTONS] = True # GatewayDeviceType.is_transceiver(gateway_device_type) # should only be disabled for decentral gateways

    LOGGER.info(f"[{LOG_PREFIX_INIT}] Initializes Gateway Device '{gateway_description}'")
    gateway_name = gateway_config.get(CONF_NAME, None)  # from configuration
    # .get instead of []: some enum values (e.g. 'ftd14') are not serial gateways and
    # have no baud rate -> clean error instead of KeyError at startup
    baud_rate = BAUD_RATE_DEVICE_TYPE_MAPPING.get(gateway_device_type)
    if baud_rate is None:
        raise ConfigEntryError(f"[{LOG_PREFIX_INIT}] Gateway type '{gateway_device_type}' (id: {gateway_id}) is not supported as a gateway.")
    port = gateway_config.get(CONF_GATEWAY_PORT, VIRT_GW_PORT if gateway_device_type == GatewayDeviceType.VirtualNetworkAdapter else 5100)
    auto_reconnect = gateway_config.get(CONF_GATEWAY_AUTO_RECONNECT, True)
    gateway_base_id = AddressExpression.parse(gateway_config[CONF_BASE_ID])
    message_delay = gateway_config.get(CONF_GATEWAY_MESSAGE_DELAY, None)
    # LAN gateways: TCP reconnect/keep-alive timeouts (shorter than the library's 60s defaults)
    reconnection_timeout = gateway_config.get(CONF_GATEWAY_RECONNECTION_TIMEOUT, 15)
    tcp_keep_alive_timeout = gateway_config.get(CONF_GATEWAY_TCP_KEEP_ALIVE_TIMEOUT, 30)
    LOGGER.debug(f"[{LOG_PREFIX_INIT}] id: {gateway_id}, device type: {gateway_device_type}, serial path: {gateway_serial_path}, baud rate: {baud_rate}, base id: {gateway_base_id}")

    if gateway_device_type == GatewayDeviceType.VirtualNetworkAdapter:
        gateway = VirtualNetworkGateway(general_settings, hass, gateway_id, port, config_entry)
    else:
        gateway = EnOceanGateway(general_settings, hass, gateway_id, gateway_device_type, gateway_serial_path, baud_rate, port, gateway_base_id, gateway_name, auto_reconnect, message_delay, config_entry,
                                 reconnection_timeout=reconnection_timeout, tcp_keep_alive_timeout=tcp_keep_alive_timeout)

    # K6: connection problems are usually temporary (e.g. USB stick not ready yet) => let HA retry
    try:
        await gateway.async_setup()
    except Exception as e:
        raise ConfigEntryNotReady(f"[{LOG_PREFIX_INIT}] Could not start gateway (id: {gateway_id}, serial path: {gateway_serial_path}): {e}") from e

    set_gateway_to_hass(hass, gateway)

    try:
        await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    except Exception:
        # do not leak a running bus thread when the platform setup fails
        await async_unload_gateway(hass, config_entry)
        raise

    # N10: removed large commented-out frontend-panel block (register_static_path
    # was removed in HA 2025.7; the panel was never actually registered).

    return True



async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload Eltako config entry."""

    # K3: unload all platforms so that entities and their dispatcher connections are
    # cleanly removed. Otherwise every reload leaks entities/listeners and produces
    # duplicate events and unique_id collisions.
    unload_platforms_ok = await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)

    await async_unload_gateway(hass, config_entry)

    return unload_platforms_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """K7: Allow removing an Eltako device via the Home Assistant UI.

    Without this module-level function Home Assistant hides the "Delete device"
    button and refuses removal ("Config entry does not support device removal").

    The integration is YAML-configured: a device that is still listed in the
    `eltako:` section will be re-created on the next reload. This function is
    therefore mainly for cleaning up leftovers after a configuration change.

    Returning True lets Home Assistant remove the device entry and its entities
    from the registry.
    """
    LOGGER.info(
        f"[{LOG_PREFIX_INIT}] Removing device from registry on user request: "
        f"{device_entry.name} (identifiers: {device_entry.identifiers})"
    )
    return True
