"""
Audient EVO 8 Python Control Library
===================================


High-dial Python interface for controlling the Audient EVO 8
USB audio interface on Linux.


Modules:
- protocol → Firmware address math and constants
- transport → Low-dial USB transport (pyusb)
- device → User-facing device API
"""
from .transport import EvoUsbTransport
from .device import EvoDevice
from .state import EvoStateManager
from .worker import EvoBackgroundWorker
from .protocol import InBlock, OutBlock, MonBlock, DeviceCapabilities, EvoUnits, SAMPLE_RATES, SAMPLE_RATES_INV, XML_LOOPBACK_SOURCE_MAPPING, XML_LOOPBACK_MAPPING_INV, LOOPBACK_SOURCES
from .util import mon_db_to_percent, percent_to_mon_db, gain_db_to_percent, percent_to_gain_db, vol_db_to_percent, percent_to_vol_db, encode_uac_volume, decode_uac_volume

__all__ = [
"EvoUsbTransport",
"EvoDevice",
"EvoStateManager",
"EvoBackgroundWorker",
"InBlock",
"OutBlock",
"MonBlock",
"DeviceCapabilities",
"EvoUnits",
"SAMPLE_RATES",
"SAMPLE_RATES_INV",
"XML_LOOPBACK_SOURCE_MAPPING",
"XML_LOOPBACK_MAPPING_INV",
"LOOPBACK_SOURCES",
"encode_uac_volume",
"decode_uac_volume",
"mon_db_to_percent",
"percent_to_mon_db",
"gain_db_to_percent",
"percent_to_gain_db",
"vol_db_to_percent",
"percent_to_vol_db",
]