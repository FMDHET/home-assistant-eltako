"""Config flows for the Eltako integration."""
# https://developers.home-assistant.io/docs/config_entries_config_flow_handler

import voluptuous as vol

import ipaddress
import socket

from homeassistant import config_entries
from homeassistant.helpers import device_registry as dr

from . import gateway
from . import config_helpers
from .const import *
from .schema import CONFIG_SCHEMA

LOGGER_PREFIX_CONFIG_FLOW = "config_flow"

class EltakoFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Eltako config flows."""

    VERSION = 1
    # B2: 1 -> 2 backfills the config-entry unique_id (AF1) for pre-v2.4.0 entries.
    # B3/AS3: 2 -> 3 remaps VOC air-quality entity unique_ids to a language-independent id.
    # New entries are created at the current MINOR_VERSION (no migration needed).
    MINOR_VERSION = 3
    MANUAL_PATH_VALUE = "Custom path"

    def __init__(self) -> None:
        """Initialize the Eltako config flow."""

    def is_input_available(self, user_input) -> bool:
        LOGGER.debug("[%s] Check available data", LOGGER_PREFIX_CONFIG_FLOW)
        if user_input is not None:
            if CONF_SERIAL_PATH in user_input and user_input[CONF_SERIAL_PATH] is not None:
                if CONF_GATEWAY_DESCRIPTION in user_input and user_input[CONF_GATEWAY_DESCRIPTION] is not None:
                    return True
        return False

    async def async_step_user(self, user_input=None):
        """Handle an Eltako config flow start."""
        # is called when adding a new gateway
        LOGGER.debug("[%s] config_flow user step started.", LOGGER_PREFIX_CONFIG_FLOW)
        return await self.async_step_detect()

    async def async_step_detect(self, user_input=None):
        """Propose a list of detected gateways."""
        LOGGER.debug("[%s] config_flow detect step started.", LOGGER_PREFIX_CONFIG_FLOW)
        return await self.manual_selection_routine(user_input)
        
    async def async_step_manual(self, user_input=None):
        """Request manual USB gateway path."""
        LOGGER.debug("[%s] config_flow manual step started.", LOGGER_PREFIX_CONFIG_FLOW)
        return await self.manual_selection_routine(user_input, manual_setp=True)
    
    async def manual_selection_routine(self, user_input=None, manual_setp:bool=False):
        LOGGER.debug("[%s] Add new gateway", LOGGER_PREFIX_CONFIG_FLOW)
        errors = {}

        if DATA_ELTAKO in self.hass.data:
            LOGGER.debug("[%s] Available Eltako Objects", LOGGER_PREFIX_CONFIG_FLOW)
            for g in self.hass.data[DATA_ELTAKO]:
                LOGGER.debug(g)

        # get configuration for debug purpose
        config = await config_helpers.async_get_home_assistant_config(self.hass, CONFIG_SCHEMA)
        LOGGER.debug(f"[%s] Config: {config}\n")

        # ensure data entry is set
        if DATA_ELTAKO not in self.hass.data:
            LOGGER.debug("[%s] No configuration available.", LOGGER_PREFIX_CONFIG_FLOW)
            self.hass.data.setdefault(DATA_ELTAKO, {})

        # goes recursively ...
        # check if values were set in the step before
        if user_input is not None:
            if self.is_input_available(user_input):
                if await self.validate_eltako_conf(user_input):
                    # AF1: one config entry per gateway id - prevents adding the same
                    # gateway twice (which previously overwrote hass.data['gateway_<id>']
                    # and let unload tear down the other entry).
                    gw_id = config_helpers.get_id_from_gateway_name(user_input[CONF_GATEWAY_DESCRIPTION])
                    if gw_id is not None:
                        await self.async_set_unique_id(f"eltako_gateway_{gw_id}")
                        self._abort_if_unique_id_configured()
                    return self.create_eltako_entry(user_input)

                # A-r2: merge into the dict - later steps previously replaced the
                # whole dict and hid this (the actual) error from the user
                errors[CONF_SERIAL_PATH] = ERROR_INVALID_GATEWAY_PATH

        LOGGER.debug("[%s] Get data for gateway selection", LOGGER_PREFIX_CONFIG_FLOW)

        # find all existing serial paths
        serial_paths = await self.hass.async_add_executor_job(gateway.detect)
        
        # get available (not registered) gateways
        g_list_dict = (await config_helpers.async_get_list_of_gateway_descriptions(self.hass, CONFIG_SCHEMA)) 
        # filter out registered gateways. all registered gateways are listen in data section
        g_list = list([g for g in g_list_dict.values() if g not in self.hass.data[DATA_ELTAKO] and 'gateway_'+str(config_helpers.get_id_from_gateway_name(g)) not in self.hass.data[DATA_ELTAKO]])
        LOGGER.debug("[%s] Available gateways to be added: %s", LOGGER_PREFIX_CONFIG_FLOW, g_list)
        if len(g_list) == 0:
            # A-r2: abort instead of rendering an un-submittable empty dropdown -
            # without configured gateways the form could never be completed
            LOGGER.debug("[%s] No gateways are configured in the 'configuration.yaml'.", LOGGER_PREFIX_CONFIG_FLOW)
            return self.async_abort(reason=ABORT_NO_CONFIGURATION_AVAILABLE)

        # add manually added serial paths and ip addresses from configuration
        for g_id in g_list_dict.keys():
            #only for available gw
            if g_list_dict[g_id] in g_list:
                g_c = config_helpers.find_gateway_config_by_id(config, g_id)
                if g_c is None:     # M10: no matching gateway config -> avoid TypeError on `in None`
                    continue
                if CONF_SERIAL_PATH in g_c:
                    serial_paths.append(g_c[CONF_SERIAL_PATH])
                if CONF_GATEWAY_ADDRESS in g_c:
                    address = g_c[CONF_GATEWAY_ADDRESS]
                    serial_paths.append(address)

        # get all serial paths which are not taken by existing gateways
        device_registry = dr.async_get(self.hass)
        serial_paths_of_registered_gateways = await gateway.async_get_serial_path_of_registered_gateway(device_registry)
        serial_paths = list(set([sp for sp in serial_paths if sp not in serial_paths_of_registered_gateways]))
        LOGGER.debug("[%s] Available serial paths/IP addresses: %s", LOGGER_PREFIX_CONFIG_FLOW, serial_paths)

        if manual_setp or len(serial_paths) == 0:
            # A-r2: only report 'no serial path' when that is actually the situation
            # and no more specific error (e.g. failed validation) is pending -
            # previously this unconditionally clobbered the real error, and even
            # appeared on the manual form's very first display.
            if len(serial_paths) == 0 and not errors:
                LOGGER.debug("[%s] No usb port or any manually configured address available.", LOGGER_PREFIX_CONFIG_FLOW)
                errors[CONF_SERIAL_PATH] = ERROR_NO_SERIAL_PATH_AVAILABLE

            return self.async_show_form(
                step_id="manual",
                data_schema=vol.Schema({
                    vol.Required(CONF_GATEWAY_DESCRIPTION, msg="EnOcean Gateway", description="Gateway to be initialized."): vol.In(g_list),
                    vol.Required(CONF_SERIAL_PATH, msg="Serial Port/IP Address", description="Serial path/IP address for selected gateway."): str
                }),
                errors=errors,
            )


        # show form in which gateways and serial paths are displayed so that a mapping can be selected.
        return self.async_show_form(
            step_id="detect",
            data_schema=vol.Schema({
                vol.Required(CONF_GATEWAY_DESCRIPTION, msg="EnOcean Gateway", description="Gateway to be initialized."): vol.In(g_list),
                vol.Required(CONF_SERIAL_PATH, msg="Serial Port/IP Address", description="Serial path/IP address for selected gateway."): vol.In(serial_paths),
            }),
            errors=errors,
        )

    async def validate_eltako_conf(self, user_input) -> bool:
        """Return True if the user_input contains a valid gateway path."""
        serial_path: str = user_input[CONF_SERIAL_PATH]
        gateway_selection: str = user_input[CONF_GATEWAY_DESCRIPTION]

        LOGGER.debug("[%s] Start serial path validation for '%s' and address '%s'", LOGGER_PREFIX_CONFIG_FLOW, gateway_selection, serial_path)

        # M10: determine the device type from the YAML config by gateway id instead of
        # substring-matching the description (which mis-matched e.g. a type value 'lan'
        # inside a gateway named "Planung", and could confuse overlapping type names).
        gateway_device_type = None
        gateway_id = config_helpers.get_id_from_gateway_name(gateway_selection)
        if gateway_id is not None:
            config = await config_helpers.async_get_home_assistant_config(self.hass, CONFIG_SCHEMA)
            g_c = config_helpers.find_gateway_config_by_id(config, gateway_id)
            if g_c is not None:
                gateway_device_type = GatewayDeviceType.find(g_c[CONF_DEVICE_TYPE])

        if gateway_device_type is None:
            LOGGER.warning("[%s] Could not determine device type for gateway '%s'.", LOGGER_PREFIX_CONFIG_FLOW, gateway_selection)
            return False

        # virtual network gateway has no physical path to validate
        if gateway_device_type == GatewayDeviceType.VirtualNetworkAdapter:
            return True

        # check ip address / hostname for esp2/3 over tcp
        if GatewayDeviceType.is_lan_gateway(gateway_device_type):
            # R3D-01: accept a HOSTNAME / mDNS `.local` name too, not only an IP literal.
            # The runtime (socket.connect in HardenedTCP2SerialCommunicator) resolves names,
            # so IP-only validation here wrongly blocked adding a LAN gateway addressed by name.
            # R3D-01 review (Bug 2): reject an empty/blank address up front - getaddrinfo("")
            # resolves to loopback and would otherwise wrongly pass.
            if not serial_path or not serial_path.strip():
                LOGGER.debug("[%s] Empty serial path/address for LAN gateway.", LOGGER_PREFIX_CONFIG_FLOW)
                return False
            try:
                ipaddress.ip_address(serial_path)
                LOGGER.debug("[%s] Found valid IP Address %s.", LOGGER_PREFIX_CONFIG_FLOW, serial_path)
                return True
            except ValueError:
                pass
            # not an IP literal -> accept if it resolves (getaddrinfo blocks -> executor)
            try:
                await self.hass.async_add_executor_job(socket.getaddrinfo, serial_path, None)
                LOGGER.debug("[%s] Resolved hostname %s.", LOGGER_PREFIX_CONFIG_FLOW, serial_path)
                return True
            except (OSError, UnicodeError):
                # R3D-01 review (Bug 1): getaddrinfo runs the name through the idna codec and
                # raises UnicodeError (a ValueError subclass, NOT an OSError) for malformed names
                # (leading dot, '..', a label > 63 chars). Catch it too so a mistyped `.local`
                # name is reported as invalid instead of crashing the config flow.
                LOGGER.debug("[%s] serial_path: %s is neither an IP address nor a resolvable hostname", LOGGER_PREFIX_CONFIG_FLOW, serial_path)
                return False

        # check serial ports / usb
        baud_rate = gateway.BAUD_RATE_DEVICE_TYPE_MAPPING.get(gateway_device_type)
        if baud_rate is None:
            # e.g. 'ftd14' is a valid enum value but has no baud rate and is not a serial gateway
            LOGGER.warning("[%s] Gateway type '%s' has no baud rate mapping and cannot be validated as a serial gateway.", LOGGER_PREFIX_CONFIG_FLOW, gateway_device_type)
            return False
        path_is_valid = await self.hass.async_add_executor_job(
            gateway.validate_path, serial_path, baud_rate
        )
        LOGGER.debug("[%s] serial_path: %s, validated with baud rate %d is %s", LOGGER_PREFIX_CONFIG_FLOW, serial_path, baud_rate, path_is_valid)
        return path_is_valid

    def create_eltako_entry(self, user_input):
        """Create an entry for the provided configuration."""
        LOGGER.debug("[%s] Create Gateway Entry", LOGGER_PREFIX_CONFIG_FLOW)
        return self.async_create_entry(title=user_input[CONF_GATEWAY_DESCRIPTION], data=user_input)
