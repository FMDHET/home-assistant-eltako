"""Diagnostics support for the Eltako integration. (B4)

Provides a downloadable diagnostics dump per config entry (Settings -> Devices &
Services -> Eltako -> ... -> Download diagnostics), so a bug report can include the
gateway's runtime state without the user copying logs by hand. The gateway host
(serial path / LAN address) is redacted."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from eltakobus.util import b2s

from .const import CONF_SERIAL_PATH, CONF_GATEWAY_ADDRESS
from . import get_gateway_from_hass, get_device_config_for_gateway

# Host / network location of the gateway - redacted from shared diagnostics.
TO_REDACT = {CONF_SERIAL_PATH, CONF_GATEWAY_ADDRESS, "serial_path", "address", "host"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, config_entry: ConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    diag: dict[str, Any] = {
        "config_entry": {
            "title": config_entry.title,
            "version": config_entry.version,
            "minor_version": config_entry.minor_version,
            "unique_id": config_entry.unique_id,
            "data": async_redact_data(dict(config_entry.data), TO_REDACT),
        },
    }

    # Diagnostics must never raise (it is the tool a user reaches for when the entry is
    # broken). get_gateway_from_hass hard-subscripts config_entry.data[GATEWAY_DESCRIPTION],
    # which a legacy/corrupted entry may lack.
    try:
        gateway = get_gateway_from_hass(hass, config_entry)
    except Exception as err:
        diag["gateway"] = None
        diag["gateway_lookup_error"] = str(err)
        return diag

    if gateway is None:
        diag["gateway"] = None
        return diag

    # AG1 signature is visible here: base_id 00-00-00-00 while received_messages climbs
    # means every telegram is being dropped.
    diag["gateway"] = {
        "dev_id": gateway.dev_id,
        "dev_name": gateway.dev_name,
        "model": gateway.model,
        "dev_type": str(gateway.dev_type),
        "native_protocol": gateway.native_protocol,
        "base_id": b2s(gateway.base_id),
        "is_connected": gateway.is_connected,
        "auto_reconnect": gateway.is_auto_reconnect_enabled,
        "message_delay": gateway.message_delay,
        "received_messages": getattr(gateway, "_received_message_count", None),
        "serial_path": "**REDACTED**",
    }

    # The device list configured for this gateway (EnOcean addresses are kept - they
    # are needed to diagnose addressing issues and are not sensitive). Still passed
    # through async_redact_data as defense-in-depth: the host-redaction guarantee must
    # not rest on get_device_config's return shape (a future refactor could surface a
    # gateway-level 'address'/host key here).
    try:
        diag["device_config"] = async_redact_data(
            get_device_config_for_gateway(hass, config_entry, gateway), TO_REDACT)
    except Exception as err:  # never let diagnostics raise
        diag["device_config_error"] = str(err)

    return diag
