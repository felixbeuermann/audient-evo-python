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

import xml.etree.ElementTree as ET

from audient_evo.protocol import DeviceCapabilities, XML_LOOPBACK_SOURCE_MAPPING

logger = logging.getLogger(__name__)

@dataclass
class InputState:
    gain: int = -1
    phantom: bool = False
    mute: bool = False
    stereo_link: bool = False
    name: str = ""

@dataclass
class OutputState:
    volume: float = -1.00
    mute: bool = False
    stereo_link: bool = True
    name: str = ""

@dataclass
class MonitorInputState:
    """Channel-Strip im Mixer (10)."""
    name: str = ""
    mode: int = 0  # 0 = Mono, 1 = Stereo Left, 2 = Stereo Right
    cut: bool = False

@dataclass
class MatrixNode:
    """Represents the individual monitor matrix nodes."""
    volume: float = -1.00
    pan: float = 0.5
    mute: bool = False  # TODO: GET RID OF THIS HERE
    solo: bool = False

@dataclass
class GlobalState:
    loopback_source: str = None
    sample_rate: int = -1
    artist_mix: bool = False

class EvoStateManager:
    """Manages the entire known state of the EVO 8."""

    def __init__(self, capabilities: DeviceCapabilities):
        self.capabilities = capabilities
        self.preset_loaded = False

        # --- Inputs (1-4) ---
        self.inputs = {ch: InputState() for ch in range(1, self.capabilities.num_inputs+1)}

        # --- Outputs (1-4) ---
        self.outputs = {ch: OutputState() for ch in range(1, self.capabilities.num_outputs+1)}

        # --- Hardware-Monitor/DSP-Channels (10 Inputs -> 4 Outputs) ---
        self.matrix = {}
        for in_ch in range(1, self.capabilities.num_monitor_inputs+1):
            for out_ch in range(1, self.capabilities.num_outputs+1):
                self.matrix[(in_ch, out_ch)] = MatrixNode()

                # --- Monitor Inputs (10 Channel-Strips) ---
                self.monitor_inputs = {}
                for ch in range(1, self.capabilities.num_monitor_inputs + 1):
                    # Set default values
                    if ch <= self.capabilities.num_inputs:
                        mode = 0  # Hardware-Mics are mono by default
                        name = f"MIC {ch}"
                    elif ch in (5, 6):
                        mode = 1 if ch == 5 else 2  # Digital Channels are stereo by default
                        name = "PC 1+2" if ch == 5 else "PC 2"
                    elif ch in (7, 8):
                        mode = 1 if ch == 7 else 2
                        name = "PC 3+4" if ch == 7 else "PC 4"
                    else:
                        mode = 1 if ch == 9 else 2
                        name = "LOOP-BACK 1+2" if ch == 9 else "LOOP-BACK 2"

                    self.monitor_inputs[ch] = MonitorInputState(name=name, mode=mode)

                # --- Hardware-Monitor Matrix (10x4 nodes) ---
                self.matrix = {}
                for in_ch in range(1, self.capabilities.num_monitor_inputs + 1):
                    mode = self.monitor_inputs[in_ch].mode

                    # Pan-Default
                    if mode == 1:
                        default_pan = 0.0  # Left
                    elif mode == 2:
                        default_pan = 1.0  # Right
                    else:
                        default_pan = 0.5  # Center
                    for out_ch in range(1, self.capabilities.num_outputs + 1):
                        self.matrix[(in_ch, out_ch)] = MatrixNode(pan=default_pan)

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
        artist_mix = self.get_global('artist_mix')
        print(f"  Sample Rate : {sr if sr != -1 else 'Unknown'} Hz")
        print(f"  Loopback_source    : {lb_source if lb_source else 'Not set'}")
        print(f"  Artist_mix         : {artist_mix if artist_mix is not None else 'Not set'}")

        # --- Inputs ---
        print(f"\n[ INPUTS (1-{self.capabilities.num_inputs}) ]")
        for ch, inp in self.inputs.items():
            # Formatting: Values right-aligned for a clean table
            gain_str = f"{inp.gain:>3} dB" if inp.gain not in (None, -1) else "N/A "
            print(f"  IN {ch} | Gain: {gain_str} | 48V: {'ON' if inp.phantom else 'OFF':<3} | "
                  f"Mute: {'ON' if inp.mute else 'OFF':<3} | Link: {'ON' if inp.stereo_link else 'OFF':<3}")

        # --- Outputs ---
        print(f"\n[ OUTPUTS (1-{self.capabilities.num_outputs}) ]")
        for ch, out in self.outputs.items():
            vol_str = f"{out.volume:.2f} dB" if out.volume not in (None, -1) else "N/A "
            print(f"  OUT {ch}| Vol: {vol_str}  | Mute: {'ON' if out.mute else 'OFF':<3} | "
                  f"Link: {'ON' if out.stereo_link else 'OFF':<3}")

        # --- Monitor Matrix ---
        print(f"\n[ MONITOR MATRIX ({self.capabilities.num_monitor_inputs} Inputs -> {self.capabilities.num_outputs} Outputs) ]")
        # Header
        print(" " * 10 + " ".join([f"OUT {i + 1}".ljust(6) for i in range(self.capabilities.num_outputs)]))
        print(" " * 10 + "-" * 25)

        # loop over the 10 internal inputs (including PC and Loopback)
        for in_ch in range(1, self.capabilities.num_monitor_inputs+1):
            row_str = f" IN {in_ch:2} |"
            for out_ch in range(1, self.capabilities.num_outputs+1):
                # Get volume out of cache
                vol = self.get_monitor(in_ch, out_ch, "volume")

                # Defensive formating
                if vol is None or vol == -1:
                    display = " N/A  "
                else:
                    display = f"{vol:.2f} dB "

                row_str += display
            print(row_str)

        print("\n" + "=" * 55 + "\n")

    def import_from_evo_xml(self, xml_string: str) -> bool:
        """
        Loads an official EVO XML preset file, and populates the state cache.
        """

        try:
            root = ET.fromstring(xml_string)

            device_node = root.find(".//device")
            if device_node is None:
                device_node = root

            # ==========================================
            # 1. GLOBAL SETTINGS
            # ==========================================
            mixer_node = device_node.find("mixer")
            if mixer_node is not None:
                artist_mix = int(mixer_node.get("artistMixEnabled", -1))
                if artist_mix != -1:
                    self.update_global("artist_mix", bool(artist_mix))

            settings = device_node.find("driver-settings")
            if settings is not None:
                sr = int(settings.get("sample-rate", -1))
                if sr != -1:
                    self.update_global("sample_rate", sr)

            # ==========================================
            # 2. ROUTING (Loopback)
            # ==========================================
            loopback_node = device_node.find(".//routing/loop-back")
            if loopback_node is not None:
                lb_type = loopback_node.get("type")
                lb_index = loopback_node.get("index")

                # Map XML type/index to string
                source_str = XML_LOOPBACK_SOURCE_MAPPING.get((lb_type, lb_index))
                if source_str:
                    # Note: If there is a Loopback target, update it here as well
                    self.update_global("loopback_source", source_str)
                    logger.debug(f"Loopback found: {source_str}")

            # ==========================================
            # 3. PHYSICAL INPUTS (Preamps)
            # ==========================================
            for inp in device_node.findall(".//inputs/input"):
                ch = int(inp.get("index", -1)) + 1  # 0-3 -> 1-4

                if 1 <= ch <= 4:
                    self.update_input(ch, "phantom", inp.get("phantom") == "1")
                    self.update_input(ch, "mute", inp.get("mute") == "1")
                    self.update_input(ch, "stereo_link", inp.get("link") == "1")  # Caution: named 'link' in the XML

                    # Gain is a Raw-Value (-2048 to 12800)
                    gain_db = int(inp.get("gain", -2048))
                    self.update_input(ch, "gain", gain_db)

            # ==========================================
            # 4. MIXER MATRIX (Artist Mix / Main Mix)
            # ==========================================
            for mixer_in in device_node.findall(".//mixer/input"):
                # in_ch: 0-1 = Mic 1-2, 2 = Mic 3/4, 4-7 = PC 1-4, 8-9 = Loopback
                # simply map everything strictly +1 (1 to 10)
                in_ch = int(mixer_in.get("channel", -1)) + 1
                software_cut = (mixer_in.get("cut") == "1")

                for mix in mixer_in.findall("mix"):
                    mix_index = int(mix.get("index", -1))

                    # uses dB in Range -128.00 - 8.00 (.00 is important)
                    vol_db = float(mix.get("volume", -128.00))
                    pan_val = float(mix.get("pan", 0.50))

                    # mix_index 0 = Output 1+2 (Main), mix_index 1 = Output 3+4 (Artist)
                    out_channels = [1, 2] if mix_index == 0 else [3, 4] if mix_index == 1 else []

                    for out_ch in out_channels:

                        self.update_monitor(in_ch, out_ch, "volume", vol_db)
                        self.update_monitor(in_ch, out_ch, "pan", pan_val)
                        self.update_monitor(in_ch, out_ch, "mute", software_cut)

            # ==========================================
            # 5. MIXER OUTPUTS
            # ==========================================
            for out in device_node.findall(".//mixer/output"):
                ch = int(out.get("channel", -1)) + 1  # 0-3 -> 1-4
                if 1 <= ch <= 4:
                    vol_db = float(out.get("volume", -128.00))
                    self.update_output(ch, "volume", vol_db)

            logger.info("XML Preset parsed successfully and stored in cache.")
            self.preset_loaded = True
            return True

        except ET.ParseError as e:
            logger.error(f"XML could not be parsed(Syntax error): {e}")
            return False
        except Exception as e:
            logger.exception(f"unexpected error during XML-Parsing: {e}")
            return False

    def export_to_evo_xml(self, preset_name: str = "Evo_Linux_Export") -> str:
        """
        Exports the cache directly to a save file compatible with the official windows EVO Mixer
        """
        import xml.etree.ElementTree as ET
        from audient_evo.protocol import XML_LOOPBACK_MAPPING_INV

        product_name = self.capabilities.name.lower()

        # 1. build root tags
        presets = ET.Element("presets")

        preset_attrs = {
            "company": "audient ltd",
            "product": product_name,
            "schema-version": "0.1.0",
            "software-version": "4.4.0",
            "name": preset_name
        }
        preset = ET.SubElement(presets, "preset", preset_attrs)
        device = ET.SubElement(preset, "device")

        # 2. MIXER
        artist_mix_val = "1" if self.get_global("artist_mix") else "0"
        mixer_node = ET.SubElement(device, "mixer", {"artistMixEnabled": artist_mix_val})

        # --- MIXER INPUTS ---
        for in_ch in range(1, self.capabilities.num_monitor_inputs + 1):

            # --- Link-Status & Cut ---
            if in_ch <= self.capabilities.num_inputs:
                is_linked = bool(self.get_input(in_ch, "stereo_link"))
                cut_val = "1" if self.get_input(in_ch, "mute") else "0"
                cache_name = self.get_input(in_ch, "name")
            else:
                mon_in = getattr(self, "monitor_inputs", {}).get(in_ch)
                if mon_in and hasattr(mon_in, "mode"):
                    is_linked = str(mon_in.mode) in ("1", "2")
                else:
                    is_linked = True

                cut_val = "1" if mon_in and getattr(mon_in, "cut", False) else "0"
                cache_name = mon_in.name if mon_in and hasattr(mon_in, "name") else ""

            # --- Calculate mode ---
            if is_linked:
                mode_str = "1" if (in_ch % 2 != 0) else "2"
            else:
                mode_str = "0"

            # --- Calculate dynamic default names based on the mode ---

            num_mics = self.capabilities.num_inputs
            num_pcs = self.capabilities.num_monitor_inputs - num_mics - 2

            if in_ch <= num_mics:
                base, num = "MIC", in_ch
            elif in_ch <= num_mics + num_pcs:
                base, num = "PC", in_ch - num_mics
            else:
                base, num = "LOOP-BACK", in_ch - (num_mics + num_pcs)

            if mode_str == "1":
                def_name = f"{base} {num}+{num + 1}"
            elif mode_str == "2":
                def_name = f"{base} {num}"
            else:
                def_name = f"{base} {num}"

            if cache_name and not cache_name.startswith(("MIC", "PC", "LOOP", "OUTPUT")):
                node_name = cache_name
            else:
                node_name = def_name

            inp_attrs = {
                "channel": str(in_ch - 1),
                "mode": mode_str,
                "name": node_name,
                "cut": cut_val
            }
            m_in_node = ET.SubElement(mixer_node, "input", inp_attrs)

            # --- MIX SUBNODES ---
            for mix_index, base_out in enumerate((1, self.capabilities.num_outputs + 1, 2)):
                vol_db = self.get_monitor(in_ch, base_out, "volume")
                pan = self.get_monitor(in_ch, base_out, "pan")

                # Exact dB formatting, no rounding
                if vol_db in (None, -1):
                    vol_str = "-128.00"
                else:
                    vol_str = f"{float(vol_db):.2f}"

                pan_str = f"{pan:.2f}" if pan is not None else "0.50"

                mix_attrs = {
                    "index": str(mix_index),
                    "volume": vol_str,
                    "pan": pan_str
                }
                ET.SubElement(m_in_node, "mix", mix_attrs)

        # --- MIXER OUTPUTS ---
        for out_ch in range(1, self.capabilities.num_outputs + 1):
            vol_db = self.get_output(out_ch, "volume")
            is_linked = bool(self.get_output(out_ch, "stereo_link"))
            cache_name = self.get_output(out_ch, "name")

            if is_linked:
                if out_ch in (1, 2):
                    def_out_name = "OUTPUTS 1+2"
                elif out_ch in (3, 4):
                    def_out_name = "OUTPUTS 3+4"
                elif out_ch in (5, 6):
                    def_out_name = "OUTPUTS 5+6"
                else:
                    def_out_name = f"OUTPUTS {out_ch}"
            else:
                def_out_name = f"OUTPUTS {out_ch}"

            if cache_name and not cache_name.startswith("OUTPUT"):
                out_name = cache_name
            else:
                out_name = def_out_name

            # Exact dB formatting
            if vol_db in (None, -1):
                vol_str = "-128.00"
            else:
                vol_str = f"{float(vol_db):.2f}"

            out_attrs = {
                "channel": str(out_ch - 1),
                "volume": vol_str,
                "name": out_name
            }
            ET.SubElement(mixer_node, "output", out_attrs)

        # 3. SYSTEM
        ET.SubElement(device, "system")

        # 4. HARDWARE INPUTS
        inputs_node = ET.SubElement(device, "inputs")
        for ch in range(1, self.capabilities.num_inputs + 1):
            phantom = "1" if self.get_input(ch, "phantom") else "0"
            mute = "1" if self.get_input(ch, "mute") else "0"
            link = "1" if self.get_input(ch, "stereo_link") else "0"

            gain_raw = self.get_input(ch, "gain")
            # Exact RAW integer formatting
            if gain_raw in (None, -1):
                gain_str = "-2048"
            else:
                gain_str = str(int(gain_raw))

            hw_inp_attrs = {
                "index": str(ch - 1),
                "phantom": phantom,
                "gain": gain_str,
                "mute": mute,
                "link": link
            }
            ET.SubElement(inputs_node, "input", hw_inp_attrs)

        # 5. LOOPBACK ROUTING
        routing_node = ET.SubElement(device, "routing")
        lb_source = self.get_global("loopback_source")
        lb_type, lb_index = XML_LOOPBACK_MAPPING_INV.get(lb_source, ("0", "0"))

        lb_attrs = {
            "type": str(lb_type),
            "index": str(lb_index)
        }
        ET.SubElement(routing_node, "loop-back", lb_attrs)

        # 6. DRIVER SETTINGS
        sr = self.get_global("sample_rate")
        sr_val = str(sr) if sr not in (-1, None) else "48000"
        ET.SubElement(device, "driver-settings", {"sample-rate": sr_val})

        if hasattr(ET, "indent"):
            ET.indent(presets, space="  ", level=0)

        xml_str = ET.tostring(presets, encoding="utf-8").decode("utf-8")

        if xml_str.startswith("<?xml"):
            xml_str = xml_str.split("?>\n", 1)[-1]

        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n\n' + xml_str.strip() + '\n'
        xml_str = xml_str.replace(" />", "/>")
        return xml_str