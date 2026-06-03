# audient-evo-python

An **experimental, unofficial Python API/wrapper** for the **Audient EVO series 8 (4,16 in the works)** USB audio interfaces on Linux.

This library allows direct USB communication with the EVO hardware, replicating the core functionality of the official EVO Mixer software using raw control transfers.

> ⚠️ **DISCLAIMER & Project Status**
> * This project is a **partially reverse-engineered** USB control protocol.
> * It is **not** affiliated with or endorsed by Audient.
> * The kernel driver is **detached** while the app is active.
> * Crashes or forced exits may leave the device unclaimed until replugged.
> * Information may be incomplete or inaccurate; use at your own risk.

## Features

* Direct USB communication with EVO firmware
* Input controls: Phantom power (48V), Gain, Mute, Mono
* Output controls: Volume, Mute
* Monitor mixer routing (per input → output)
* Sample‑rate switching (experimental)
* Event polling support
* Input Routing Loopback source persistently changeable

## Architecture

The library is built on three logical layers, completely independent of any UI frameworks:

* `Evo8Device`: High‑level device API. Converts semantic actions into protocol commands.
* `EvoProtocol`: Pure helper class containing addressing rules and calculators (no USB logic).
* `EvoUsbTransport`: Low‑level USB transport handling discovery, interface claiming, and control transfers.

## Requirements

* Python **3.10+**
* Linux
* `pyusb`

## Installation & Setup

1. Install the package:
   ```bash
   pip install audient-evo-linux
   ```
2. Ensure your user has permission to access the EVO USB device by adding yourself to the audio group:
    ```bash
    sudo usermod -aG audio $USER
    ```


3. You may also want to copy the udev rule from this projects `udev/` directory into `/etc/udev/rules.d/`:

    ```bash
    SUBSYSTEM=="usb", ATTR{idVendor}=="2708", ATTR{idProduct}=="0008", MODE="0666" # EVO16
    SUBSYSTEM=="usb", ATTR{idVendor}=="2708", ATTR{idProduct}=="0007", MODE="0666" # EVO8
    SUBSYSTEM=="usb", ATTR{idVendor}=="2708", ATTR{idProduct}=="0006", MODE="0666" # EVO4
    ```
---

## Usage Example

```python
from audient_evo import EvoUsbTransport, Evo8Device

transport = EvoUsbTransport()
transport.connect()
evo = Evo8Device(transport)
evo.set_gain(1, 40)  # gain range 0 - 100
evo.set_phantom(1, True)
```

> Channel numbers are **1‑based**.

---

## References

This project builds upon previous community efforts:

* [audient-evo-linux-tools](https://github.com/vijay-prema/audient-evo-linux-tools)
* [evoctl](https://github.com/soerenbnoergaard/evoctl)

Huge credit to the developers of those projects for protocol discovery.

---

## Source Code Documentation Overview

### Key Classes

#### `EvoUsbTransport`

Responsible for:

* USB device discovery
* Kernel driver detachment
* Interface claiming / releasing
* Control transfer I/O

Implements context‑manager support for **safe cleanup**.

#### `EvoProtocol`

Pure helper class that:

* Encapsulates EVO firmware addressing rules
* Calculates channel‑based and monitor‑matrix addresses

Contains **no USB logic**.

#### `Evo8Device`

High‑level API intended for UI and scripting:

* Converts semantic actions into protocol commands
* Handles error recovery
* Enforces "app vs OS" control state

---

## License

**BSD-3-Clause**

---

## Contributing

Any kind of contribution is always welcome.