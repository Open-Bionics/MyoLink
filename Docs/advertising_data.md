# Open Bionics BLE Advertising Data Format

This document details the structure of the Bluetooth Low Energy (BLE) advertising data packets broadcast by Open Bionics devices (OB2 Hand, OB2 Sensor).

Understanding this format is crucial for discovering devices and extracting initial information like device type, battery level, and status without establishing a full connection.

## General Advertising Packet Structure

The advertising packet typically contains several data elements (AD Types). Key elements relevant to Open Bionics devices include:

*   **Flags (AD Type `0x01`):** Indicates device capabilities. Usually `0x06` (LE General Discoverable Mode, BR/EDR Not Supported).
*   **Tx Power Level (AD Type `0x0A`):** Indicates the transmission power (e.g., `0` for 0 dBm).
*   **Shortened Local Name (AD Type `0x08`):** A user-settable name (e.g., "Hero Hand Left"). Note: This might not always be present or unique.
*   **Manufacturer Specific Data (AD Type `0xFF`):** This is the primary data structure containing Open Bionics specific information. See details below.

## Manufacturer Specific Data Format

This data element starts with the Open Bionics Company ID (`0x0ABA`).

| Offset | Length (bytes) | Description             | Value      | Notes                         |
| :----- | :------------- | :---------------------- | :--------- | :---------------------------- |
| 0      | 2              | Company ID              | `0x0ABA`   | Identifies Open Bionics     |
| 2      | 1              | Schema Version          | Varies     | Defines format of next section |
| 3      | Variable       | Schema Specific Data    | Varies     | See Schema Versions below     |

### Schema Version

This byte indicates the format of the `Schema Specific Data` that follows.

| Version | Comments                                                               |
| :------ | :--------------------------------------------------------------------- |
| 0       | Hero BLE Advertising Data format (Not OB2 - Document d100874 V1.00)    |
| 1       | Initial OB2 BLE Advertising Data format (Deprecated by Sidekick 1.4.1+)|
| 2       | Updated association data format                                        |
| 3       | Updated Sensor Device Specific Data format                             |

### Schema Specific Data - Version 1 (Deprecated)

*Total Length (Schema Specific Data): 9 bytes*

| Offset (from start of Mfg Data) | Length (bytes) | Description          | Example Value                    |
| :------------------------------ | :------------- | :------------------- | :------------------------------- |
| 3                               | 1              | Device Configuration | `0x05`                           |
| 4                               | 1              | Device Specific Data | `0x00`                           |
| 5                               | 1              | Battery Level (%)    | `100`                            |
| 6                               | 6              | Association ID       | `0x01:0x23:0x45:0x67:0x89:0xAB`  |

### Schema Specific Data - Version 2

*Total Length (Schema Specific Data): Variable (8 + 4 * N_assoc bytes)*

| Offset (from start of Mfg Data) | Length (bytes) | Description               | Example Value              | Notes                                 |
| :------------------------------ | :------------- | :------------------------ | :------------------------- | :------------------------------------ |
| 3                               | 1              | Device Configuration      | `0x05`                     | See Details Below                     |
| 4                               | 1              | Device Specific Data      | `0x00`                     | See Details Below                     |
| 5                               | 1              | Battery Level (%)         | `100`                      | 0-100%                                |
| 6                               | 4              | MAC Address (Partial)     | `0x01234567`               | Lower 4 bytes of MAC, Big-Endian      |
| 10                              | 1              | Number of Associations    | `N` (e.g., `2`)            | Number of IDs in the following array  |
| 11                              | 4 * N          | Association IDs Array     | `[0x01234567, 0x89ABCDEF]` | Array of N 4-byte IDs, Big-Endian   |

### Schema Specific Data - Version 3

*Total Length (Schema Specific Data): Variable (8 + 4 * N_assoc bytes)*

Same structure as Version 2, but the interpretation of the `Device Specific Data` byte *for Sensors* changes.

| Offset (from start of Mfg Data) | Length (bytes) | Description               | Example Value              | Notes                                 |
| :------------------------------ | :------------- | :------------------------ | :------------------------- | :------------------------------------ |
| 3                               | 1              | Device Configuration      | `0x05`                     | See Details Below                     |
| 4                               | 1              | Device Specific Data      | `0x00`                     | **See V3 Sensor Details Below**       |
| 5                               | 1              | Battery Level (%)         | `100`                      | 0-100%                                |
| 6                               | 4              | MAC Address (Partial)     | `0x01234567`               | Lower 4 bytes of MAC, Big-Endian      |
| 10                              | 1              | Number of Associations    | `N` (e.g., `2`)            | Number of IDs in the following array  |
| 11                              | 4 * N          | Association IDs Array     | `[0x01234567, 0x89ABCDEF]` | Array of N 4-byte IDs, Big-Endian   |

