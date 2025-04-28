# MyoPod Data Streaming Protocol

## Quick Reference

- **Service UUID:** `0B0B3000-FEED-DEAD-BEE5-08E9B1091C50`
- **Data Stream Config Characteristic:** `0B0B3101-FEED-DEAD-BEE5-0BE9B1091C50`
- **Data Stream Characteristic:** `0B0B3102-FEED-DEAD-BEE5-0BE9B1091C50`

---

## 1. Data Stream Configuration Characteristic (`0x3101`)

### 1.1. Format (Read/Notify, Schema Version 0)

| Offset | Length | Name                        | Format         | Description                                 |
|--------|--------|-----------------------------|----------------|---------------------------------------------|
| 0      | 1      | Data Schema Version         | uint8          | Always 0                                    |
| 1      | 2      | Average Samples             | uint16 (BE)    | Number of samples to average                |
| 3      | 1      | Active Stream/Compression   | uint8          | Upper nibble: stream, lower: compression    |
| 4      | 1      | Data Stream Schema Version  | uint8          | Usually 0                                   |
| 5      | 2      | Native Sample Rate (Hz)     | uint16 (BE)    | Device's native sample rate                 |
| 7      | 4      | Conversion Factor           | float32 (BE)   | Multiply data points by this for units      |

### 1.2. Format (Write, Schema Version 0)

| Offset | Length | Name                        | Format         | Description                                 |
|--------|--------|-----------------------------|----------------|---------------------------------------------|
| 0      | 1      | Data Schema Version         | uint8          | Always 0                                    |
| 1      | 2      | Average Samples             | uint16 (BE)    | Number of samples to average                |
| 3      | 1      | Active Stream/Compression   | uint8          | Upper nibble: stream, lower: compression    |
| 4      | 1      | Data Stream Schema Version  | uint8          | Usually 0                                   |

---

### 1.3. Field Details

- **Average Samples:**  
  - Default is 1 (full native rate).  
  - Setting to N means 1 sample is streamed for every N captured (data is averaged).  
  - Effective stream rate = Native Sample Rate / Average Samples.

- **Active Stream / Compression Type:**  
  - Upper nibble (bits 7-4): Stream type (see table below).  
  - Lower nibble (bits 3-0): Compression type (see table below).

#### Stream Types (Upper Nibble)

| Value | Name                | Units | Description                        |
|-------|---------------------|-------|------------------------------------|
| 0     | NONE                | N/A   | No active stream                   |
| 1     | PROCESSED_EMG       | %     | Processed EMG data                 |
| 2     | FILTERED_EMG        | mV    | Filtered EMG data                  |
| 3     | RAW_EMG             | mV    | Raw EMG data                       |
| 4     | IMU                 | TBD   | IMU data stream                    |
| 5     | TEMPERATURES        | C     | Temperature data                   |
| 6     | FAKE_EMG            | %     | Fake EMG data                      |
| 7     | AMP_OUTPUT          | mV    | Instrumentation amplifier output    |
| 8-15  | RESERVED            |       |                                    |

#### Compression Types (Lower Nibble)

| Value | Name                | Description                                      |
|-------|---------------------|--------------------------------------------------|
| 0     | NONE                | 32-bit float per sample                          |
| 1     | INT16               | 16-bit signed integer per sample                 |
| 2     | BYTE_PACK_12BIT     | 4x 12-bit signed ints packed into 6 bytes        |
| 3     | RES_LIMIT_8BIT      | 8-bit signed int (MSB of 12-bit value)           |
| 4-15  | RESERVED            |                                                  |

- **Conversion Factor:**  
  - Multiply each data point by this value to convert to physical units (e.g., mV, °C).

---

## 2. Data Stream Characteristic (`0x3102`)

### 2.1. Format (Notification, Schema Version 0)

| Offset | Length | Name                        | Format         | Description                                 |
|--------|--------|-----------------------------|----------------|---------------------------------------------|
| 0      | 1      | Data Schema Version         | uint8          | Always 0                                    |
| 1      | 1      | Stream Block Number         | uint8          | Increments by 1 for each block, wraps at 255|
| 2      | 1      | Active Stream/Compression   | uint8          | As above                                    |
| 3      | 4      | Stream Block Timestamp      | float32 (BE)   | Time since sync (see read-only config)      |
| 7      | 4      | Conversion Factor           | float32 (BE)   | As above                                    |
| 11     | 1      | Stream Data Length          | uint8          | Number of bytes of stream data              |
| 12     | N      | Stream Data                 | varies         | See compression type                        |

#### Stream Data Layout by Compression

| Compression Type   | Bytes per Frame | Samples per Frame | Format/Notes                                 |
|--------------------|-----------------|------------------|----------------------------------------------|
| NONE               | 4               | 1                | float32 (BE)                                 |
| INT16              | 2               | 1                | int16 (BE)                                   |
| BYTE_PACK_12BIT    | 6               | 4                | 4x 12-bit signed ints packed into 3x uint16  |
| RES_LIMIT_8BIT     | 1               | 1                | int8 (MSB of 12-bit value)                   |

---

## 3. Notes

- **Stream Block Number:**  
  - Used to detect missed packets (should increment by 1, wraps at 255).

- **Stream Block Timestamp:**  
  - Time (in seconds) since device synchronisation (see read-only config).
  - Useful for synchronising data from multiple devices.

- **Average Samples:**  
  - Higher values reduce the data rate and provide averaged data.

- **Conversion Factor:**  
  - Always apply this to raw data points to get physical units.

---

## 4. Example

**To stream Raw EMG at 200 Hz with INT16 compression and no averaging:**
- Set stream type to RAW_EMG (3), compression to INT16 (1), average_samples to 1.
- Effective rate = native sample rate (e.g., 200 Hz) / 1 = 200 Hz.

**To stream at 20 Hz:**
- Set average_samples to 10 (200 / 10 = 20 Hz).