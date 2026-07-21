"""Support for Eltako covers."""
from __future__ import annotations

from typing import Any

from eltakobus.util import AddressExpression
from eltakobus.eep import *

from homeassistant import config_entries
from homeassistant.components.cover import CoverEntity, CoverEntityFeature, ATTR_POSITION, ATTR_TILT_POSITION
from homeassistant.const import CONF_DEVICE_CLASS, Platform, STATE_OPEN, STATE_OPENING, STATE_CLOSED, STATE_CLOSING
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType

from .device import *
from . import config_helpers 
from .config_helpers import DeviceConf
from .gateway import EnOceanGateway
from .const import CONF_SENDER, CONF_TIME_CLOSES, CONF_TIME_OPENS, CONF_TIME_TILTS, LOGGER
from . import get_gateway_from_hass, get_device_config_for_gateway
import asyncio

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eltako cover platform."""
    gateway: EnOceanGateway = get_gateway_from_hass(hass, config_entry)
    config: ConfigType = get_device_config_for_gateway(hass, config_entry, gateway)

    entities: list[EltakoEntity] = []
    
    platform = Platform.COVER
    if platform in config:
        for entity_config in config[platform]:

            try:
                dev_conf = DeviceConf(entity_config, [CONF_DEVICE_CLASS, CONF_TIME_CLOSES, CONF_TIME_OPENS, CONF_TIME_TILTS])
                _area_start = len(entities)
                sender_config = config_helpers.get_device_conf(entity_config, CONF_SENDER)

                entities.append(EltakoCover(platform, gateway, dev_conf.id, dev_conf.name, dev_conf.eep,
                                            sender_config.id, sender_config.eep,
                                            dev_conf.get(CONF_DEVICE_CLASS), dev_conf.get(CONF_TIME_CLOSES), dev_conf.get(CONF_TIME_OPENS), dev_conf.get(CONF_TIME_TILTS)))
                apply_area_to_entities(entities, _area_start, dev_conf)   # F1

            except Exception as e:
                LOGGER.warning("[%s] Could not load configuration", platform)
                LOGGER.critical(e, exc_info=True)
                
        
    validate_actuators_dev_and_sender_id(entities)
    log_entities_to_be_added(entities, platform)
    async_add_entities(entities)

class EltakoCover(EltakoEntity, CoverEntity, RestoreEntity):
    """Representation of an Eltako cover device."""

    def __init__(self, platform:str, gateway: EnOceanGateway, dev_id: AddressExpression, dev_name: str, dev_eep: EEP, sender_id: AddressExpression, sender_eep: EEP, device_class: str, time_closes, time_opens, time_tilts):
        """Initialize the Eltako cover device."""
        super().__init__(platform, gateway, dev_id, dev_name, dev_eep)
        self._sender_id = sender_id
        self._sender_eep = sender_eep

        self._attr_device_class = device_class
        self._attr_is_opening = False
        self._attr_is_closing = False
        self._attr_is_closed = None # means undefined state
        self._attr_current_cover_position = None
        self._attr_current_cover_tilt_position = None
        self._time_closes = time_closes
        self._time_opens = time_opens
        self._time_tilts = time_tilts
        self._tilt_task = None  # H5: cancellable task for the tilt move/stop sequence
        self._move_generation = 0  # R3-18: bumped by every new tilt/movement; a tilt task's
        # post-sleep STOP is only sent if its generation is still current
        
        self._attr_supported_features = (CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP)
        
        if time_tilts is not None:
            self._attr_supported_features |= CoverEntityFeature.SET_TILT_POSITION

        if time_closes is not None and time_opens is not None:
            self._attr_supported_features |= CoverEntityFeature.SET_POSITION


    def load_value_initially(self, latest_state:State):
        # LOGGER.debug(f"[cover {self.dev_id}] latest state: {latest_state.state}")
        # LOGGER.debug(f"[cover {self.dev_id}] latest state attributes: {latest_state.attributes}")
        try:
            # A-r2: .get() - HA only stores current_tilt_position when tilt is
            # configured; direct indexing raised KeyError for covers without
            # time_tilts and the except wiped the already-restored position on
            # EVERY restart.
            self._attr_current_cover_position = latest_state.attributes.get('current_position')
            self._attr_current_cover_tilt_position = latest_state.attributes.get('current_tilt_position')

            #if self._attr_current_cover_tilt_position == 0:
            #    self._attr_current_cover_tilt_position = 0
            if latest_state.state == STATE_OPEN:
                self._attr_is_opening = False
                self._attr_is_closing = False
                self._attr_is_closed = False
                self._attr_current_cover_position = 100
                self._attr_current_cover_tilt_position = 100
            elif latest_state.state == STATE_CLOSED:
                self._attr_is_opening = False
                self._attr_is_closing = False
                self._attr_is_closed = True
                self._attr_current_cover_position = 0
                self._attr_current_cover_tilt_position = 0
            elif latest_state.state in (STATE_CLOSING, STATE_OPENING):
                # R3-17: the movement ended while HA was down; nothing will ever clear a
                # restored moving flag (no further telegram, no timeout), so the cover would
                # be stuck showing "opening"/"closing" forever. Restore as STOPPED with the
                # endpoint unknown and derive is_closed from the last known position.
                self._attr_is_opening = False
                self._attr_is_closing = False
                pos = self._attr_current_cover_position
                self._attr_is_closed = (pos == 0) if pos is not None else None


        except Exception:
            self._attr_current_cover_position = None
            self._attr_current_cover_tilt_position = None
            self._attr_is_opening = None
            self._attr_is_closing = None
            self._attr_is_closed = None # means undefined state
            # raise e
        
        self.schedule_update_ha_state()
        LOGGER.debug(f"[cover {self.dev_id}] value initially loaded: [" 
                     + f"is_opening: {self.is_opening}, "
                     + f"is_closing: {self.is_closing}, "
                     + f"is_closed: {self.is_closed}, "
                     + f"current_possition: {self._attr_current_cover_position}, "
                     + f"current_tilt_position: {self._attr_current_cover_tilt_position}, "
                     + f"state: {self.state}]")


    def open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        if self._time_opens is not None:
            # A-r2: clamp - schema allows time values up to 255 and the +1 made
            # encode_message raise ValueError (byte must be in range(0, 256))
            time = min(self._time_opens + 1, 255)
        else:
            time = 255
        
        address, _ = self._sender_id

        if self._sender_eep == H5_3F_7F:
            self._cancel_tilt_task_for_new_movement()   # A-r2: stray tilt STOP must not halt this move
            msg = H5_3F_7F(time, 0x01, 1).encode_message(address)
            self.send_message(msg)

        else:
            LOGGER.warning("[%s %s] Sender EEP %s not supported.", Platform.COVER, str(self.dev_id), self._sender_eep.eep_string)
            return
        
        #TODO: ... setting state should be comment out
        # Don't set state instead wait for response from actor so that real state of light is displayed.
        if self.general_settings[CONF_FAST_STATUS_CHANGE]:
            self._attr_is_opening = True
            self._attr_is_closing = False
            
            self.schedule_update_ha_state()
    

    def close_cover(self, **kwargs: Any) -> None:
        """Close cover."""
        if self._time_closes is not None:
            # A-r2: clamp (see open_cover)
            time = min(self._time_closes + 1, 255)
        else:
            time = 255
        
        address, _ = self._sender_id

        if self._sender_eep == H5_3F_7F:
            self._cancel_tilt_task_for_new_movement()   # A-r2
            msg = H5_3F_7F(time, 0x02, 1).encode_message(address)
            self.send_message(msg)

        else:
            LOGGER.warning("[%s %s] Sender EEP %s not supported.", Platform.COVER, str(self.dev_id), self._sender_eep.eep_string)
            return
        
        #TODO: ... setting state should be comment out
        # Don't set state instead wait for response from actor so that real state of light is displayed.
        if self.general_settings[CONF_FAST_STATUS_CHANGE]:
            self._attr_is_closing = True
            self._attr_is_opening = False

            self.schedule_update_ha_state()

    def set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        if self._time_closes is None or self._time_opens is None:
            return
        
        address, _ = self._sender_id
        position = kwargs[ATTR_POSITION]

        # H4: after a restart without restore state the current position is None.
        # Absolute moves (fully up/down) work; relative moves cannot be computed.
        if self._attr_current_cover_position is None and position not in (0, 100):
            LOGGER.warning("[%s %s] Current position unknown - cannot move to %s%%. Move cover fully up or down once first.", Platform.COVER, str(self.dev_id), position)
            return

        if position == self._attr_current_cover_position:
            return
        elif position == 100:
            direction = "up"
            time = min(self._time_opens + 1, 255)   # A-r2: clamp (see open_cover)
        elif position == 0:
            direction = "down"
            time = min(self._time_closes + 1, 255)  # A-r2: clamp
        elif position > self._attr_current_cover_position:
            direction = "up"
            time = max(1,min(int(((position - self._attr_current_cover_position) / 100.0) * self._time_opens), 255))
            # try to prevent covers moving completely up or down when time = 0
        elif position < self._attr_current_cover_position:
            direction = "down"
            time = max(1,min(int(((self._attr_current_cover_position - position) / 100.0) * self._time_closes), 255))
            # try to prevent covers moving completely up or down when time = 0

        if self._sender_eep == H5_3F_7F:
            if direction == "up":
                command = 0x01
            elif direction == "down":
                command = 0x02

            self._cancel_tilt_task_for_new_movement()   # A-r2
            msg = H5_3F_7F(time, command, 1).encode_message(address)
            self.send_message(msg)

        else:
            LOGGER.warning("[%s %s] Sender EEP %s not supported.", Platform.COVER, str(self.dev_id), self._sender_eep.eep_string)
            return
        
        if self.general_settings[CONF_FAST_STATUS_CHANGE]:
            if direction == "up":
                self._attr_is_opening = True
                self._attr_is_closing = False
            elif direction == "down":
                self._attr_is_closing = True
                self._attr_is_opening = False
                
            self.schedule_update_ha_state()
        

    def stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        address, _ = self._sender_id

        if self._sender_eep == H5_3F_7F:
            self._cancel_tilt_task_for_new_movement()   # A-r2: we send our own STOP below
            msg = H5_3F_7F(0, 0x00, 1).encode_message(address)
            self.send_message(msg)
        
        if self.general_settings[CONF_FAST_STATUS_CHANGE]:
            self._attr_is_closing = False
            self._attr_is_opening = False

            self.schedule_update_ha_state()


    def value_changed(self, msg):
        """Update the internal state of the cover."""
        try:
            decoded = self.dev_eep.decode_message(msg)
        except Exception as e:
            LOGGER.warning("Could not decode message: %s", str(e))
            return
        
        if self.dev_eep in [G5_3F_7F]:
            LOGGER.debug(f"[cover {self.dev_id}] G5_3F_7F - {decoded.__dict__}")

            ## is received as response when button pushed (command was sent) 
            ## this message is received directly when the cover starts to move
            ## when the cover results in completely open or close one of the following messages (open or closed) will appear
            if decoded.state == 0x02: # down
                self._attr_is_closing = True
                self._attr_is_opening = False
                self._attr_is_closed = False
            elif decoded.state == 0x50: # closed
                self._attr_is_opening = False
                self._attr_is_closing = False
                self._attr_is_closed = True
                self._attr_current_cover_position = 0
                self._attr_current_cover_tilt_position = 0
            elif decoded.state == 0x01: # up
                self._attr_is_opening = True
                self._attr_is_closing = False
                self._attr_is_closed = False
            elif decoded.state == 0x70: # open
                self._attr_is_opening = False
                self._attr_is_closing = False
                self._attr_is_closed = False
                self._attr_current_cover_position = 100
                self._attr_current_cover_tilt_position = 100

            ## is received when cover stops at the desired intermediate position
            ## if not close state is always open (close state should be reported with closed message above)
            elif decoded.time is not None and decoded.direction is not None and (self._time_closes is None or self._time_opens is None):
                # A-r2: without configured travel times the position cannot be computed,
                # but the telegram still means the cover STOPPED - previously it was
                # swallowed entirely and is_opening/is_closing stayed set forever.
                self._attr_is_opening = False
                self._attr_is_closing = False
                self._attr_is_closed = False

            elif decoded.time is not None and decoded.direction is not None and self._time_closes is not None and self._time_opens is not None:

                time_in_seconds = decoded.time / 10.0

                if decoded.direction == 0x01:  # up
                    # If the latest state is unknown, the cover position
                    # will be set to None, therefore we have to guess
                    # the initial position.
                    if self._attr_current_cover_position is None:
                        self._attr_current_cover_position = 0
                    
                    self._attr_current_cover_position = min(self._attr_current_cover_position + int(time_in_seconds / self._time_opens * 100.0), 100)
                    if self._time_tilts is not None:
                        # H4: tilt position can be None after restart -> guard like the main position above
                        if self._attr_current_cover_tilt_position is None:
                            self._attr_current_cover_tilt_position = 0
                        self._attr_current_cover_tilt_position = min(self._attr_current_cover_tilt_position + int(decoded.time / self._time_tilts * 100.0), 100)

                else:  # down
                    # If the latest state is unknown, the cover position
                    # will be set to None, therefore we have to guess
                    # the initial position.
                    if self._attr_current_cover_position is None:
                        self._attr_current_cover_position = 100
                    
                    self._attr_current_cover_position = max(self._attr_current_cover_position - int(time_in_seconds / self._time_closes * 100.0), 0)
                    if self._time_tilts is not None:
                        # H4: tilt position can be None after restart -> guard like the main position above
                        if self._attr_current_cover_tilt_position is None:
                            self._attr_current_cover_tilt_position = 100
                        self._attr_current_cover_tilt_position = max(self._attr_current_cover_tilt_position - int(decoded.time / self._time_tilts * 100.0), 0)

                if self._attr_current_cover_position == 0:
                    self._attr_is_closed = True
                    self._attr_is_opening = False
                    self._attr_is_closing = False
                else:
                    self._attr_is_closed = False
                    self._attr_is_opening = False
                    self._attr_is_closing = False

            
            LOGGER.debug(f"[cover {self.dev_id}] state: {self.state}, opening: {self.is_opening}, closing: {self.is_closing}, closed: {self.is_closed}, position: {self._attr_current_cover_position}")

            self.schedule_update_ha_state()


    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        # H5: was a synchronous method that called time.sleep() for up to 255s, blocking an
        # executor worker for minutes (fleet-wide starvation on scenes). Now async: the
        # move/stop telegrams are sent by a cancellable background task.
        address, _ = self._sender_id
        tilt_position = kwargs[ATTR_TILT_POSITION]

        # H4: current tilt position is None after a restart without restore state -> relative
        # tilt cannot be computed and `int > None` would raise TypeError. Same guard as set_cover_position.
        if self._attr_current_cover_tilt_position is None:
            LOGGER.warning("[%s %s] Current tilt position unknown - cannot tilt to %s%%. Move cover fully up or down once first.", Platform.COVER, str(self.dev_id), tilt_position)
            return

        if tilt_position == self._attr_current_cover_tilt_position:
            return
        elif tilt_position > self._attr_current_cover_tilt_position:
            direction = "up"
            sleeptime = min((((tilt_position - self._attr_current_cover_tilt_position) / 100.0 * self._time_tilts / 10.0) ), 255.0)
        else:
            direction = "down"
            sleeptime = min((((self._attr_current_cover_tilt_position - tilt_position) / 100.0 * self._time_tilts / 10.0) ), 255.0)

        if self._sender_eep == H5_3F_7F:
            command = 0x01 if direction == "up" else 0x02
            # cancel a still-running tilt sequence and WAIT for its stop telegram to be queued
            # before sending the new move telegram. hass.async_create_task eager-starts the new
            # task, so without this await the new MOVE would be dispatched before the cancelled
            # task's STOP, and the STOP would immediately halt the new movement (no-op tilt).
            await self._cancel_tilt_task()
            # R3-18: tag this tilt with a movement generation; the post-sleep STOP only fires
            # if no newer tilt/movement superseded it in the meantime (see _async_run_tilt).
            self._move_generation += 1
            generation = self._move_generation
            self._tilt_task = self.hass.async_create_task(self._async_run_tilt(address, command, sleeptime, generation))

        if self.general_settings[CONF_FAST_STATUS_CHANGE]:
            if direction == "up":
                self._attr_is_opening = True
                self._attr_is_closing = False
            elif direction == "down":
                self._attr_is_closing = True
                self._attr_is_opening = False
            self.schedule_update_ha_state()

    async def _async_run_tilt(self, address, command, sleeptime, generation=None) -> None:
        """Send the tilt-start telegram, wait, then send the stop telegram. Cancellable. (H5)"""
        try:
            self.send_message(H5_3F_7F(0, command, 1).encode_message(address))
            await asyncio.sleep(sleeptime)
            # R3-18: re-validate before the post-sleep STOP. If a newer tilt/movement superseded
            # this one during the sleep (without the cancel reaching us in time), our stale STOP
            # would halt the NEW movement. Only stop if we are still the current generation.
            if generation is not None and (self._move_generation != generation or self._tilt_task is not asyncio.current_task()):
                LOGGER.debug("[%s %s] Tilt superseded during move - skipping stale STOP telegram.", Platform.COVER, str(self.dev_id))
                return
            self.send_message(H5_3F_7F(0, 0x00, 1).encode_message(address))
        except asyncio.CancelledError:
            # ensure the cover does not keep moving when cancelled (new command / entity removed).
            # A-r2 exception: when a movement command (open/close/set_position/stop) superseded
            # this tilt, that command already controls the actuator - sending our stop telegram
            # afterwards would halt the NEW movement. The superseding path marks the task.
            if not getattr(asyncio.current_task(), '_eltako_skip_stop_on_cancel', False):
                try:
                    self.send_message(H5_3F_7F(0, 0x00, 1).encode_message(address))
                except Exception:
                    pass
            raise

    def _cancel_tilt_task_for_new_movement(self) -> None:
        """Cancel a pending tilt task from the sync command paths (executor thread). (A-r2)

        Previously only async_set_cover_tilt_position cancelled the task, so a
        sleeping tilt task's delayed STOP telegram could halt a movement started
        by open/close/set_position afterwards. The task is marked so its
        cancel-handler does NOT emit a stop telegram (the new command takes over).
        """
        # R3-18: a new movement supersedes any in-flight tilt generation, so even a tilt task
        # that races past its cancellation will skip its stale post-sleep STOP.
        self._move_generation += 1
        task = self._tilt_task
        self._tilt_task = None
        if task is not None and not task.done():
            task._eltako_skip_stop_on_cancel = True
            # task.cancel() must run on the event loop; sync covers run in the executor
            self.hass.loop.call_soon_threadsafe(task.cancel)

    async def _cancel_tilt_task(self) -> None:
        # Cancel the running tilt task and await it so its stop-on-cancel telegram is flushed
        # into the (FIFO) send pipeline before the caller sends anything new.
        task = self._tilt_task
        self._tilt_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                LOGGER.exception("[%s %s] Error while cancelling tilt task.", Platform.COVER, str(self.dev_id))

    async def async_will_remove_from_hass(self) -> None:
        # H5: cancel a pending tilt sequence so it does not send telegrams for a removed entity
        await self._cancel_tilt_task()
        await super().async_will_remove_from_hass()