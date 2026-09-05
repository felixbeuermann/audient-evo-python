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
    """
    Dataclass holding the current hardware state for a single physical input channel.

    Attributes:
        gain (int): The current input gain value in raw steps or dB representation.
        phantom (bool): Active status of +48V phantom power.
        mute (bool): Mute status of the input channel.
        stereo_link (bool): True if linked with its adjacent channel pair.
        name (str): Display name for the channel.
    """
    gain: int = -1
    phantom: bool = False
    mute: bool = False
    stereo_link: bool = False
    name: str = ""

@dataclass
class OutputState:
    """
    Dataclass holding the state for a physical output channel pair.

    Attributes:
        volume (float): Master volume output level.
        mute (bool): Mute status of the output channel.
        stereo_link (bool): True if stereo linking is active.
        name (str): Display name for the output.
    """
    volume: float = -1.00
    mute: bool = False
    stereo_link: bool = True
    name: str = ""

@dataclass
class MonitorInputState:
    """Represents Output Agnostic Monitor-Strips""" #TODO
    name: str = ""
    mode: int = 0  # 0 = Mono, 1 = Stereo Left, 2 = Stereo Right
    cut: bool = False

@dataclass
class MatrixNode:
    """Represents the individual monitor matrix nodes."""   #TODO
    volume: float = -1.00
    pan: float = 0.5
    solo: bool = False

