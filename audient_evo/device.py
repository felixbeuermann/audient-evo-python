# ============================================================
# audient_evo/device.py
# ============================================================
"""
High-dial EVO 8 device API.
This is the primary interface intended for UI and scripting.
"""

import math
import time
import logging
import threading
import queue
from typing import Optional, Callable
from concurrent.futures import Future
from functools import wraps
from .protocol import LOOPBACK_SOURCES, SAMPLE_RATES, \
    SAMPLE_RATES_INV, LOOPBACK_MAPPINGS_INV, CATEGORY_TO_HARDWARE
from .transport import EvoUsbTransport
from .state import EvoStateManager
from .worker import EvoBackgroundWorker
from .util import mon_step_to_bytes, \
    percent_to_mon_step, bytes_to_mon_step, mon_step_to_percent, \
    is_in_percent_range, vol_step_to_bytes, bytes_to_vol_percent, \
    percent_to_out_step, get_partner_channel, calculate_monitor_wValue, \
    gain_bytes_to_percent, percent_to_gain_bytes, gain_bytes_to_db, db_to_gain_bytes, \
    encode_uac_volume, decode_uac_volume

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class UsbTask:
    """Encapsulate method call for the queue."""
    def __init__(self, func, args, kwargs):
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.future = Future()

def safe_usb_transaction(func: Callable) -> Callable:
    """
    Decorator that ensures thread safety and robust error handling for USB transactions.

    Routes the transaction either directly or through a thread-safe queue depending on the calling thread and ghost mode status.
    """
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        is_getter = func.__name__.startswith("get_")

        # 1. GHOST MODE: Skip Queue
        if getattr(self, "transport", None) and self.transport.ghost_mode:
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                logger.exception(f"Ghost-Mode-Error in '{func.__name__}': {e}")
                return -1 if is_getter else False

        # 2. USB WORKER THREAD: Skip Queue
        if getattr(threading.current_thread(), "is_usb_worker", False):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                logger.exception(f"Hardware-Error in '{func.__name__}': {e}")
                return -1 if is_getter else False

        # 3. USB TASK QUEUE:
        task = UsbTask(func, (self,) + args, kwargs)
        self.command_queue.put(task)
        try:
            return task.future.result(timeout=2.0)
        except Exception as e:
            logger.exception(f"Hardware-Error in '{func.__name__}' via Queue: {e}")
            return -1 if is_getter else False

    return wrapper

