# Hand Control Protocol

This document details the BLE communication protocol for controlling the digits and grips of the Hero RGD and Hero PRO hands.

## Target Service/Characteristic

*   **Service UUID:** `0B0B4000-FEED-DEAD-BEE5-0BE9B1091C50`
*   **Characteristic UUID (Write/Notify):** `0B0B4102-FEED-DEAD-BEE5-0BE9B1091C50` (Used for sending commands and receiving responses/notifications)

## Command Structure Overview

Commands sent to the Hand generally follow this structure:

| Offset | Length | Name               | Format             | Description                                     |
|--------|--------|--------------------|--------------------|-------------------------------------------------|
| 0      | 1      | Schema Version     | `uint8`            | Protocol version (e.g., `0x00`)                 |
| 1      | 1      | Command Code       | `uint8`            | Identifies the command (e.g., `0x06` for digits) |
| 2      | 1      | Is Request/Response | `uint8`            | `0x01` for Set/Request, `0x00` for Response     |
| 3      | 1      | Data Length        | `uint8`            | Number of bytes in the *Command Data* payload   |
| 4      | N      | Command Data       | Variable (see below) | Command-specific payload                        |

## Command: Set Digit Positions (Code `0x06`)

This command is sent by the client (App/Library) to set the position of one or more digits.

### Request Data (`Is Request/Response = 0x01`)

The *Command Data* payload for a **Set Digit Positions** request has the following structure:

| Offset | Length | Name                   | Format      | Description                                            |
|--------|--------|------------------------|-------------|--------------------------------------------------------|
| 0      | 1      | Get/Set Type           | `uint8`     | `0x01` (Set Digit Positions)                           |
| 1      | 5 * K  | Digit Positions Array | (see below) | Array containing K digit index/position pairs          |

**Digit Positions Array Element (Repeated K times):**

| Offset (within element) | Length | Name         | Format      | Description                                                              |
|-------------------------|--------|--------------|-------------|--------------------------------------------------------------------------|
| 0                       | 1      | Digit Index  | `uint8`     | Index of the digit (0=Thumb, 1=Index, 2=Middle, 3=Ring, 4=Pinky)        |
| 1                       | 4      | Digit Position | `float32` (BE)| Position value (0.0 = Fully Open, 1.0 = Fully Closed). Big-Endian format. |

### Response Data

No response data is expected from the Hand following a successful Set Digit Positions request.

### Example: Set Thumb to 50% and Index to 20%

Assuming Schema Version `0x00`:

1.  **Digit 1:** Index=`0x00` (Thumb), Position=`0.5` (float32 BE = `0x3F000000`)
2.  **Digit 2:** Index=`0x01` (Index), Position=`0.2` (float32 BE = `0x3E4CCCCD`)

*   Command Data Header: Get/Set Type = `0x01`
*   Digit 1 Data: `00 3F 00 00 00`
*   Digit 2 Data: `01 3E 4C CC CD`
*   Full Command Data Payload: `01 00 3F 00 00 00 01 3E 4C CC CD` (Length = 1 + 5 + 5 = 11 bytes)

*   Overall Command Structure Header:
    *   Schema Version: `00`
    *   Command Code: `06`
    *   Is Request: `01`
    *   Data Length: `0B` (11)

*   **Final BLE Write Value (Hex):** `00 06 01 0B 01 00 3F 00 00 00 01 3E 4C CC CD`

## Command: Get Digit Positions (Code `0x06`)

This command is sent by the client to request the current position of specific digits.

### Request Data (`Is Request/Response = 0x01`)

The *Command Data* payload for a **Get Digit Positions** request has the following structure:

| Offset | Length | Name         | Format        | Description                                            |
|--------|--------|--------------|---------------|--------------------------------------------------------|
| 0      | 1      | Get/Set Type | `uint8`       | `0x00` (Get Digit Positions)                           |
| 1      | M      | Digit Indices | `uint8` Array | Array of M digit indices (0-4) to query                |

### Response Data (`Is Request/Response = 0x00`)

The Hand responds with a message containing the positions. The *Command Data* payload structure is:

| Offset | Length | Name                   | Format      | Description                               |
|--------|--------|------------------------|-------------|-------------------------------------------|
| 0      | 5 * M  | Digit Positions Array | (see below) | Array containing M digit index/position pairs |

**Digit Positions Array Element (Repeated M times):** (Same format as Set request element)

| Offset (within element) | Length | Name         | Format      | Description                                                              |
|-------------------------|--------|--------------|-------------|--------------------------------------------------------------------------|
| 0                       | 1      | Digit Index  | `uint8`     | Index of the digit (0=Thumb, 1=Index, 2=Middle, 3=Ring, 4=Pinky)        |
| 1                       | 4      | Digit Position | `float32` (BE)| Position value (0.0 = Fully Open, 1.0 = Fully Closed). Big-Endian format. |

*(Note: Need details on Grip commands (Code 0x07?) to add here)*

## Other Commands (if any)

*   (TODO: Add details for Grip Control - Command Code `0x07`)
*   (TODO: Add other commands if discovered) 