@dataclass
class GlobalState:
    """
    Dataclass storing global audio interface parameters.

    Attributes:
        loopback_source (str): Currently selected audio source for loopback recording.
        sample_rate (int): Active device sample rate in Hz.
        artist_mix (bool): Active status of artist mix routing mode.
    """
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
        """
        Updates an attribute for an input channel in the cache.

        Args:
            ch (int): The input channel.
            key (str): Attribute name to update.
            value: New value to assign.
        """
        if ch in self.inputs and hasattr(self.inputs[ch], key):
            old_val = getattr(self.inputs[ch], key)
            if old_val != value: # Only update if the value is new
                setattr(self.inputs[ch], key, value)
        else:
            logger.warning(f"Unknown input attribute: {key}")

    def get_input(self, ch: int, key: str):
        """
        Retrieves an attribute value for an input channel from the cache.

        Args:
            ch (int): The input channel.
            key (str): Attribute name to read.

        Returns:
            Any: The attribute value if found, or None if channel or key does not exist.
        """
        if ch in self.inputs and hasattr(self.inputs[ch], key):
            return getattr(self.inputs[ch], key, None)
        return None

    # ---------------- OUTPUTS ----------------

    def update_output(self, out_ch: int, key: str, value) -> None:
        """
        Updates an attribute for an output channel in the cache.

        Args:
            out_ch (int): The output channel.
            key (str): Attribute name to update.
            value: New value to assign.
        """
        if out_ch in self.outputs and hasattr(self.outputs[out_ch], key):
            old_val = getattr(self.outputs[out_ch], key)
            if old_val != value:
                setattr(self.outputs[out_ch], key, value)
        else:
            logger.warning(f"Unknown output attribute: {key}")

    def get_output(self, out_ch: int, key: str):
        """
        Retrieves an attribute value for an output channel from the cache.

        Args:
            out_ch (int): The output channel.
            key (str): Attribute name to read.

        Returns:
            Any: The attribute value if found, or None.
        """
        if out_ch in self.outputs and hasattr(self.outputs[out_ch], key):
            return getattr(self.outputs[out_ch], key, None)
        return None

    # ---------------- MONITOR ----------------

    def update_monitor(self, in_ch: int, out_ch: int, key: str, value):
        """
        Updates a matrix node attribute in the monitor matrix cache.

        Args:
            in_ch (int): Input channel of the matrix node.
            out_ch (int): Target output channel of the matrix node.
            key (str): Attribute name to update (e.g., 'volume', 'pan').
            value: New value to assign.
        """
        node = self.matrix.get((in_ch, out_ch))
        if node and hasattr(node, key):
            old_val = getattr(node, key)
            if old_val != value:
                setattr(node, key, value)

    def get_monitor(self, in_ch: int , out_ch: int, key: str):
        """
        Retrieves a matrix node attribute value from the monitor matrix cache.

        Args:
            in_ch (int): Input channel of the matrix node.
            out_ch (int): Output channel of the matrix node.
            key (str): Attribute name to read.

        Returns:
            Any: Attribute value if present, otherwise None.
        """
        node = self.matrix.get((in_ch, out_ch))
        if node and hasattr(node, key):
            return getattr(node, key)
        return None

    def update_monitor_in(self, in_ch: int, key: str, value):
        """
        Updates an attribute of a monitor input channel-strip in the cache.

        Args:
            in_ch (int): Monitor input channel.
            key (str): Attribute name to update (e.g., 'mode', 'cut').
            value: New value to assign.
        """
        if in_ch in self.monitor_inputs and hasattr(self.monitor_inputs[in_ch], key):
            old_val = getattr(self.monitor_inputs[in_ch], key)
            if old_val != value:
                setattr(self.monitor_inputs[in_ch], key, value)
        else:
            logger.warning(f"Unknown input attribute: {key}")

    def get_monitor_in(self, in_ch: int, key: str):
        """
        Retrieves an attribute value from a monitor input channel-strip cache.

        Args:
            in_ch (int): Monitor input channel.
            key (str): Attribute name to read.

        Returns:
            Any: Attribute value if present, otherwise None.
        """
        if in_ch in self.monitor_inputs and hasattr(self.monitor_inputs[in_ch], key):
            return getattr(self.monitor_inputs[in_ch], key, None)
        return None

    # ---------------- GLOBALS ----------------

    def update_global(self, key: str, value) -> None:
        """
        Updates a global configuration attribute in the local cache.

        Args:
            key (str): Name of the global attribute to update.
            value: New value to assign.
        """
        if hasattr(self.globals, key):
            old_value = getattr(self.globals, key)
            if old_value != value:
                setattr(self.globals, key, value)
        else:
            logger.warning(f"Unknown global attribute: {key}")

    def get_global(self, key: str):
        """
        Retrieves a global configuration attribute from the local cache.

        Args:
            key (str): Name of the global attribute to read.

        Returns:
            Any: Attribute value if present, otherwise None.
        """
        return getattr(self.globals, key, None)

    # ---------------- EXPORT / IMPORT ----------------

    def get_full_state_dict(self) -> dict:
        """
        Flattens the hierarchical hardware state cache into a one-dimensional dictionary.

        Returns:
            dict: A flat dictionary containing all current inputs, outputs, monitor matrices,
            and global configurations.
        """
        full_state = {}

        # 1. Inputs (Preamps)
        for ch, inp in self.inputs.items():
            full_state[f"input_{ch}_gain"] = inp.gain
            full_state[f"input_{ch}_phantom"] = inp.phantom
            full_state[f"input_{ch}_mute"] = inp.mute
            full_state[f"input_{ch}_stereo_link"] = inp.stereo_link
            full_state[f"input_{ch}_name"] = inp.name

        # 2. Outputs (Master)
        for ch, out in self.outputs.items():
            full_state[f"output_{ch}_volume"] = out.volume
            full_state[f"output_{ch}_mute"] = out.mute
            full_state[f"output_{ch}_stereo_link"] = out.stereo_link
            full_state[f"output_{ch}_name"] = out.name

        # 3. Monitor Inputs (Output Agnostic Monitor-Strips)
        for ch, mon_in in self.monitor_inputs.items():
            full_state[f"monin_{ch}_mode"] = mon_in.mode
            full_state[f"monin_{ch}_cut"] = mon_in.cut
            full_state[f"monin_{ch}_name"] = mon_in.name

        # 4. Monitor Matrix (Individual Nodes)
        for (in_ch, out_ch), node in self.matrix.items():
            full_state[f"matrix_{in_ch}_{out_ch}_volume"] = node.volume
            full_state[f"matrix_{in_ch}_{out_ch}_pan"] = node.pan
            full_state[f"matrix_{in_ch}_{out_ch}_solo"] = node.solo

        # 5. Globals
        full_state["global_loopback"] = self.get_global('loopback_source')
        full_state["global_samplerate"] = self.get_global('sample_rate')
        full_state["global_artistmix"] = self.get_global('artist_mix')

        return full_state

    def print_cache(self) -> None:
        """
        Prints the entire current cache state (StateManager)
        in a clearly formatted way to the terminal.
        """
        # Define column width for the matrix (space for V, P, M, S)
        col_width = 34
        # Calculate total width: 10 character offset + width of columns
        separator_len = 10 + (col_width * self.capabilities.num_outputs)

        print("\n" + "=" * separator_len)
        print("🎛️ EVO 8 CURRENT STATE CACHE 🎛️".center(separator_len))
        print("=" * separator_len)

        # --- Globals ---
        print("\n[ GLOBALS ]")
        sr = self.get_global('sample_rate')
        lb_source = self.get_global('loopback_source')
        artist_mix = self.get_global('artist_mix')

        print(f"  Sample Rate    : {sr if sr not in (None, -1) else 'Unknown'} Hz")
        print(f"  Loopback_source: {lb_source if lb_source else 'Not set'}")

        # Read Artist Mix safely (may be None)
        if artist_mix is None:
            am_str = 'Not set'
        else:
            am_str = 'ON' if artist_mix else 'OFF'
        print(f"  Artist_mix     : {am_str}")

        # --- Inputs ---
        print(f"\n[ INPUTS (1-{self.capabilities.num_inputs}) ]")
        for ch, inp in self.inputs.items():
            # Formatting: Raw Gain (e.g., -2048) needs slightly more space
            gain_str = f"{inp.gain:>5}" if inp.gain not in (None, -1) else "N/A  "
            print(f"  IN {ch} | Gain: {gain_str} | 48V: {'ON' if inp.phantom else 'OFF':<3} | "
                  f"Mute: {'ON' if inp.mute else 'OFF':<3} | Link: {'ON' if inp.stereo_link else 'OFF':<3}")

        # --- Outputs ---
        print(f"\n[ OUTPUTS (1-{self.capabilities.num_outputs}) ]")
        for ch, out in self.outputs.items():
            vol_str = f"{out.volume:.2f} dB" if out.volume not in (None, -1) else "N/A     "
            print(f"  OUT {ch}| Vol: {vol_str:>9} | Mute: {'ON' if out.mute else 'OFF':<3} | "
                  f"Link: {'ON' if out.stereo_link else 'OFF':<3}")

        # --- Monitor Matrix ---
        print(
            f"\n[ MONITOR MATRIX ({self.capabilities.num_monitor_inputs} Inputs -> {self.capabilities.num_outputs} Outputs) ]")

        # Build header dynamically
        header_row = " " * 10 + "".join(
            [f"OUT {i + 1}".center(col_width) for i in range(self.capabilities.num_outputs)])
        print(header_row)
        print(" " * 10 + "-" * (col_width * self.capabilities.num_outputs))

        # loop over the 10 internal inputs (including PC and Loopback)
        for in_ch in range(1, self.capabilities.num_monitor_inputs + 1):
            row_str = f"  IN {in_ch:2} |"
            cut = self.get_monitor_in(in_ch, "cut")
            for out_ch in range(1, self.capabilities.num_outputs + 1):
                # Retrieve values defensively from the cache
                vol = self.get_monitor(in_ch, out_ch, "volume")
                pan = self.get_monitor(in_ch, out_ch, "pan")
                solo = self.get_monitor(in_ch, out_ch, "solo")

                # Defensive formatting for each parameter
                vol_str = f"{vol:.2f}" if vol not in (None, -1) else "N/A"
                pan_str = f"{pan:.2f}" if pan is not None else "N/A"
                cut_str = "ON" if cut else "OFF"
                solo_str = "ON" if solo else "OFF"

                # Build cell (V = Volume, P = Pan, M = Mute, S = Solo)
                cell = f"V:{vol_str:>7} P:{pan_str:>4} M:{cut_str:<3} S:{solo_str:<3}"

                # Center cell and add separator
                row_str += cell.center(col_width - 1) + "|"
            print(row_str)

        print("\n" + "=" * separator_len + "\n")

    def import_from_xml(self, xml_string: str) -> bool:
        """
        Parses an official EVO XML preset file and populates the internal state cache.

        Args:
            xml_string (str): The XML preset string.

        Returns:
            bool: True if the XML was parsed and loaded successfully, False otherwise.
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
                self.update_global("artist_mix", mixer_node.get("artistMixEnabled", -1) == "1")

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
                in_ch = int(mixer_in.get("channel", -1)) + 1
                mon_in_cut = (mixer_in.get("cut") == "1")
                mon_in_name = (mixer_in.get("name", ""))
                mon_in_mode = (mixer_in.get("mode", -1))

                self.update_monitor_in(in_ch, "cut", mon_in_cut)
                self.update_monitor_in(in_ch, "name", mon_in_name)
                self.update_monitor_in(in_ch, "mode", mon_in_mode)

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

    def export_to_xml(self, preset_name: str = "EvoLinuxExport") -> str:
        """
        Exports the current state cache to an XML file compatible with the official EVO Mixer.

        Args:
            preset_name (str): The name to assign to the exported preset.

        Returns:
            str: The formatted XML string representing the current state.
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
            for mix_index, base_out in enumerate((1, 3)):
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
        sample_rate = self.get_global("sample_rate")
        sr_str = str(sample_rate) if sample_rate not in (-1, None) else "Error -1"
        ET.SubElement(device, "driver-settings", {"sample-rate": sr_str})

        if hasattr(ET, "indent"):
            ET.indent(presets, space="  ", level=0)

        xml_str = ET.tostring(presets, encoding="utf-8").decode("utf-8")

        if xml_str.startswith("<?xml"):
            xml_str = xml_str.split("?>\n", 1)[-1]

        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n\n' + xml_str.strip() + '\n'
        xml_str = xml_str.replace(" />", "/>")
        return xml_str