## Device Configuration Byte

This byte provides general information about the device.

| Bit(s) | Name           | Description                                           |
| :----- | :------------- | :---------------------------------------------------- |
| 7      | HIL            | `1` = Hardware In the Loop test setup device.         |
| 6      | Bootloader     | `1` = Device is currently in bootloader mode.         |
| 5-4    | Reserved       | Must be `0`.                                          |
| 3-1    | Device Type    | Identifies the type of device (see table below).      |
| 0      | Chirality      | Identifies Left/Right (Hand) or Open/Close (Sensor).  |

### Chirality (Bit 0)

| Value | Hand Meaning | Sensor Meaning |
| :---- | :----------- | :------------- |
| 0     | Right        | Close (1 dot)  |
| 1     | Left         | Open (2 dots)  |

### Device Type (Bits 3-1)

| Value (Binary) | Value (Decimal) | Meaning    |
| :------------- | :-------------- | :--------- |
| `000`          | 0               | Hero Arm   |
| `001`          | 1               | OB2 Hand   |
| `010`          | 2               | OB2 Sensor |
| `011` - `111`  | 3 - 7           | Reserved   |

## Device Specific Data Byte

The interpretation of this byte depends on the `Device Type`.

### Hero Arm Specific Data

Identifies the motor configuration.

| Value  | Motor Configuration |
| :----- | :------------------ |
| `0x00` | Unknown             |
| `0x03` | Maxon 3 motor       |
| `0x04` | Maxon 4 Motor       |

### OB2 Hand Specific Data

| Bit(s) | Name       | Description                               |
| :----- | :--------- | :---------------------------------------- |
| 7-4    | Reserved   | Must be `0`.                              |
| 3-2    | Class      | Identifies the Hand class (see table).    |
| 1-0    | Size       | Identifies the Hand size (see table).     |

#### Hand Size (Bits 1-0)

| Value (Binary) | Value (Decimal) | Meaning     |
| :------------- | :-------------- | :---------- |
| `00`           | 0               | Extra Small |
| `01`           | 1               | Small       |
| `10`           | 2               | Medium      |
| `11`           | 3               | Large       |

#### Hand Class (Bits 3-2)

| Value (Binary) | Value (Decimal) | Meaning    |
| :------------- | :-------------- | :--------- |
| `00`           | 0               | OB2 Air    |
| `01`           | 1               | OB2 Pro    |
| `10`           | 2               | OB2 Rugged |
| `11`           | 3               | Reserved   |

### OB2 Sensor Specific Data

#### Schema Version 1

| Bit(s) | Name        | Description                               |
| :----- | :---------- | :---------------------------------------- |
| 7-2    | Reserved    | Must be `0`.                              |
| 1-0    | Sensor Type | Identifies the Sensor type (see table).   |

#### Schema Version 2

| Bit(s) | Name                      | Description                                                   |
| :----- | :------------------------ | :------------------------------------------------------------ |
| 7-3    | Reserved                  | Must be `0`.                                                  |
| 2      | Open For Association (OFA)| `1` = Sensor is open for a new association.                 |
| 1-0    | Sensor Type               | Identifies the Sensor type (see table).                       |

#### Schema Version 3

| Bit(s) | Name               | Description                                         |
| :----- | :----------------- | :-------------------------------------------------- |
| 7-5    | Reserved           | Must be `0`.                                        |
| 4      | Leads Status       | `1` = Sensor leads detected on user.                |
| 3-2    | Advertising Reason | Why the sensor is advertising (see table).          |
| 1-0    | Sensor Type        | Identifies the Sensor type (see table).             |

#### Sensor Type (Bits 1-0 for all Schemas)

| Value (Binary) | Value (Decimal) | Meaning  |
| :------------- | :-------------- | :------- |
| `00`           | 0               | EMG      |
| `01` - `11`  | 1 - 3           | Reserved |

#### Sensor Advertising Reason (Bits 3-2 for Schema V3)

| Value (Binary) | Value (Decimal) | Meaning                         |
| :------------- | :-------------- | :------------------------------ |
| `00`           | 0               | Connect to associated Hand      |
| `01`           | 1               | Form new Hand association       |
| `10`           | 2               | Connect to App                  |
| `11`           | 3               | Advertising on request from Hand| 