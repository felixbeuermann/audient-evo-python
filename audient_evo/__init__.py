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


#from .protocol import EvoProtocol, InBlock, OutBlock, MonBlock
from .transport import EvoUsbTransport, UsbNotBoundError
from .device import Evo8Device


__all__ = [
#"EvoProtocol",
#"InBlock",
#"OutBlock",
#"MonBlock",
"EvoUsbTransport",
"UsbNotBoundError",
"Evo8Device",
]