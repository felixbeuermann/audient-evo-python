# ============================================================
# audient_evo/state.py
# ============================================================
"""
State Management for EVO 8.
Handles only the data structure, caching, and validation of the device state.
No USB or Threading logic lives here.
"""

from dataclasses import dataclass, asdict
from typing import Dict, Optional, Any
import logging
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

# --- Datenstrukturen (Dataclasses) ---

@dataclass
class InputState:
    gain: int = -1
    phantom: bool = False
    mic_mute: bool = False
    stereo_link: bool = False


@dataclass
class OutputState:
    volume: int = -1
    out_mute: bool = False
    stereo_link: bool = True

@dataclass
class MonitorState:
    volume: int = -1
    pan: int = 0    # between 0 and 1 in mono both are centered at 0.50 and in stereo l is 0.00 and r is 1.00
    mute: bool = False
    solo: bool = False   # Still have to figure out how I want to implement
    stereo_link: bool = True    # Let's assume this behaves like the output stereo link

@dataclass
class GlobalState:
    loopback_source: Optional[str] = None
    loopback_target: Optional[str] = None
    sample_rate: int = -1

class EvoStateManager:
    """Manages the entire known state of the EVO 8."""

    def __init__(self):
        # --- Inputs (1-4) ---
        self.inputs: Dict[int, InputState] = {
            ch: InputState() for ch in range(1, 5)
        }

        # --- Outputs (1-4) ---
        self.outputs: Dict[int, OutputState] = {
            i: OutputState() for i in range(1, 5)
        }

        # --- Hardware-Monitor/DSP-Channels (1-40) ---
        self.monitors: Dict[int, MonitorState] = {
            i: MonitorState() for i in range(1, 41)
        }

        # --- Global State ---
        self.globals = GlobalState()

    # ---------------- INPUTS ----------------

    def update_input(self, ch: int, key: str, value: Any) -> None:
        """Updates a specific value of an input channel."""
        if ch in self.inputs:
            if hasattr(self.inputs[ch], key):
                setattr(self.inputs[ch], key, value)
            else:
                logger.warning(f"Unknown input attribute: {key}")

    def get_input(self, ch: int, key: str) -> Any:
        """Retrieves a value from the input cache."""
        if ch in self.inputs:
            return getattr(self.inputs[ch], key, None)
        return None

    # ---------------- OUTPUTS ----------------

    def update_output(self, out_ch: int, key: str, value: Any) -> None:
        if out_ch in self.outputs:
            if hasattr(self.outputs[out_ch], key):
                setattr(self.outputs[out_ch], key, value)
            else:
                logger.warning(f"Unknown output attribute: {key}")

    def get_output(self, out_ch: int, key: str) -> Any:
        if out_ch in self.outputs:
            return getattr(self.outputs[out_ch], key, None)
        return None

    # ---------------- MONITOR ----------------

    def update_monitor(self, in_ch: int , out_ch: int, key: str, value: any):
        ch = (in_ch - 1) * 4 + (out_ch - 1)
        if ch in self.monitors and hasattr(self.monitors[ch], key):
            setattr(self.monitors[ch], key, value)

    def get_monitor(self, in_ch: int , out_ch: int, key: str) -> any:
        ch = (in_ch - 1) * 4 + (out_ch - 1)
        if ch in self.monitors and hasattr(self.monitors[ch], key):
            return getattr(self.monitors[ch], key)
        return None

    # ---------------- GLOBALS ----------------

    def update_global(self, key: str, value: Any) -> None:
        if hasattr(self.globals, key):
            setattr(self.globals, key, value)
        else:
            logger.warning(f"Unknown global attribute: {key}")

    def get_global(self, key: str) -> Any:
        return getattr(self.globals, key, None)

    # ---------------- EXPORT / IMPORT (Bonus) ----------------

    def to_dict(self) -> dict:
        """Exports the entire state as a standard dictionary (e.g., for JSON/GUI)."""
        return {
            "inputs": {ch: asdict(state) for ch, state in self.inputs.items()},
            "outputs": {f"{ch[0]}+{ch[1]}": asdict(state) for ch, state in self.outputs.items()},
            "monitor": {f"{k[0]}->{k[1][0]}+{k[1][1]}": v for k, v in self.monitors.items()},
            "globals": asdict(self.globals)
        }

    def print_cache(self) -> None:
        """
        Prints the entire current cache state (StateManager)
        in a clearly formatted way to the terminal.
        """

        print("\n" + "=" * 55)
        print("🎛️   EVO 8 CURRENT STATE CACHE   🎛️".center(50))
        print("=" * 55)

        # --- Globals ---
        print("\n[ GLOBALS ]")
        sr = self.get_global('sample_rate')
        lb_source = self.get_global('loopback_source')
        lb_target = self.get_global('loopback_target')
        print(f"  Sample Rate : {sr if sr != -1 else 'Unbekannt'} Hz")
        print(f"  Loopback_source    : {lb_source if lb_source else 'Nicht gesetzt'}")
        print(f"  Loopback_target    : {lb_target if lb_target else 'Nicht gesetzt'}")

        # --- Inputs ---
        print("\n[ INPUTS (1-4) ]")
        for ch, inp in self.inputs.items():
            # Formatting: Values right-aligned for a clean table
            gain_str = f"{inp.gain:>3}%" if inp.gain != -1 else "N/A "
            print(f"  IN {ch} | Gain: {gain_str} | 48V: {'ON' if inp.phantom else 'OFF':<3} | "
                  f"Mute: {'ON' if inp.mic_mute else 'OFF':<3} | Link: {'ON' if inp.stereo_link else 'OFF':<3}")

        # --- Outputs ---
        print("\n[ OUTPUTS (1-4) ]")
        for ch, out in self.outputs.items():
            vol_str = f"{out.volume:>3}%" if out.volume != -1 else "N/A "
            print(f"  OUT {ch}| Vol: {vol_str}  | Mute: {'ON' if out.out_mute else 'OFF':<3} | "
                  f"Link: {'ON' if out.stereo_link else 'OFF':<3}")

        # --- Monitor Matrix ---
        print("\n[ MONITOR MATRIX (10 Inputs -> 4 Outputs) ]")
        print("          OUT 1   OUT 2   OUT 3   OUT 4")
        print("         -------------------------------")

        # We loop over the 10 internal inputs (including PC and Loopback)
        for in_ch in range(1, 11):
            mon_vols = []
            for out_ch in range(1, 5):
                #print(f"in_ch: {in_ch}, out_ch: {out_ch}, mon_ch: {(in_ch - 1) * 4 + (out_ch - 1)}")
                vol = self.get_monitor(in_ch, out_ch, "volume")
                # If the value has never been read (None or -1), we display "---"
                if vol is None or vol == -1:
                    vol_str = "---"
                else:
                    vol_str = f"{vol:>3}%"
                mon_vols.append(vol_str)

            # Output the line per input
            print(f"  IN {in_ch:>2} |  {'    '.join(mon_vols)}")

        print("\n" + "=" * 55 + "\n")



    def import_from_evo_xml(self, xml_string: str) -> bool:
        """
        Reads an official EVO XML save file.
        Caution: Requires conversion of raw values (e.g., gain="3584" -> percent).
        """
        try:
            root = ET.fromstring(xml_string)
            device = root.find("preset/device")
            if device is None: return False

            # Read globals
            settings = device.find("driver-settings")
            if settings is not None:
                sr = int(settings.get("sample-rate", -1))
                self.update_global("sample_rate", sr)

            # Read inputs (Caution: index 0 = Channel 1)
            inputs_node = device.find("inputs")
            if inputs_node is not None:
                for inp in inputs_node.findall("input"):
                    ch = int(inp.get("index", -1)) + 1

                    # Here you would need to call your conversion functions!
                    # raw_gain = int(inp.get("gain", 0))
                    # percent_gain = deine_logik(raw_gain)

                    self.update_input(ch, "phantom", inp.get("phantom") == "1")
                    self.update_input(ch, "mic_mute", inp.get("mute") == "1")
                    self.update_input(ch, "mono", inp.get("link") == "1")

            logger.info("XML state successfully imported.")
            return True
        except Exception as e:
            logger.error(f"Error during XML import: {e}")
            return False