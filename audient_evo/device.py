# ============================================================
# audient_evo/device.py
# ============================================================
"""
High-dial EVO 8 device API.
This is the primary interface intended for UI and scripting.
"""
import time
import threading
import queue
from typing import Optional, Callable
from concurrent.futures import Future
import math

from functools import wraps

from .protocol import LOOPBACK_SOURCES, SAMPLE_RATES, \
    SAMPLE_RATE_INV, LOOPBACK_MAPPINGS_INV, CATEGORY_TO_HARDWARE
from .transport import EvoUsbTransport
from .state import EvoStateManager
from .worker import EvoBackgroundWorker
from .util import mon_step_to_bytes, \
    percent_to_mon_step, bytes_to_mon_step, \
    bytes_to_vol_step, is_in_percent_range, out_step_to_percent, vol_step_to_bytes, \
    percent_to_out_step, get_partner_channel, calculate_monitor_wValue, \
    mon_step_to_percent, gain_bytes_to_percent, percent_to_gain_bytes, encode_uac_volume, decode_uac_volume, \
    gain_bytes_to_db, db_to_gain_bytes

import logging

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
    Decorator that combines thread safety AND error handling (Try/Catch)
    for USB transactions.
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
        if self.transport.is_connected() and not self.transport.ghost_mode:
            return True
        try:
            self.transport.connect()
            self.worker.start()
            if not force_hardware_sync and self._is_cache_populated():
                self.push_cache_to_hardware()
            else:
                self._initialize_state_from_hardware()
            return True
        except Exception as e:
            logger.exception(f"Error connecting to hardware: {e}")
            self.transport.ghost_mode = True
            return False

    def disconnect_hardware(self) -> None:
        """Stop Communication, give back to ALSA and turn on Ghost Mode."""
        if self.transport.ghost_mode: return
        logger.info("Giving Hardware back to ALSA (Ghost Mode)...")
        self.worker.stop()
        self.transport.release()

        # ---------------- INITIALISATION ----------------

    def _initialize_state_from_hardware(self):
        """Reads all important values live from the device at startup to populate the cache."""
        logger.info("Synchronizing initial state from hardware...")
        success = True
        try:
            self.set_monitor(0, 10, 20)  # Wakeup monitor by calling out of range monitor address
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

    def push_cache_to_hardware(self) -> bool:
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
                self.set_volume_db(vol, ch)

            mute = self.state.get_output(ch, "mute")
            if mute is not None:
                self.set_out_mute(mute, ch)

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
        """Central function for sending USB values based on the dictionary."""
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
        """Central function for querying USB values based on the dictionary."""
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
        state_byte = state.to_bytes(length=1)
        success = self._set_parameter("phantom", state_byte, ch)
        if success:
            self.state.update_input(ch, "phantom", state)
        return success

    @safe_usb_transaction
    def get_phantom(self, ch: int) -> bool:
        state_byte = self._get_parameter("phantom", ch)
        return state_byte == b'\x01'

    @safe_usb_transaction
    def set_gain(self, ch: int, value: int) -> bool:
        if not is_in_percent_range(value):
            logger.error(f"set_gain: Invalid gain value {value}")
            return False
        gain_bytes = percent_to_gain_bytes(value)
        success = self._set_parameter("gain", gain_bytes, ch)
        gain_db = gain_bytes_to_db(gain_bytes)
        if success:
            self.state.update_input(ch, "gain", gain_db)
        return success

    @safe_usb_transaction
    def get_gain(self, ch: int) -> int:
        gain_bytes = self._get_parameter("gain", ch)
        return gain_bytes_to_percent(gain_bytes)

    @safe_usb_transaction
    def set_gain_db(self, ch: int, gain_db: int) -> bool:
        """Gain dB Range is -2048 - 12800"""
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
        gain_bytes = self._get_parameter("gain", ch)
        return gain_bytes_to_db(gain_bytes)

    @safe_usb_transaction
    def set_mic_mute(self, ch: int, state: bool) -> bool:
        state_byte = state.to_bytes(length=1)
        success = self._set_parameter("mic_mute", state_byte, ch)
        if success:
            self.state.update_input(ch, "mute", state)
        return success

    @safe_usb_transaction
    def get_mic_mute(self, ch: int) -> bool:
        state_byte = self._get_parameter("mic_mute", ch)
        return state_byte == b'\x01'

    @safe_usb_transaction
    def set_mic_stereo(self, ch: int, state: bool) -> bool:
        state_byte = state.to_bytes(length=1)
        success = self._set_parameter("mic_stereo", state_byte, ch)
        if success:
            self.state.update_input(ch, "stereo_link", state)
        return success

    @safe_usb_transaction
    def get_mic_stereo(self, ch: int) -> bool:
        state_byte = self._get_parameter("mic_stereo", ch)
        return state_byte == b'\x01'

    # ---------------- Output controls ----------------

    @safe_usb_transaction
    def set_volume(self, volume: int, out_ch: int) -> bool:
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
    def set_volume_db(self, volume: float, out_ch: int) -> bool:
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
    def get_volume_db(self, out_ch: int):
        vol_bytes = self._get_parameter("volume", ch=out_ch)

        if not vol_bytes or len(vol_bytes) < 4:
            return -1

        return float(f"{decode_uac_volume(vol_bytes):.2f}")

    @safe_usb_transaction
    def get_volume(self, out_ch: int) -> int:
        vol_bytes = self._get_parameter("volume", ch=out_ch)

        if not vol_bytes or len(vol_bytes) < 4:
            return -1

        volume = out_step_to_percent(bytes_to_vol_step(vol_bytes))    # appears to work
        return volume

    @safe_usb_transaction
    def set_out_mute(self, state: bool, out_ch: int) -> bool:
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
        state_byte = self._get_parameter("out_mute", out_ch)
        return state_byte == b'\x01'

    @safe_usb_transaction
    def set_out_stereo(self, out_ch: int, enable: bool) -> bool:
        """
        Toggles Mono/Stereo.
        ch: The channel from which the action originates (important when enabling the link!)
        """
        state_byte = enable.to_bytes(length=1)
        # Send the link command (0x0200)
        success = self._set_parameter("out_stereo", state_byte, out_ch)

        if success:
            partner = get_partner_channel(out_ch)

            # 1. Update the link status for both channels in the cache
            self.state.update_output(out_ch, "stereo_link", enable)
            self.state.update_output(partner, "stereo_link", enable)

            # 2. When linking, the hardware copies the volume from 'ch' to 'partner'.
            #    the cache must now reflect this!
            if enable:
                current_vol = self.state.get_output(out_ch, "volume")
                if current_vol != -1:
                    self.state.update_output(partner, "volume", current_vol)

        return success

    @safe_usb_transaction
    def get_out_stereo(self, out_ch: int) -> bool:
        state_byte = self._get_parameter("out_stereo", out_ch)
        return state_byte == b'\x01'

    # ---------------- Monitor Mixer ----------------

    def _sync_hardware_for_outputs(self, out_targets: list) -> bool:
        """Calculates the mix (Volume vs. Mute/Solo/Pan) and sends it to the device."""
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
    def set_monitor(self, value: int, in_ch: int, out_ch: int) -> bool:
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
        elif in_ch > self.profile.num_inputs:
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

        # Update cache with the master volume
        for i in in_targets:
            for o in out_targets:
                self.state.update_monitor(i, o, "volume", value_db)

        # Synchronize hardware
        return self._sync_hardware_for_outputs(out_targets)

    @safe_usb_transaction
    def get_monitor(self, in_ch: int, out_ch: int) -> int:
        monitor_vol_bytes = self._get_parameter("monitor", in_ch, out_ch)

        if monitor_vol_bytes == b'\x00\x00\xff\xff':
            return 0
        else:
            monitor_vol = mon_step_to_percent(bytes_to_mon_step(monitor_vol_bytes))
        return monitor_vol

    @safe_usb_transaction
    def set_monitor_db(self, value_db: float, in_ch: int, out_ch: int) -> bool:
        in_targets = [in_ch]
        out_targets = [out_ch]

        # 1. Check Output Link (Mirrors the command to the right/left ear)
        if self.state.get_output(out_ch, "stereo_link"):
            out_targets.append(get_partner_channel(out_ch))

        # 2. Check Input Link (If Mic 1+2 are linked, include Mic 2 as well)
        if in_ch <= self.profile.num_inputs and self.state.get_input(in_ch, "stereo_link"):
            in_targets.append(get_partner_channel(in_ch))
        elif in_ch > self.profile.num_inputs:
            # Digital channels (PC / Loopback) are typically stereo pairs by default
            in_targets.append(get_partner_channel(in_ch))

        # 3. Artist Mix Mirroring (1 to 3, 2 to 4)
        if not self.state.get_global("artist_mix") and any(c in (1, 2) for c in out_targets):
            for c in list(out_targets):
                if c in (1, 2):
                    out_targets.append(c + 2)

        out_targets = list(set(out_targets))
        in_targets = list(set(in_targets))

        # Update cache with the master volume
        for i in in_targets:
            for o in out_targets:
                self.state.update_monitor(i, o, "volume", float(f"{value_db:.2f}"))

        # Synchronize hardware
        return self._sync_hardware_for_outputs(out_targets)

    @safe_usb_transaction
    def get_monitor_db(self, in_ch: int, out_ch: int) -> float:
        monitor_vol_bytes = self._get_parameter("monitor", in_ch, out_ch)

        if monitor_vol_bytes == b'\x00\x00\xff\xff':
            return 0
        else:
            monitor_db = decode_uac_volume(monitor_vol_bytes)
        return float(f"{monitor_db:.2f}")

    @safe_usb_transaction
    def set_monitor_mute(self, state: bool, in_ch: int, out_ch: int) -> bool:
        # 1. Input pair (if Stereo Link is active)
        in_targets = [in_ch]
        if in_ch <= self.profile.num_inputs and self.state.get_input(in_ch, "stereo_link"):
            in_targets.append(get_partner_channel(in_ch))
        elif in_ch > self.profile.num_inputs:
            in_targets.append(get_partner_channel(in_ch))  # Digital immer Stereo

        # 2. Output pair (A mix is ALWAYS L+R)
        base_out = out_ch if out_ch % 2 != 0 else out_ch - 1
        out_targets = [base_out, base_out + 1]

        # 3. Artist Mix Mirroring
        if not self.state.get_global("artist_mix") and out_targets == [1, 2]:
            out_targets.extend([3, 4])

        # 4. Update state for the entire channel strip
        for i in set(in_targets):
            for o in set(out_targets):
                self.state.update_monitor(i, o, "mute", state)

        return self._sync_hardware_for_outputs(list(set(out_targets)))

    @safe_usb_transaction
    def set_monitor_solo(self, state: bool, in_ch: int, out_ch: int) -> bool:
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
        return self.state.get_monitor(in_ch, out_ch, "mute")

    def get_monitor_solo(self, in_ch: int, out_ch: int) -> bool:
        return self.state.get_monitor(in_ch, out_ch, "solo")

    @safe_usb_transaction
    def set_monitor_pan(self, pan: float, in_ch: int, out_ch: int) -> bool:
        """
        Set the panning (0.0 = Left, 0.5 = Center, 1.0 = Right).
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
        return self.state.get_monitor(in_ch, out_ch, "pan")

    def set_artist_mix(self, enable: bool) -> bool:
        self.state.update_global("artist_mix", enable)

        if not enable:
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
        return self.state.get_global("artist_mix")

    # ---------------- Loopback ----------------

    @safe_usb_transaction
    def get_loopback_source(self) -> str:
        # Unpack addresses from the dictionary
        wValue_left, wValue_right = 0x0604, 0x0605

        # Query values from the hardware
        loopback_byte_left = self.transport.ctrl_get(wValue_left, 0x3300, length=1)
        loopback_byte_right = self.transport.ctrl_get(wValue_right, 0x3300, length=1)

        return LOOPBACK_MAPPINGS_INV.get((loopback_byte_left, loopback_byte_right), "Unknown loopback group")

    @safe_usb_transaction
    def get_loopback_source_left(self) -> bytes:
        return self.transport.ctrl_get(0x0604, 0x3300, length=1)

    @safe_usb_transaction
    def get_loopback_source_right(self) -> bytes:
        return self.transport.ctrl_get(0x0605, 0x3300, length=1)

    @safe_usb_transaction
    def set_loopback_source(self, loopback_source: str) -> bool:
        if loopback_source not in LOOPBACK_SOURCES:
            raise ValueError(f"Invalid loopback source. Supported: {list(LOOPBACK_SOURCES.keys())}")

        # Cleanly unpack addresses and data bytes from the dictionaries
        wValue_left, wValue_right = 0x0604, 0x0605
        data_left, data_right = LOOPBACK_SOURCES[loopback_source]

        # Write both channels
        success = self.transport.ctrl_set(wValue_left, 0x3300, data_left)
        if success:
            success = self.transport.ctrl_set(wValue_right, 0x3300, data_right)

        if success:
            self.state.update_global("loopback_source", loopback_source)

        return success

    # ---------------- Sample Rate ----------------

    @safe_usb_transaction
    def get_sample_rate(self) -> int:
        sr_bytes = self.transport.ctrl_get(0x0100,0x2900, 4)
        return SAMPLE_RATE_INV.get(sr_bytes, -1)

    @safe_usb_transaction
    def set_sample_rate(self, sr:int) -> bool:
        if sr not in SAMPLE_RATES:
            raise ValueError(f"Unsupported sample rate {sr}. Supported: {list(SAMPLE_RATES.keys())}")
        success = self.transport.ctrl_set(0x0100, 0x2900, SAMPLE_RATES[sr])
        if success:
            self.state.update_global("sample_rate", sr)
        return success

    # ---------------- Events ----------------

    @safe_usb_transaction
    def event_listen(self) -> Optional[bytes]:
        return self.transport.ctrl_get(0x0600, 0x3E00, 4, 500)

    def event_changed(self, new_state: bytes) -> bool:
        if new_state != self._last_state:
            self._last_state = new_state
            return True
        return False