## Audient EVO 8 USB Protocol (Reverse-Engineered)

> ⚠️ **DISCLAIMER**
>
> This document describes a **partially reverse-engineered** USB control
> protocol. Information may be incomplete or inaccurate. Use at your own risk.

---

## Overview

The Audient EVO 8 exposes internal mixer and device controls via **USB control
transfers** (endpoint 0). The official EVO Mixer software uses the same mechanism
on Windows and macOS.

This project communicates with the firmware using:

* **bmRequestType**: `0xA1` (IN/GET), `0x21` (OUT/SET)
* **bRequest**: `0x01` (CUR), `0x02` (RANGE), `0x03` (MEM)
* **wValue**: Control selector / offset
* **wIndex**: Control block / address space (Extension Units)

---

## Address Spaces (wIndex)

| Name        | Value  | Purpose                 |
| ----------- | ------ | ----------------------- |
| IDX_INPUT   | 0x3A00 | Input channel controls  |
| IDX_OUTPUT  | 0x3B00 | Output channel controls |
| IDX_MONITOR | 0x3C00 | Monitor mixer matrix    |
| IDX_BUFFER  | 0x3E00 | Event / state buffer    |

These values are defined in `EvoProtocol`.

---

## Channel Addressing

Channels are **1-based** (firmware convention).

### Input / Output Controls

```
wValue = BASE_OFFSET + (channel - 1)
```

Example:

* Input gain, channel 1 → `0x0100`
* Input gain, channel 2 → `0x0101`

---

## Input Blocks (InBlock)

| Block   | Offset | Description       |
| ------- | ------ | ----------------- |
| PHANTOM | 0x0000 | 48V phantom power |
| GAIN    | 0x0100 | Mic preamp gain   |
| MUTE    | 0x0200 | Input mute        |
| MONO    | 0x0300 | Mono downmix      |

Boolean controls use a single byte:

* `0x00` → off
* `0x01` → on

---

## Output Blocks (OutBlock)

| Block  | Offset | Description   |
| ------ | ------ | ------------- |
| VOLUME | 0x0000 | Output volume |
| MUTE   | 0x0100 | Output mute   |

Volume values are raw firmware-scaled bytes.

---

## Monitor Mixer Matrix

The EVO 8 implements a **matrix mixer**:

```
Input Channel → Output Channel
```

Address calculation:

```
wValue = BASE + (input - 1) * 4 + (output - 1)
```

* Matrix width is fixed at 4 outputs
* Each cell controls gain from one input to one output

---

## Sample Rate Control (Experimental)

Sample rate is controlled via undocumented control registers.

Known values:

| Sample Rate | Bytes (LE)  |
| ----------- | ----------- |
| 44100 Hz    | 44 AC 00 00 |
| 48000 Hz    | 80 BB 00 00 |
| 88200 Hz    | 88 58 01 00 |
| 96000 Hz    | 00 77 01 00 |

⚠️ Behavior may depend on ALSA state.

---

## Event Buffer

Polling:

```
ctrl_get(wValue=0x0600, wIndex=IDX_BUFFER)
```

Returns a firmware-managed state blob.

This is used for:

* Detecting external changes
* Synchronizing UI state

---

## Known Unknowns

* SOLO implementation
* Loopback routing semantics
* Firmware state persistence
* Exact volume curve mapping

TODO: Showcase the captured requests:

request: 0xa1, 0x01, 0x0600, 0x3e00, 0x0004
response: ff 01 00 ff | 01 00 00 3a
                        00 01 00 3a
            channel, selector, interface, Unit
* request: 0x21, 0x01, 0x0600, 0x3a00, 0x4d4943203100 (Text: MIC1)
* request: 0x21, 0x01, 0x0601, 0x3a00, 0x4d4943203200 (Text: MIC2)
* request: 0x21, 0x01, 0x0602, 0x3a00, 0x4d4943203300 (Text: MIC3)
* request: 0x21, 0x01, 0x0603, 0x3a00, 0x4d4943203400 (Text: MIC4)
* request: 0x21, 0x01, 0x0605, 0x3a00, 0x5043203200 (Text: PC2)
* request: 0x21, 0x01, 0x0607, 0x3a00, 0x5043203400 (Text: PC4)
* request: 0x21, 0x01, 0x0609, 0x3a00, 0x4c4f4f502d4241434b203200 (Text: LOOP -BACK2)


* request: 0x21, 0x01, 0x0604, 0x3300, 0a (Loopback routing to PC1)
* request: 0x21, 0x01, 0x0605, 0x3300, 0b (Loopback routing to PC2)


* MEM request: 0xa1, 0x03, 0x0000003c1400
* MEM responses:
*                00 00 01 00  00 00 00 00  b0 01 4e 01  00 00 00 00  00 00 00 00
                 01 00 01 00  00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00 
                 00 00 01 00  01 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
                 00 00 00 00  02 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
                 00 00 00 00  07 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
                 00 00 00 00  09 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
                 00 00 00 00  0f 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
                 01 00 00 00  12 0c 0c 00  00 00 00 00  00 00 00 00  00 00 00 00
                 0a 00 00 00  ce 17 21 00  00 00 00 00  00 00 00 00  00 00 00 00
                 07 00 00 00  8c 00 03 00  00 00 00 00  00 00 00 00  00 00 00 00
                 07 00 00 00  2a 00 02 00  00 00 00 00  00 00 00 00  00 00 00 00
                 02 00 00 00  1c 00 02 00  00 00 00 00  00 00 00 00  00 00 00 00
                 04 00 00 00  1c 00 02 00  00 00 00 00  00 00 00 00  00 00 00 00
                 00 00 46 00  16 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
                 00 00 6d 00  19 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
                 04 00 00 00  00 00 01 00  00 00 00 00  00 00 00 00  00 00 00 00

                 

* MEM request: 0xa1, 0x03, 0x0000003c0800
* MEM responses: 
*                00 00 00 00  00 00 00 00
                 01 00 01 00  00 00 00 00
                 01 00 01 00  00 00 00 00
                 03 00 03 00  00 00 00 00
                 05 00 05 00  00 00 00 00
                 07 00 07 00  00 00 00 00
                 fc 05 fc 05  00 00 00 00
                 fd 0b fd 0b  00 00 00 00
                 49 00 49 00  00 00 00 00
                 17 00 17 00  00 00 00 00
                 0f 00 0f 00  00 00 00 00
                 10 00 10 00  00 00 00 00
                 13 00 13 00  06 00 06 00
                 1c 00 1c 00  09 00 09 00
                 fd 0b fd 0b  00 00 00 00
                 34 01 08 01  34 01 08 01

* MEM request: 0xa1, 0x03, 0x0000003c0a00
* MEM responses: 
*               00 00 00 00  00 00 00 00  00 00
                00 00 00 00  00 00 00 00  00 00
                00 00 00 00  00 00 00 00  00 00

* MEM request: 0xa1, 0x03, 0x0000003c0400
* MEM responses:
*               00 00 00 00
                00 00 00 00
                00 00 00 00

---

Reverse-engineering contributions are welcome.