class EvoDevice:
    """High-dial user-facing device API."""
    def __init__(self, transport: EvoUsbTransport):
        self._last_state: Optional[bytes] = None
        self.last_error: Optional[str] = None

        self.transport = transport
        self.profile = transport.profile

        self.state = EvoStateManager(self.profile)
        self.command_queue = queue.Queue()
        self.worker = EvoBackgroundWorker(self, self.state)

    # --------------------------------------------------------
    # Control ownership
    # --------------------------------------------------------

    def connect_hardware(self, force_hardware_sync: bool = False) -> bool:
        """
        Connects to the hardware device and synchronizes its state.
        Args:
            force_hardware_sync (bool): If True, forces a fresh initialization from the
                hardware instead of using the local state cache.
        Returns:
            bool: True if the connection and synchronization were successful, False otherwise.
        """
        if self.transport.is_connected() and not self.transport.ghost_mode:
            return True
        try:
            self.transport.connect()
            self.worker.start()
            if not force_hardware_sync and self._is_cache_populated():
                self._push_cache_to_hardware()
            else:
                self._initialize_state_from_hardware()
            return True
        except Exception as e:
            logger.exception(f"Error connecting to hardware: {e}")
            self.transport.ghost_mode = True
            return False

    def disconnect_hardware(self) -> None:
        """
        Stops communication with the hardware and enables Ghost Mode.

        Releases the USB interface back to the ALSA driver and stops the background worker.
        """
        if self.transport.ghost_mode: return
        logger.info("Giving Hardware back to ALSA (Ghost Mode)...")
        self.worker.stop()
        self.transport.release()

        # ---------------- INITIALISATION ----------------

    def _initialize_state_from_hardware(self):
        """Reads essential parameters directly from the device to populate the initial cache."""
        logger.info("Synchronizing initial state from hardware...")
        success = True
        try:
            self.set_monitor(10, 20, 0)  # Wakeup monitor by calling out of range monitor address
            time.sleep(0.05)

            for ch in range(1, self.profile.num_inputs+1):
                self.state.update_input(ch, "gain", self.get_gain_db(ch))
                self.state.update_input(ch, "phantom", self.get_phantom(ch))
                self.state.update_input(ch, "mute", self.get_mic_mute(ch))
                self.state.update_input(ch, "stereo_link", self.get_mic_stereo(ch))

            for ch in range (1, self.profile.num_outputs+1):
                self.state.update_output(ch, "volume", self.get_volume_db(ch))
                self.state.update_output(ch, "mute", self.get_out_mute(ch))
                self.state.update_output(ch, "stereo_link", self.get_out_stereo(ch))

            time.sleep(0.05)

            for in_ch in range(1, self.profile.num_monitor_inputs + 1):
                for out_ch in range(1, self.profile.num_outputs + 1):
                    self.state.update_monitor(in_ch, out_ch, "volume", self.get_monitor_db(in_ch, out_ch))
                    time.sleep(0.01)

            loopback_source = self.get_loopback_source()
            time.sleep(0.02)
            sample_rate =self.get_sample_rate()

            self.state.update_global("loopback_source", loopback_source)
            self.state.update_global("sample_rate", sample_rate)

            return success
        except Exception as e:
            logger.exception(f"Init state from hardware failed: {e}")
        return False

    def _push_cache_to_hardware(self) -> bool:
        """
        Applies the current state cache to the hardware device to wake it up.

        Returns:
            bool: True if the cache was successfully pushed to the hardware.
        """
        logger.info("Pushing Cache to Hardware (Wake-Up Call)...")
        # 1. Globals (Loopback & Sample Rate)
        lb_source = self.state.get_global("loopback_source")
        if lb_source:
            self.set_loopback_source(lb_source)

        sr = self.state.get_global("sample_rate")
        if sr and sr != -1:
            self.set_sample_rate(sr)

        artist_mix = self.state.get_global("artist_mix")
        if artist_mix is not None:
            self.set_artist_mix(artist_mix)

        # 2. Physical Inputs
        for ch in range(1, self.profile.num_inputs + 1):
            gain = self.state.get_input(ch, "gain")
            if gain not in (None, -1):
                self.set_gain_db(ch, gain)

            phantom = self.state.get_input(ch, "phantom")
            if phantom is not None:
                self.set_phantom(ch, phantom)

            mute = self.state.get_input(ch, "mute")
            if mute is not None:
                self.set_mic_mute(ch, mute)

            link = self.state.get_input(ch, "stereo_link")
            if link is not None:
                self.set_mic_stereo(ch, link)

        # 3. Physical Outputs
        for ch in range(1, self.profile.num_outputs + 1):
            vol = self.state.get_output(ch, "volume")
            if vol not in (None, -1):
                self.set_volume_db(ch, vol)

            mute = self.state.get_output(ch, "mute")
            if mute is not None:
                self.set_out_mute(ch, mute)

            link = self.state.get_output(ch, "stereo_link")
            if link is not None:
                self.set_out_stereo(ch, link)

        # 4. Sync Monitor Matrix
        self._sync_hardware_for_outputs(list(range(1, self.profile.num_outputs + 1)))

        logger.info("Cache successfully pushed to Hardware!")
        return True

    def _is_cache_populated(self) -> bool:
        """Check if cache is filled with preset."""
        return self.state.preset_loaded

    # ---------------- Internal Helper Functions ----------------

    def _set_parameter(self, param_name: str, data: bytes, ch: Optional[int] = None, out_ch: Optional[int] = None) -> bool:
        """
        Centralized method for sending USB control parameters.

        Args:
            param_name (str): The name of the parameter mapped in CATEGORY_TO_HARDWARE.
            data (bytes): The raw byte data to send.
            ch (int, optional): The target input or output channel.
            out_ch (int, optional): The target output channel (used for monitor matrix).

        Returns:
            bool: True if the USB transfer was successful, False otherwise.
        """
        mapping = CATEGORY_TO_HARDWARE.get(param_name)
        if not mapping:
            logger.error(f"Unknown parameter: {param_name}")
            return False
        # Case 1: Monitor Matrix (requires in_ch and out_ch)
        if ch is not None and out_ch is not None and param_name == "monitor":
            wValue = calculate_monitor_wValue(mapping["wValue_base"], ch, out_ch)
        # Case 2: Regular channel (1-based to 0-based offset)
        elif ch is not None:
            wValue = mapping["wValue_base"] + (ch - 1)
        # Case 3: Global parameters (like monitor_bridge without channel)
        else:
            wValue = mapping["wValue_base"]

        return self.transport.ctrl_set(wValue, mapping["wIndex"], data)

    def _get_parameter(self, param_name: str, ch: Optional[int] = None, out_ch: Optional[int] = None) -> bytes:
        """
        Reads a raw USB control parameter directly from the hardware.

        Args:
            param_name (str): Identifier mapped in CATEGORY_TO_HARDWARE.
            ch (int, optional): Target channel if applicable.
            out_ch (int, optional): Target output channel (used with monitor)

        Returns:
            bytes: Raw byte payload returned by the hardware.
        """
        mapping = CATEGORY_TO_HARDWARE.get(param_name)
        if not mapping:
            logger.error(f"Unknown parameter: {param_name}")
            return b""

        if out_ch is not None and param_name == "monitor" and ch is not None:
            wValue = calculate_monitor_wValue(mapping["wValue_base"], ch, out_ch)
        elif ch is not None:
            wValue = mapping["wValue_base"] + (ch - 1)
        else:
            wValue = mapping["wValue_base"]

        return self.transport.ctrl_get(wValue, mapping["wIndex"], length=mapping["length"])


    # ---------------- Input controls ----------------

    @safe_usb_transaction
    def set_phantom(self, ch: int, state: bool) -> bool:
        """
        Sets the phantom power state for a specific input channel.

        Args:
            ch (int): The input channel number (1-based).
            state (bool): True to enable phantom power, False to disable.

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        state_byte = state.to_bytes(length=1)
        success = self._set_parameter("phantom", state_byte, ch)
        if success:
            self.state.update_input(ch, "phantom", state)
        return success

    @safe_usb_transaction
    def get_phantom(self, ch: int) -> bool:
        """
        Gets the phantom power state for a specific input channel.

        Args:
            ch (int): The input channel number (1-based).

        Returns:
            bool: True if phantom power is enabled, False otherwise.
        """
        state_byte = self._get_parameter("phantom", ch)
        return state_byte == b'\x01'

    @safe_usb_transaction
    def set_gain(self, ch: int, percent: int) -> bool:
        """
        Sets the gain for a specific input channel.

        Args:
            ch (int): The input channel number (1-based).
            percent (int): Gain in % (0-100).

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        if not is_in_percent_range(percent):
            logger.error(f"set_gain: Invalid gain value {percent}")
            return False
        gain_bytes = percent_to_gain_bytes(percent)
        success = self._set_parameter("gain", gain_bytes, ch)
        gain_db = gain_bytes_to_db(gain_bytes)
        if success:
            self.state.update_input(ch, "gain", gain_db)
        return success

    @safe_usb_transaction
    def get_gain(self, ch: int) -> int:
        """
        Gets the gain for a specific input channel.

        Args:
            ch (int): The input channel number (1-based).

        Returns:
            int: Gain in % (0-100).
        """
        gain_bytes = self._get_parameter("gain", ch)
        return gain_bytes_to_percent(gain_bytes)

    @safe_usb_transaction
    def set_gain_db(self, ch: int, gain_db: int) -> bool:
        """
        Sets the gain for a specific input channel.

        Args:
            ch (int): The input channel number (1-based).
            gain_db (int): Gain in dB (-2048 to 12800)

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        if gain_db not in range(-2048, 12800):
            logger.error(f"set_gain_db: Invalid gain value {gain_db}")
            return False
        gain_bytes = db_to_gain_bytes(gain_db)
        success = self._set_parameter("gain", gain_bytes, ch)
        if success:
            self.state.update_input(ch, "gain", gain_db)
        return success

    @safe_usb_transaction
    def get_gain_db(self, ch: int) -> int:
        """
        Gets the gain for a specific input channel.

        Args:
            ch (int): The input channel number (1-based).

        Returns:
            int: Gain in dB (-2048 to 12800)
        """
        gain_bytes = self._get_parameter("gain", ch)
        return gain_bytes_to_db(gain_bytes)

    @safe_usb_transaction
    def set_mic_mute(self, ch: int, state: bool) -> bool:
        """
        Sets the mute state for a specific input channel.

        Args:
            ch (int): The input channel number (1-based).
            state (bool): True to enable mute, False to disable.

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        state_byte = state.to_bytes(length=1)
        success = self._set_parameter("mic_mute", state_byte, ch)
        if success:
            self.state.update_input(ch, "mute", state)
        return success

    @safe_usb_transaction
    def get_mic_mute(self, ch: int) -> bool:
        """
        Gets the mute state for a specific input channel.

        Args:
            ch (int): The input channel number (1-based).

        Returns:
            bool: True if mute is enabled, False otherwise.
        """
        state_byte = self._get_parameter("mic_mute", ch)
        return state_byte == b'\x01'

    @safe_usb_transaction
    def set_mic_stereo(self, ch: int, state: bool) -> bool:
        """
        Sets the stereo state for a specific input channel.

        Args:
            ch (int): The input channel number (1-based).
            state (bool): True to enable stereo, False to disable.

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        state_byte = state.to_bytes(length=1)
        success = self._set_parameter("mic_stereo", state_byte, ch)
        if success:
            self.state.update_input(ch, "stereo_link", state)
        return success

    @safe_usb_transaction
    def get_mic_stereo(self, ch: int) -> bool:
        """
        Gets the stereo state for a specific input channel.

        Args:
            ch (int): The input channel number (1-based).

        Returns:
            bool: True if stereo is enabled, False otherwise.
        """
        state_byte = self._get_parameter("mic_stereo", ch)
        return state_byte == b'\x01'

    # ---------------- Output controls ----------------

    @safe_usb_transaction
    def set_volume(self, out_ch: int, volume: int) -> bool:
        """
        Sets the master output volume level for a specified output channel using percent.

        Args:
            volume (int): Volume value expressed as a percentage (0 - 100).
            out_ch (int): Output channel (1-based).

        Returns:
            bool: True if transaction was successful, False otherwise.
        """
        if not is_in_percent_range(volume):
            logger.error(f"set_volume: Invalid volume {volume}")
            return False

        volume_bytes = vol_step_to_bytes(percent_to_out_step(volume))
        success = self._set_parameter("volume", volume_bytes, ch=out_ch)

        if success:
            volume_db = decode_uac_volume(bytes(volume_bytes))
            self.state.update_output(out_ch, "volume", volume_db)

            if self.state.get_output(out_ch, "stereo_link"):
                partner = get_partner_channel(out_ch)
                self.state.update_output(partner, "volume", volume_db)

        return success

    @safe_usb_transaction
    def get_volume(self, out_ch: int) -> int:
        """
        Gets the current master output volume level for an output channel in percent.

        Args:
            out_ch (int): Output channel (1-based).

        Returns:
            int: Volume level value (0 - 100).
        """
        vol_bytes = self._get_parameter("volume", ch=out_ch)

        if not vol_bytes or len(vol_bytes) < 4:
            return -1

        #volume = out_step_to_percent(bytes_to_vol_step(vol_bytes))
        volume = bytes_to_vol_percent(vol_bytes)
        return volume

    @safe_usb_transaction
    def set_volume_db(self, out_ch: int, volume: float) -> bool:
        """
        Sets the master volume level for an output channel using decibels.

        Args:
            volume (float): Target volume level in dB (-128.00 - 0.00).
            out_ch (int): Output channel (1-based).

        Returns:
            bool: True if hardware sync succeeded, False otherwise.
        """
        round_vol = float(f"{volume:.2f}")
        if -128.00 > round_vol > 0.00:
            logger.error(f"set_volume_db: Invalid volume {round_vol}")
            return False

        volume_bytes = encode_uac_volume(round_vol)
        success = self._set_parameter("volume", volume_bytes, ch=out_ch)

        if success:
            self.state.update_output(out_ch, "volume", round_vol)

            if self.state.get_output(out_ch, "stereo_link"):
                partner = get_partner_channel(out_ch)
                self.state.update_output(partner, "volume", round_vol)

        return success

    @safe_usb_transaction
    def get_volume_db(self, out_ch: int) -> float:
        """
        Gets the current master output volume level for an output channel in decibel.

        Args:
            out_ch (int): Output channel (1-based).

        Returns:
            int: Volume level value (0 - 100).
        """
        vol_bytes = self._get_parameter("volume", ch=out_ch)

        if not vol_bytes or len(vol_bytes) < 4:
            return -1

        return float(f"{decode_uac_volume(vol_bytes):.2f}")

    @safe_usb_transaction
    def set_out_mute(self, out_ch: int, state: bool) -> bool:
        """
        Mutes or unmutes a specified output channel.

        Args:
            state (bool): True to mute, False to unmute.
            out_ch (int): Target output channel (1-based).

        Returns:
            bool: True if output mute state was updated successfully.
        """
        state_byte = state.to_bytes(length=1)
        success = self._set_parameter("out_mute", state_byte, out_ch)

        if success:
            self.state.update_output(out_ch, "mute", state)

            if self.state.get_output(out_ch, "stereo_link"):
                partner = get_partner_channel(out_ch)
                self.state.update_output(partner, "mute", state)

        return success

    @safe_usb_transaction
    def get_out_mute(self, out_ch: int) -> bool:
        """
        Retrieves the mute status of an output channel.

        Args:
            out_ch (int): Target output channel (1-based).

        Returns:
            bool: True if channel is muted, False otherwise.
        """
        state_byte = self._get_parameter("out_mute", out_ch)
        return state_byte == b'\x01'

    @safe_usb_transaction
    def set_out_stereo(self, out_ch: int, state: bool) -> bool:
        """
        Sets the stereo link state for an output channel pair.

        Args:
            out_ch (int): The channel from which the action originates. Essential for
                determining the link direction.
            state (bool): True to link the channels, False to unlink.

        Returns:
            bool: True if the hardware and state cache were updated successfully, False otherwise.
        """
        state_byte = state.to_bytes(length=1)
        # Send the link command (0x0200)
        success = self._set_parameter("out_stereo", state_byte, out_ch)

        if success:
            partner = get_partner_channel(out_ch)

            # 1. Update the link status for both channels in the cache
            self.state.update_output(out_ch, "stereo_link", state)
            self.state.update_output(partner, "stereo_link", state)

            # 2. When linking, the hardware copies the volume from 'ch' to 'partner'.
            #    the cache must now reflect this!
            if state:
                current_vol = self.state.get_output(out_ch, "volume")
                if current_vol != -1:
                    self.state.update_output(partner, "volume", current_vol)

        return success

    @safe_usb_transaction
    def get_out_stereo(self, out_ch: int) -> bool:
        """
        Queries whether an output channel is configured in a stereo link pair.

        Args:
            out_ch (int): Target output channel (1-based).

        Returns:
            bool: True if output channel pair is stereo linked, False otherwise.
        """
        state_byte = self._get_parameter("out_stereo", out_ch)
        return state_byte == b'\x01'

    # ---------------- Monitor Mixer ----------------

    def _sync_hardware_for_outputs(self, out_targets: list) -> bool:
        """
        Calculates the combined mix parameters and updates the hardware monitor nodes.

        Args:
            out_targets (list): List of target output channels (1-based) to re-calculate and sync.

        Returns:
            bool: True if all target mix outputs were updated successfully.
        """
        success = True

        for o_ch in out_targets:
            # Determine identity (Left or Right channel)
            is_linked = self.state.get_output(o_ch, "stereo_link")
            partner_ch = get_partner_channel(o_ch) if is_linked else o_ch
            base_out = min(o_ch, partner_ch) if is_linked else o_ch
            is_left = (o_ch == base_out)

            any_solo = any(
                self.state.get_monitor(x, o_ch, "solo")
                for x in range(1, self.profile.num_monitor_inputs + 1)
            )

            for i_ch in range(1, self.profile.num_monitor_inputs + 1):
                logical_vol_db = self.state.get_monitor(i_ch, o_ch, "volume")
                is_muted = self.state.get_monitor(i_ch, o_ch, "mute")
                is_solo = self.state.get_monitor(i_ch, o_ch, "solo")

                # Apply panning if the output is linked
                if is_linked and logical_vol_db not in (None, -1, -128.0):
                    # Read panning from cache (default is 0.5 = center)
                    pan = self.state.get_monitor(i_ch, base_out, "pan")
                    if pan is None:
                        pan = 0.5

                    # Linear panning calculation (attenuation)
                    mult = min(1.0, (1.0 - pan) * 2.0) if is_left else min(1.0, pan * 2.0)

                    if mult <= 0.001:
                        physical_vol_db = -128.00  # -Infinity dB / Mute
                    else:
                        attenuation_db = 20 * math.log10(mult)
                        physical_vol_db = max(-128.00, logical_vol_db + attenuation_db)
                else:
                    physical_vol_db = logical_vol_db

                # If muted, send -128 dB (UAC2 Mute) to the hardware
                if is_muted or (any_solo and not is_solo):
                    vol_to_send = -128.00
                else:
                    vol_to_send = physical_vol_db

                if vol_to_send in (None, -1):
                    continue

                monitor_bytes = encode_uac_volume(vol_to_send)
                if not self._set_parameter("monitor", monitor_bytes, i_ch, o_ch):
                    success = False
        return success

    @safe_usb_transaction
    def set_monitor(self, in_ch: int, out_ch: int, value: int) -> bool:
        """
        Sets the direct monitoring send level from an input to an output target.

        Args:
            value (int): Level value for the matrix routing node in percent (0 - 100).
            in_ch (int): Source monitor input channel (1-based).
            out_ch (int): Destination output channel (1-based).

        Returns:
            bool: True if hardware routing update succeeded.
        """

        if not is_in_percent_range(value):
            return False

        value_db = decode_uac_volume(bytes(mon_step_to_bytes(percent_to_mon_step(value))))

        in_targets = [in_ch]
        out_targets = [out_ch]

        # 1. Check Output Link (Mirrors the command to the right/left ear)
        if self.state.get_output(out_ch, "stereo_link"):
            out_targets.append(get_partner_channel(out_ch))

        # 2. Check Input Link (If Mic 1+2 are linked, include Mic 2 as well)
        if in_ch <= self.profile.num_inputs and self.state.get_input(in_ch, "stereo_link"):
            in_targets.append(get_partner_channel(in_ch))
        elif in_ch > self.profile.num_inputs and self.state.get_monitor_in(in_ch, "mode") != 0:
            # Digital channels (PC / Loopback) are typically stereo pairs by default
            in_targets.append(get_partner_channel(in_ch))

        # 3. Artist Mix Mirroring (1 to 3, 2 to 4)
        if not self.state.get_global("artist_mix") and any(c in (1, 2) for c in out_targets):
            if out_ch + 2 <= self.profile.num_outputs:
                for c in list(out_targets):
                    if c in (1, 2):
                        out_targets.append(c + 2)

        out_targets = list(set(out_targets))
        in_targets = list(set(in_targets))

        for i in in_targets:
            for o in out_targets:
                self.state.update_monitor(i, o, "volume", value_db)

        return self._sync_hardware_for_outputs(out_targets)

    @safe_usb_transaction
    def get_monitor(self, in_ch: int, out_ch: int) -> int:
        """
        Gets the direct monitoring send level for a specific matrix node.

        Args:
            in_ch (int): Source monitor input channel (1-based).
            out_ch (int): Destination output channel (1-based).

        Returns:
            int: Routing node monitor-volume value in percent (0 - 100).
        """

        monitor_vol_bytes = self._get_parameter("monitor", in_ch, out_ch)

        if monitor_vol_bytes == b'\x00\x00\xff\xff':
            return 0
        else:
            monitor_vol = mon_step_to_percent(bytes_to_mon_step(monitor_vol_bytes))
        return monitor_vol

    @safe_usb_transaction
    def set_monitor_db(self, in_ch: int, out_ch: int, value_db: float) -> bool:
        """
        Sets the direct monitoring send level from an input to an output target.

        Args:
            value_db (float): Level value for the matrix routing node in decibel (-128.00 - +8.00).
            in_ch (int): Source monitor input channel (1-based).
            out_ch (int): Destination output channel (1-based).

        Returns:
            bool: True if hardware routing update succeeded.
        """

        in_targets = [in_ch]
        out_targets = [out_ch]

        # 1. Check Output Link (Mirrors the command to the right/left ear)
        if self.state.get_output(out_ch, "stereo_link"):
            out_targets.append(get_partner_channel(out_ch))

        # 2. Check Input Link (If Mic 1 + Mic 2 are linked, include Mic 2 as well)
        if in_ch <= self.profile.num_inputs and self.state.get_input(in_ch, "stereo_link"):
            in_targets.append(get_partner_channel(in_ch))
        elif in_ch > self.profile.num_inputs and self.state.get_monitor_in(in_ch, "mode") != 0:
            # Digital channels (PC / Loopback) are typically stereo pairs by default
            in_targets.append(get_partner_channel(in_ch))

        # 3. Artist Mix Mirroring (1 to 3, 2 to 4)
        if not self.state.get_global("artist_mix") and any(c in (1, 2) for c in out_targets):
            for c in list(out_targets):
                if c in (1, 2):
                    out_targets.append(c + 2)

        out_targets = list(set(out_targets))
        in_targets = list(set(in_targets))

        for i in in_targets:
            for o in out_targets:
                self.state.update_monitor(i, o, "volume", float(f"{value_db:.2f}"))

        return self._sync_hardware_for_outputs(out_targets)

    @safe_usb_transaction
    def get_monitor_db(self, in_ch: int, out_ch: int) -> float:
        """
        Gets the direct monitoring send level for a specific matrix node.

        Args:
            in_ch (int): Source monitor input channel (1-based).
            out_ch (int): Destination output channel (1-based).

        Returns:
            float: Routing node monitor-volume value in decibel (-128.00 - +8.00).
        """

        monitor_vol_bytes = self._get_parameter("monitor", in_ch, out_ch)

        if monitor_vol_bytes == b'\x00\x00\xff\xff':
            return 0
        else:
            monitor_db = decode_uac_volume(monitor_vol_bytes)
        return float(f"{monitor_db:.2f}")

    @safe_usb_transaction
    def set_monitor_mute(self, in_ch: int, out_ch: int, state: bool) -> bool:
        """
        Sets mute state for a monitor node.


        Args:
            state (bool): True to enable mute, False to disable.
            in_ch (int): Source monitor input channel.
            out_ch (int): Destination output channel.

        Returns:
            bool: True if mute state updated successfully.
        """

        # 1. Input pair (if Stereo Link is active)
        in_targets = [in_ch]
        if in_ch <= self.profile.num_inputs and self.state.get_input(in_ch, "stereo_link"):
            in_targets.append(get_partner_channel(in_ch))
        elif in_ch > self.profile.num_inputs and self.state.get_monitor_in(in_ch, "mode") != 0:
            in_targets.append(get_partner_channel(in_ch))

        # 2. Output pair (A mix is ALWAYS L+R)
        base_out = out_ch if out_ch % 2 != 0 else out_ch - 1
        out_targets = [base_out, base_out + 1]

        if not self.state.get_global("artist_mix") and out_targets == [1, 2]:
            out_targets.extend([3, 4])

        for i in set(in_targets):
            for o in set(out_targets):
                self.state.update_monitor(i, o, "mute", state)

        return self._sync_hardware_for_outputs(list(set(out_targets)))

    @safe_usb_transaction
    def set_monitor_solo(self, in_ch: int, out_ch: int, state: bool) -> bool:
        """
        Sets solo state for a monitor node.

        Args:
            state (bool): True to enable soloing, False to disable.
            in_ch (int): Source monitor input channel.
            out_ch (int): Destination output channel.

        Returns:
            bool: True if solo state updated successfully.
        """

        in_targets = [in_ch]
        if in_ch <= self.profile.num_inputs and self.state.get_input(in_ch, "stereo_link"):
            in_targets.append(get_partner_channel(in_ch))
        elif in_ch > self.profile.num_inputs:
            in_targets.append(get_partner_channel(in_ch))

        base_out = out_ch if out_ch % 2 != 0 else out_ch - 1
        out_targets = [base_out, base_out + 1]

        if not self.state.get_global("artist_mix") and out_targets == [1, 2]:
            out_targets.extend([3, 4])

        for i in set(in_targets):
            for o in set(out_targets):
                self.state.update_monitor(i, o, "solo", state)

        return self._sync_hardware_for_outputs(list(set(out_targets)))

    def get_monitor_mute(self, in_ch: int, out_ch: int) -> bool:
        """
        Reads the current mute state of a specific monitor matrix node.

        Args:
            in_ch (int): Source monitor input channel.
            out_ch (int): Destination output channel.

        Returns:
            bool: True if node is muted, False otherwise.
        """

        return self.state.get_monitor(in_ch, out_ch, "mute")

    def get_monitor_solo(self, in_ch: int, out_ch: int) -> bool:
        """
        Reads the current solo state of a specific monitor matrix node.

        Args:
            in_ch (int): Source monitor input channel.
            out_ch (int): Destination output channel.

        Returns:
            bool: True if node is soloed, False otherwise.
        """
        return self.state.get_monitor(in_ch, out_ch, "solo")

    @safe_usb_transaction
    def set_monitor_pan(self, in_ch: int, out_ch: int, pan: float) -> bool: # TODO: FINISH Monitor-Input logic
        """
        Sets the panning of a monitor node to state-cache, then syncs cache to hardware

        Args:
            pan (float): Panning of a monitor node (0.00 = Left, 0.50 = Center, 1.00 = Right).
            in_ch (int): Source monitor input channel.
            out_ch (int): Destination output channel.

        Returns:
            bool: True if pan value updated successfully, False otherwise.
        """

        if not (0.0 <= pan <= 1.0):
            return False

        out_targets = [out_ch]

        if self.state.get_output(out_ch, "stereo_link"):
            out_targets.append(get_partner_channel(out_ch))

        if not self.state.get_global("artist_mix") and any(c in (1, 2) for c in out_targets):
            for c in list(out_targets):
                if c in (1, 2):
                    out_targets.append(c + 2)

        out_targets = list(set(out_targets))

        # Store pan in cache for all associated outputs
        for o in out_targets:
            self.state.update_monitor(in_ch, o, "pan", pan)

        # Panning changes the actual volume, so it has to sync the hardware
        return self._sync_hardware_for_outputs(out_targets)

    def get_monitor_pan(self, in_ch: int, out_ch: int) -> float:
        """
        Gets the panning of a monitor node from state-cache.

        Args:
            in_ch (int): Source monitor input channel.
            out_ch (int): Destination output channel.

        Returns:
            float: Panning of a monitor node (0.00 = Left, 0.50 = Center, 1.00 = Right).
        """

        return self.state.get_monitor(in_ch, out_ch, "pan")

    def set_monitor_in_stereo(self, in_ch:int, state: bool): # TODO: make it sync and add decorator
        """
        Sets the stereo state for a specific monitor input channel to state cache.

        Args:
            in_ch (int): The monitor input channel number (1-based).
            state (bool): True to enable stereo, False to disable.

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """

        if not state:
            self.state.update_monitor_in(in_ch, "mode", 0)
        else:
            if in_ch % 2 == 0:
                self.state.update_monitor_in(in_ch, "mode", 1)
            else:
                self.state.update_monitor_in(in_ch, "mode", 2)


    def get_monitor_in_stereo(self, in_ch:int) -> bool:
        """
        Gets the stereo state for a specific monitor input channel from state cache.

        Args:
            in_ch (int): The monitor input channel number (1-based).

        Returns:
            bool: True if stereo is enabled, False otherwise.
        """
        mode = self.state.get_monitor_in(in_ch, "mode")
        if mode == 1 or mode == 2:
            return True
        return False

    def set_monitor_in_mute(self, in_ch:int, state: bool) -> bool: # TODO: make it sync and add decorator
        """
        Sets the mute state for a specific monitor input channel to state cache.

        Args:
            in_ch (int): The monitor input channel number (1-based).
            state (bool): True to enable mute, False to disable.

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        raise NotImplemented

    def get_monitor_in_mute(self, in_ch:int) -> bool:
        """
        Gets the mute state for a specific monitor input channel from state cache.

        Args:
            in_ch (int): The monitor input channel number (1-based).

        Returns:
            bool: True if mute is enabled, False otherwise.
        """
        raise NotImplemented

    def set_monitor_in_name(self, in_ch:int, name: str) -> bool:
        """
        Sets the name for a specific monitor input channel to state cache.

        Args:
            in_ch (int): The monitor input channel number (1-based).
            name (str): The name of a monitor input channel.

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        raise NotImplemented

    def get_monitor_in_name(self, in_ch:int) -> str:
        """
        Gets the name of a specific monitor input channel from state cache.

        Args:
            in_ch (int): The monitor input channel number (1-based).

        Returns:
            str: The name of a monitor input channel.
        """
        raise NotImplemented

    def set_artist_mix(self, state: bool) -> bool: #TODO: actually restructure to utilize success values, currently can only return True
        """
        Enables or disables Artist Mix in State-Cache.

        Args:
            state (bool): True to activate Artist Mix mode, False to deactivate.

        Returns:
            bool: True if mode state was sent successfully.
        """
        self.state.update_global("artist_mix", state)

        if not state:
            if self.profile.num_outputs >= 4:
                # Artist Mix off: overwrite 3+4 with 1+2 in state
                for in_ch in range(1, self.profile.num_monitor_inputs + 1):
                    for src, dst in [(1, 3), (2, 4)]:
                        vol = self.state.get_monitor(in_ch, src, "volume")
                        if vol not in (None, -1):
                            self.state.update_monitor(in_ch, dst, "volume", vol)
                            self.state.update_monitor(in_ch, dst, "mute", self.state.get_monitor(in_ch, src, "mute"))
                            self.state.update_monitor(in_ch, dst, "solo", self.state.get_monitor(in_ch, src, "solo"))

                self._sync_hardware_for_outputs([3, 4])
        return True

    def get_artist_mix(self) -> bool:
        """
        Get Artist Mix from State-Cache

        Returns:
            bool: True if Artist Mix is enabled, False otherwise.
        """
        return self.state.get_global("artist_mix")

    # ---------------- Loopback ----------------

    @safe_usb_transaction
    def set_loopback_source(self, loopback_source: str) -> bool:
        """
        Configures the source stream assigned to the loopback input channel pair.

        Args:
            loopback_source (str): Descriptive source identifier ('PC1+2', 'PC3+4', 'LB1+2', 'MM1+2', 'AM1+2').

        Returns:
            bool: True if loopback routing changed successfully.
        """
        if loopback_source not in LOOPBACK_SOURCES:
            raise ValueError(f"Invalid loopback source. Supported: {list(LOOPBACK_SOURCES.keys())}")

        data_left, data_right = LOOPBACK_SOURCES[loopback_source]

        success = self._set_parameter("loopback_left", data=data_left)
        if success:
            success = self._set_parameter("loopback_right", data=data_right)

        if success:
            self.state.update_global("loopback_source", loopback_source)

        return success

    @safe_usb_transaction
    def get_loopback_source(self) -> str:
        """
        Retrieves the active source mapped to the hardware loopback channels.

        Returns:
            str: Currently assigned loopback source name ('PC1+2', 'PC3+4', 'LB1+2', 'MM1+2', 'AM1+2').
        """

        loopback_byte_left = self._get_parameter("loopback_left")
        loopback_byte_right = self._get_parameter("loopback_right")

        return LOOPBACK_MAPPINGS_INV.get((loopback_byte_left, loopback_byte_right), "Unknown loopback group")

    @safe_usb_transaction
    def get_loopback_source_left(self) -> bytes:
        """
        Retrieves the source byte mapped to the left loopback channel.

        Returns:
            bytes: Active source identifier for left loopback channel.
        """
        return self._get_parameter("loopback_left")

    @safe_usb_transaction
    def get_loopback_source_right(self) -> bytes:
        """
        Retrieves the source byte mapped to the right loopback channel.

        Returns:
            bytes: Active source identifier for right loopback channel.
        """
        return self._get_parameter("loopback_right")

    # ---------------- Sample Rate ----------------

    @safe_usb_transaction
    def get_sample_rate(self) -> int:
        """
        Reads the currently configured operating sample rate from the device.

        Returns:
            int: Active sample rate in Hz (44100, 48000, 88200, 96000).
        """
        sample_rate_bytes = self._get_parameter("sample_rate")
        return SAMPLE_RATES_INV.get(sample_rate_bytes, -1)

    @safe_usb_transaction
    def set_sample_rate(self, sample_rate:int) -> bool:
        """
        Changes the operating audio sample rate on the hardware device.

        Args:
            sample_rate (int): Desired sample rate in Hz. (44100,48000,88200,96000)

        Returns:
            bool: True if sample rate was switched successfully, False otherwise.
        """
        if sample_rate not in SAMPLE_RATES:
            raise ValueError(f"Unsupported sample rate {sample_rate}. Supported: {list(SAMPLE_RATES.keys())}")
        success = self._set_parameter("sample_rate", data=SAMPLE_RATES[sample_rate])
        if success:
            self.state.update_global("sample_rate", sample_rate)
        return success

    # ---------------- Events ----------------

    @safe_usb_transaction
    def event_listen(self) -> Optional[bytes]:
        """
        Gets event from hardware buffer-stack

        Returns:
            bytes, optional: Raw event byte packet if received, None on timeout.
        """
        return self._get_parameter("get_event")


    def event_changed(self, new_state: bytes) -> bool:
        """
        Compares new buffer element with the last.

        Return:
            bool: True if the new buffer element differs from the one before (saved in self._last_state), False otherwise.

        Args:
            new_state (bytes): Raw payload received from event_listen.
        """
        if new_state != self._last_state:
            self._last_state = new_state
            return True
        return False