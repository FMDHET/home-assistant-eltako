import asyncio
from typing import Any

from custom_components.eltako.config_helpers import *
from custom_components.eltako.gateway import EnOceanGateway
class BusMock():

    def __init__(self):
        self.fired_events = list()

    def fire(self, 
                event_type: str,
                event_data: dict[str, Any] | None = None,
                origin = None,
                context = None,
                ) -> None:
        
        self.fired_events.append({
            'event_type': event_type,
            'event_data': event_data,
            'origin': origin,
            'context': context
        })

class FakeServiceRegistry():
    """Minimal hass.services stand-in (A6: init/gateway service (de)registration)."""
    def __init__(self):
        self._services = {}
    def async_register(self, domain, name, func, *a, **k):
        self._services[(domain, name)] = func
    def has_service(self, domain, name):
        return (domain, name) in self._services
    def async_remove(self, domain, name):
        self._services.pop((domain, name), None)


class FakeConfigEntries():
    """Minimal hass.config_entries stand-in (A6: init setup/unload).

    Set `forward_side_effect`/`unload_result` to drive error paths."""
    def __init__(self):
        self.forwarded = None
        self.unloaded = None
        self.forward_side_effect = None
        self.unload_result = True
    async def async_forward_entry_setups(self, entry, platforms):
        if self.forward_side_effect is not None:
            raise self.forward_side_effect
        self.forwarded = list(platforms)
        return True
    async def async_unload_platforms(self, entry, platforms):
        self.unloaded = list(platforms)
        return self.unload_result


class HassMock():

    def __init__(self, data: dict = None) -> None:
        self.bus = BusMock()
        self.data = data if data is not None else {}
        self.services = FakeServiceRegistry()
        self.config_entries = FakeConfigEntries()
        # Python >= 3.12: get_event_loop() no longer creates a loop implicitly (RuntimeError on 3.14)
        try:
            self.loop = asyncio.get_event_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

    async def async_add_executor_job(self, func, *args):
        # run synchronously so reconnect/validate_path/detect execute in tests
        return func(*args)

    def async_create_task(self, coro, *a, **k):
        return self.loop.create_task(coro)

    def create_task(self, coro, *a, **k):
        return self.loop.create_task(coro)


class ConfigEntryMock():

    def __init__(self, data: dict = None, entry_id: str = "entity_id", domain=None, title="", unique_id=None):
        self.entry_id = entry_id
        self.data = data if data is not None else {}
        self.domain = domain if domain is not None else DOMAIN
        self.title = title
        self.unique_id = unique_id
        self.version = 1
        self.minor_version = 1
        self.state = None
        self._on_unload = []
    def async_on_unload(self, func):
        self._on_unload.append(func)

class EltakoBusMock():

    def __init__(self):
        self._is_active = True

    def is_active(self):
        return self._is_active

class EventMock():
    def __init__(self, service:str, data:dict) -> None:
        self.service = service
        self.data = data
class GatewayMock(EnOceanGateway):

    def __init__(self, general_settings:dict=DEFAULT_GENERAL_SETTINGS, dev_id: int=123, base_id:AddressExpression=AddressExpression.parse('FF-AA-80-00')):
        hass = HassMock()
        gw_type = GatewayDeviceType.GatewayEltakoFAM14

        super().__init__(general_settings, hass, dev_id, gw_type, 'SERIAL_PATH', 56700, 5100, base_id, "MyFAM14", auto_reconnect=True, message_delay=0.01, config_entry=ConfigEntryMock())

        self._bus = EltakoBusMock()

    def set_status_changed_handler(self):
        pass

    def _register_device(self) -> None:
        pass

    def _fire_connection_state_changed_event(self, status):
        pass

    def add_connection_state_changed_handler(self, handler):
        # B1: record the handler so tests can assert registration and drive
        # connect/disconnect. Unlike the real gateway we do NOT schedule the
        # immediate notify - there is no running loop in unit tests.
        self._connection_state_handlers.append(handler)


class LatestStateMock():
    def __init__(self, state:str=None, attributes:dict[str:str]={}):
        self.state = state
        self.attributes = attributes
        
