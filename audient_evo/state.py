# ============================================================
# audient_evo/state.py
# ============================================================
"""
State Management for EVO 8.
Handles only the data structure, caching, and validation of the device state.
No USB or Threading logic lives here.
"""

from dataclasses import dataclass
import logging

from audient_evo.protocol import DeviceCapabilities

logger = logging.getLogger(__name__)

@dataclass
class InputState:
    gain: int = -1
    phantom: bool = False
    mute: bool = False
    stereo_link: bool = False


@dataclass
class OutputState:
    volume: int = -1
    mute: bool = False
    stereo_link: bool = True

@dataclass
class MatrixNode:
    """Represents a single crosspoint in the 10x4 matrix"""
    volume: int = -1       # Raw value or percentage
    pan: float = 0.5       # 0.0 (Left) to 1.0 (Right), 0.5 is Center
    mute: bool = False     # Software mute for this specific mix
    solo: bool = False     # Solo status

@dataclass
class GlobalState:
    loopback_source: str = None
    loopback_target: str = None
    sample_rate: int = -1

class EvoStateManager:
    """Manages the entire known state of the EVO 8."""

    def __init__(self, capabilities: DeviceCapabilities):
        self.capabilities = capabilities

        # --- Inputs (1-4) ---
        self.inputs = {ch: InputState() for ch in range(1, self.capabilities.num_inputs+1)}

        # --- Outputs (1-4) ---
        self.outputs = {ch: OutputState() for ch in range(1, self.capabilities.num_outputs+1)}

        # --- Hardware-Monitor/DSP-Channels (10 Inputs -> 4 Outputs) ---
        self.matrix = {}
        for in_ch in range(1, self.capabilities.num_monitor_inputs+1):
            for out_ch in range(1, self.capabilities.num_outputs+1):
                self.matrix[(in_ch, out_ch)] = MatrixNode()

        # --- Global State ---
        self.globals = GlobalState()

    # ---------------- INPUTS ----------------

    def update_input(self, ch: int, key: str, value) -> None:
        """Updates a specific value of an input channel."""
        if ch in self.inputs:
            if hasattr(self.inputs[ch], key):
                setattr(self.inputs[ch], key, value)
            else:
                logger.warning(f"Unknown input attribute: {key}")

    def get_input(self, ch: int, key: str):
        """Retrieves a value from the input cache."""
        if ch in self.inputs:
            return getattr(self.inputs[ch], key, None)
        return None

    # ---------------- OUTPUTS ----------------

    def update_output(self, out_ch: int, key: str, value) -> None:
        if out_ch in self.outputs:
            if hasattr(self.outputs[out_ch], key):
                setattr(self.outputs[out_ch], key, value)
            else:
                logger.warning(f"Unknown output attribute: {key}")

    def get_output(self, out_ch: int, key: str):
        if out_ch in self.outputs:
            return getattr(self.outputs[out_ch], key, None)
        return None

    # ---------------- MONITOR ----------------

    def update_monitor(self, in_ch: int, out_ch: int, key: str, value):
        node = self.matrix.get((in_ch, out_ch))
        if node and hasattr(node, key):
            setattr(node, key, value)

    def get_monitor(self, in_ch: int , out_ch: int, key: str):
        node = self.matrix.get((in_ch, out_ch))
        if node and hasattr(node, key):
            return getattr(node, key)
        return None

    # ---------------- GLOBALS ----------------

    def update_global(self, key: str, value) -> None:
        if hasattr(self.globals, key):
            setattr(self.globals, key, value)
        else:
            logger.warning(f"Unknown global attribute: {key}")

    def get_global(self, key: str):
        return getattr(self.globals, key, None)

    # ---------------- EXPORT / IMPORT ----------------

#    def to_dict(self) -> dict:
#        """Exports the entire state as a standard dictionary (e.g., for JSON/GUI)."""
#        return {
#            "inputs": {ch: asdict(state) for ch, state in self.inputs.items()},
#            "outputs": {f"{ch[0]}+{ch[1]}": asdict(state) for ch, state in self.outputs.items()},
#            "monitor": {f"{k[0]}->{k[1][0]}+{k[1][1]}": v for k, v in self.matrix.items()},
#            "globals": asdict(self.globals)
#        }

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
        print(f"\n[ INPUTS (1-{self.capabilities.num_inputs}) ]")
        for ch, inp in self.inputs.items():
            # Formatting: Values right-aligned for a clean table
            gain_str = f"{inp.gain:>3}%" if inp.gain not in (None, -1) else "N/A "
            print(f"  IN {ch} | Gain: {gain_str} | 48V: {'ON' if inp.phantom else 'OFF':<3} | "
                  f"Mute: {'ON' if inp.mute else 'OFF':<3} | Link: {'ON' if inp.stereo_link else 'OFF':<3}")

        # --- Outputs ---
        print(f"\n[ OUTPUTS (1-{self.capabilities.num_outputs}) ]")
        for ch, out in self.outputs.items():
            vol_str = f"{out.volume:>3}%" if out.volume not in (None, -1) else "N/A "
            print(f"  OUT {ch}| Vol: {vol_str}  | Mute: {'ON' if out.mute else 'OFF':<3} | "
                  f"Link: {'ON' if out.stereo_link else 'OFF':<3}")

        # --- Monitor Matrix ---
        print(f"\n[ MONITOR MATRIX ({self.capabilities.num_monitor_inputs} Inputs -> {self.capabilities.num_outputs} Outputs) ]")
        # Kopfzeile
        print(" " * 10 + " ".join([f"OUT {i + 1}".ljust(6) for i in range(self.capabilities.num_outputs)]))
        print(" " * 10 + "-" * 25)

        # We loop over the 10 internal inputs (including PC and Loopback)
        for in_ch in range(1, self.capabilities.num_monitor_inputs+1):
            row_str = f" IN {in_ch:2} |"
            for out_ch in range(1, self.capabilities.num_outputs+1):
                # Hole den Wert aus dem Cache
                vol = self.get_monitor(in_ch, out_ch, "volume")

                # Defensive Formatierung
                if vol is None or vol == -1:
                    display = " N/A  "
                else:
                    # Annahme: val ist ein Prozentwert oder Rohwert
                    display = f"{vol:>4}% "

                row_str += display
            print(row_str)

        print("\n" + "=" * 55 + "\n")



#    def import_from_evo_xml(self, xml_string: str) -> bool:
#        """
#        Reads an official EVO XML save file.
#        Caution: Requires conversion of raw values (e.g., gain="3584" -> percent).
#        """
#        try:
#            root = ET.fromstring(xml_string)
#            device = root.find("preset/device")
#            if device is None: return False
#
#            # Read globals
#            settings = device.find("driver-settings")
#            if settings is not None:
#                sr = int(settings.get("sample-rate", -1))
#                self.update_global("sample_rate", sr)
#
#            # Read inputs (Caution: index 0 = Channel 1)
#            inputs_node = device.find("inputs")
#            if inputs_node is not None:
#                for inp in inputs_node.findall("input"):
#                    ch = int(inp.get("index", -1)) + 1
#
#                    # Here you would need to call your conversion functions!
#                    # raw_gain = int(inp.get("gain", 0))
#                    # percent_gain = deine_logik(raw_gain)
#
#                    self.update_input(ch, "phantom", inp.get("phantom") == "1")
#                    self.update_input(ch, "mute", inp.get("mute") == "1")
#                    self.update_input(ch, "stereo_link", inp.get("stereo_link") == "1")
#
#            logger.info("XML state successfully imported.")
#            return True
#        except Exception as e:
#            logger.error(f"Error during XML import: {e}")
#            return False