"""Support for Eltako devices."""
# N10: removed dead imports (os, async_register_built_in_panel, panel_custom,
# websocket_api) that only served the commented-out frontend-panel code below.
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from eltakobus.eep import VOC_SubstancesType


from .const import *
from .virtual_network_gateway import VirtualNetworkGateway, VIRT_GW_PORT
from .schema import CONFIG_SCHEMA
from . import config_helpers
from .eltakobus_patches import apply_eltakobus_patches
from .gateway import *
# N10: dead 'from .frontend.info_page_view import InfoPageView' removed - the view
# was never registered (its panel/static registration is gone).

LOG_PREFIX_INIT = "Eltako Integration Setup"

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Eltako component."""
    LOGGER.info(f"[{LOG_PREFIX_INIT}] Initialize Home Assistant Eltako Integration: https://github.com/grimmpp/home-assistant-eltako")

    # B5: patch known eltako14bus bugs before any telegram is encoded/decoded.
    apply_eltakobus_patches()

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


def _update_base_id_repair(hass: HomeAssistant, gateway: EnOceanGateway) -> None:
    """B4 / AG1: surface the silent "no base id -> all telegrams dropped" failure.

    ESP2 gateways other than the FAM14 never auto-query their base id (see
    EnOceanGateway.query_for_base_id_and_version). If such a gateway is left at the
    schema default 00-00-00-00, the receive path's `int(base_id) != 0` gate silently
    discards EVERY telegram while the message counter keeps climbing - previously
    invisible to the user. Raise a repair issue instead; clear it once a valid base id
    is configured (checked again on each reload)."""
    issue_id = f"missing_base_id_{gateway.dev_id}"
    # The Virtual Network Gateway forwards OTHER gateways' telegrams to TCP clients; it
    # hardcodes base_id 00-00-00-00, never runs the serial receive gate, and cannot be
    # reconfigured - so the silent-drop condition does NOT apply to it (would otherwise
    # be a permanent, unfixable false-positive warning).
    is_virtual = gateway.dev_type == GatewayDeviceType.VirtualNetworkAdapter
    queries_base_id = (not GatewayDeviceType.is_esp2_gateway(gateway.dev_type)
                       or gateway.dev_type == GatewayDeviceType.GatewayEltakoFAM14)
    base_id_missing = int.from_bytes(gateway.base_id[0]) == 0

    if base_id_missing and not queries_base_id and not is_virtual:
        LOGGER.warning("[%s] Gateway '%s' (%s) has no base id (00-00-00-00) and does not query one - "
                       "ALL received telegrams are being ignored. Configure a valid 'base_id'.",
                       LOG_PREFIX_INIT, gateway.dev_name, gateway.dev_type.value)
        ir.async_create_issue(
            hass, DOMAIN, issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="missing_base_id",
            translation_placeholders={"gateway": gateway.dev_name, "gateway_type": gateway.dev_type.value},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)

async def async_unload_gateway(hass: HomeAssistant, config_entry: ConfigEntry) -> None:

    gateway:EnOceanGateway = get_gateway_from_hass(hass, config_entry)
    if gateway is not None:

        LOGGER.info(f"[{LOG_PREFIX_INIT}] Unload {gateway.dev_name} and all its supported devices!")

        # B4/AG1: clear the base-id repair issue on unload so it does not linger after
        # the gateway is removed; a reload re-evaluates it in async_setup_entry.
        ir.async_delete_issue(hass, DOMAIN, f"missing_base_id_{gateway.dev_id}")

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


def _voc_localized_to_stable_key() -> dict[str, str]:
    """AS3: map a normalized localized VOC substance name -> the stable (name_en based)
    unique_id fragment, for migrating entities created under a different HA language.

    AMBIGUOUS localized names are EXCLUDED: an upstream eltakobus data bug gives two
    substances the same German name ('Styren' -> Styrene AND Hexane), so that name
    cannot be reverse-mapped to one substance and is left un-migrated rather than
    remapped to the wrong one."""
    def _norm(s: str) -> str:
        return s.replace(' ', '_').replace('-', '_').lower()

    stable_by_name: dict[str, str] = {}
    indices_by_name: dict[str, set] = {}
    for t in VOC_SubstancesType:
        stable = _norm(t.name_en)
        for localized in (t.name_en, t.name_de):
            n = _norm(localized)
            indices_by_name.setdefault(n, set()).add(t.index)
            stable_by_name[n] = stable
    return {n: stable_by_name[n] for n, idxs in indices_by_name.items() if len(idxs) == 1}


async def _async_migrate_voc_unique_ids(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """AS3 (config-entry 1.2 -> 1.3): remap VOC air-quality entities whose unique_id
    embeds a LOCALIZED substance name to the language-independent (name_en based) id,
    via the entity registry so history/customizations are preserved. English installs
    already use that id (no-op); only other-language installs are remapped."""
    ent_reg = er.async_get(hass)
    marker = "air_quality_sensor_"
    loc_to_stable = _voc_localized_to_stable_key()

    def _migrator(entity: er.RegistryEntry) -> dict | None:
        uid = entity.unique_id
        if marker not in uid:
            return None
        prefix, _, localized = uid.rpartition(marker)
        stable = loc_to_stable.get(localized)
        # None => unknown/ambiguous; equal => already stable (e.g. English install)
        if stable is None or stable == localized:
            return None
        new_uid = f"{prefix}{marker}{stable}"
        # Collision guard: a user who switched language may already have the stable
        # entity in the registry. HA's async_migrate_entries does NOT catch the
        # "unique id already in use" error async_update_entity raises, so a naive
        # remap would abort the whole migration (setup fails). Skip instead.
        if ent_reg.async_get_entity_id(entity.domain, entity.platform, new_uid):
            LOGGER.warning("[%s] VOC entity %s -> %s skipped: target unique_id already exists.",
                           LOG_PREFIX_INIT, uid, new_uid)
            return None
        LOGGER.info("[%s] Migrating VOC entity unique_id %s -> %s.", LOG_PREFIX_INIT, uid, new_uid)
        return {"new_unique_id": new_uid}

    await er.async_migrate_entries(hass, config_entry.entry_id, _migrator)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate a config entry to the current (VERSION, MINOR_VERSION). (B2)

    Foundation for all config-entry / registry migrations. Home Assistant calls this
    only when the stored version is behind the config flow's VERSION/MINOR_VERSION
    (see config_flow.py); a stored MAJOR version newer than ours is already rejected
    by HA before we are called (guarded again below so a future major bump fails loudly).

    Steps (each idempotent, applied in ascending order; a legacy entry runs all steps
    below its version in one call):
      1.1 -> 1.2  Backfill the config-entry unique_id (`eltako_gateway_<id>`) for
                  entries created before v2.4.0 (AF1), so the duplicate-gateway guard
                  also protects installs that predate it.
      1.2 -> 1.3  Remap VOC air-quality entity unique_ids from the localized substance
                  name to the language-independent (name_en based) id (AS3), via the
                  entity registry so history is preserved.

    Further entity-registry unique_id migrations (e.g. AM3, B3) will be added as
    additional minor-version steps here, using homeassistant.helpers.entity_registry.
    """
    LOGGER.debug("[%s] Migrating config entry '%s' from version %s.%s.",
                 LOG_PREFIX_INIT, config_entry.title, config_entry.version, config_entry.minor_version)

    if config_entry.version > 1:
        # A newer MAJOR version cannot be downgraded. HA already refuses this, but be
        # explicit so a future major bump does not silently pass an unmigrated entry.
        LOGGER.error("[%s] Config entry '%s' has unsupported version %s (current major is 1).",
                     LOG_PREFIX_INIT, config_entry.title, config_entry.version)
        return False

    # --- 1.1 -> 1.2 : backfill config-entry unique_id (AF1) ---
    if config_entry.minor_version < 2:
        new_unique_id = config_entry.unique_id
        if new_unique_id is None:
            description = config_entry.data.get(CONF_GATEWAY_DESCRIPTION)
            gw_id = config_helpers.get_id_from_gateway_name(description) if description else None
            if gw_id is None:
                LOGGER.warning("[%s] Could not derive a gateway id for '%s'; leaving unique_id unset.",
                               LOG_PREFIX_INIT, config_entry.title)
            else:
                candidate = f"eltako_gateway_{gw_id}"
                # Never invent a colliding unique_id: legacy AF1 could leave two entries
                # for the same gateway. HA does NOT enforce uniqueness on async_update_entry.
                clash = any(e.entry_id != config_entry.entry_id and e.unique_id == candidate
                            for e in hass.config_entries.async_entries(DOMAIN))
                if clash:
                    LOGGER.warning("[%s] Another config entry already uses unique_id '%s'; leaving '%s' "
                                   "unset (duplicate gateway entry - consider removing one).",
                                   LOG_PREFIX_INIT, candidate, config_entry.title)
                else:
                    new_unique_id = candidate

        hass.config_entries.async_update_entry(config_entry, unique_id=new_unique_id, minor_version=2)
        LOGGER.info("[%s] Migrated config entry '%s' to version 1.2 (unique_id=%s).",
                    LOG_PREFIX_INIT, config_entry.title, new_unique_id)

    # --- 1.2 -> 1.3 : VOC air-quality entity unique_ids (AS3, first entity-registry migration) ---
    if config_entry.minor_version < 3:
        await _async_migrate_voc_unique_ids(hass, config_entry)
        hass.config_entries.async_update_entry(config_entry, minor_version=3)
        LOGGER.info("[%s] Migrated config entry '%s' to version 1.3 (VOC unique_ids).",
                    LOG_PREFIX_INIT, config_entry.title)

    return True


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

    # B4/AG1: raise (or clear) a repair issue if this gateway type can never obtain a
    # base id and none is configured, so the silent telegram-dropping is visible.
    _update_base_id_repair(hass, gateway)

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
