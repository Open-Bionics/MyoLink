import asyncio
from bleak import BleakClient
from enum import Enum
from typing import Callable, Any, TYPE_CHECKING
import logging
import struct # Added for packing data

# Type hint StreamDataPacket without circular import
if TYPE_CHECKING:
    from myolink.myopod import StreamDataPacket

# --- Service UUIDs ---
DATA_STREAMING_SERVICE_UUID = "0B0B3000-FEED-DEAD-BEE5-08E9B1091C50"
# CONTROL_SERVICE_UUID = "0B0B4000-FEED-DEAD-BEE5-0BE9B1091C50" # No longer needed for MyoPod stream control
# Add other service UUIDs (Battery, Settings, etc.) as needed

# --- Characteristic UUIDs ---
# Data Streaming Service Chars
READ_ONLY_CONFIG_CHAR_UUID = "0B0B3100-FEED-DEAD-BEE5-0BE9B1091C50"
DATA_STREAM_CONFIG_CHAR_UUID = "0B0B3101-FEED-DEAD-BEE5-0BE9B1091C50"
DATA_STREAM_CHAR_UUID = "0B0B3102-FEED-DEAD-BEE5-0BE9B1091C50"

# Control Service Chars
# CONTROL_COMMANDS_CHAR_UUID = "0B0B4102-FEED-DEAD-BEE5-0BE9B1091C50" # No longer needed for MyoPod stream control

# --- Schema Versions & Constants ---
READ_ONLY_CONFIG_SCHEMA_VERSION = 0x00
DATA_STREAM_CONFIG_SCHEMA_VERSION = 0x00
DATA_STREAM_SCHEMA_VERSION = 0x00
# CONTROL_COMMAND_SCHEMA_VERSION = 0x00 # No longer needed
# CONTROL_CMD_STATUS_IS_REQUEST = 0b00000001 # No longer needed

# --- Active Stream Types (Upper Nibble of Config Byte) ---
# From section 9.2.2.4.1
ACTIVE_STREAM_NONE = 0x00
ACTIVE_STREAM_PROCESSED_EMG = 0x01
ACTIVE_STREAM_FILTERED_EMG = 0x02
ACTIVE_STREAM_RAW_EMG = 0x03
ACTIVE_STREAM_IMU = 0x04
ACTIVE_STREAM_TEMPERATURES = 0x05
ACTIVE_STREAM_FAKE_EMG = 0x06
ACTIVE_STREAM_AMP_OUTPUT = 0x07
# Values 8-15 reserved/N/A

# --- Compression Types (Lower Nibble of Config Byte) ---
# From section 9.2.2.4.2
COMPRESSION_NONE = 0x00      # 32-bit float
COMPRESSION_INT16 = 0x01     # 16-bit signed int
COMPRESSION_BYTE_PACK = 0x02 # 4x 12-bit signed int -> 6 bytes
COMPRESSION_RES_LIMIT = 0x03 # 8-bit signed int (MSB)
# Values 4-15 reserved/N/A

# --- Placeholder Control Command Codes --- (REMOVED)
# CMD_CODE_START_STREAM = 0xAA
# CMD_CODE_STOP_STREAM = 0xBB

# --- Stream Type Byte Placeholders (No longer needed, constructed dynamically) ---
# STREAM_TYPE_BYTE_RAW = 0x01 # Replace with actual byte value for Raw EMG
# STREAM_TYPE_BYTE_AVG = 0x02 # Replace with actual byte value for Averaged EMG

logger = logging.getLogger(__name__)

class EmgStreamSource(Enum):
    """Defines the source of the data stream (maps to Active Stream type)."""
    NONE = ACTIVE_STREAM_NONE
    PROCESSED_EMG = ACTIVE_STREAM_PROCESSED_EMG # %
    FILTERED_EMG = ACTIVE_STREAM_FILTERED_EMG   # mV
    RAW_EMG = ACTIVE_STREAM_RAW_EMG             # mV
    IMU = ACTIVE_STREAM_IMU                     # TBD
    TEMPERATURES = ACTIVE_STREAM_TEMPERATURES   # C
    FAKE_EMG = ACTIVE_STREAM_FAKE_EMG           # %
    AMP_OUTPUT = ACTIVE_STREAM_AMP_OUTPUT       # mV

class CompressionType(Enum):
    """Defines the compression type for the data stream."""
    NONE = COMPRESSION_NONE
    INT16 = COMPRESSION_INT16
    BYTE_PACK_12BIT = COMPRESSION_BYTE_PACK
    RES_LIMIT_8BIT = COMPRESSION_RES_LIMIT

# --- Data Structures for Parsed Data ---
from dataclasses import dataclass

@dataclass
class ReadOnlyConfig:
    """Parsed data from the Read Only Configuration characteristic (0x3100)."""
    read_only_schema: int
    max_editable_config_schema: int
    max_data_stream_schema: int
    service_version: int
    sync_timestamp: float

@dataclass
class StreamConfiguration:
    """Parsed data from the Data Stream Configuration characteristic (0x3101 - Read/Notify)."""
    config_schema: int
    average_samples: int
    active_stream_type_byte: int # Raw byte combining stream & compression
    data_stream_schema: int
    native_sample_rate_hz: int
    conversion_factor: float

    @property
    def active_stream_source(self) -> EmgStreamSource:
        source_val = (self.active_stream_type_byte >> 4) & 0x0F
        try:
            return EmgStreamSource(source_val)
        except ValueError:
            logger.warning(f"Unknown active stream source value: {source_val}")
            return EmgStreamSource.NONE # Or raise an error

    @property
    def compression_type(self) -> CompressionType:
        comp_val = self.active_stream_type_byte & 0x0F
        try:
            return CompressionType(comp_val)
        except ValueError:
            logger.warning(f"Unknown compression type value: {comp_val}")
            return CompressionType.NONE # Or raise an error

@dataclass
class StreamDataPacket:
    """Parsed data from a Data Stream characteristic notification (0x3102)."""
    data_schema: int
    block_number: int
    active_stream_type_byte: int # Raw byte combining stream & compression
    timestamp: float # Relative to device time
    conversion_factor: float
    data_points: list[Any] # List of parsed data points (float, int)

    @property
    def active_stream_source(self) -> EmgStreamSource:
        source_val = (self.active_stream_type_byte >> 4) & 0x0F
        try:
            return EmgStreamSource(source_val)
        except ValueError:
            logger.warning(f"Unknown active stream source value: {source_val}")
            return EmgStreamSource.NONE

    @property
    def compression_type(self) -> CompressionType:
        comp_val = self.active_stream_type_byte & 0x0F
        try:
            return CompressionType(comp_val)
        except ValueError:
            logger.warning(f"Unknown compression type value: {comp_val}")
            return CompressionType.NONE

class MyoPod:
    """Represents a MyoPod EMG sensor device."""

    def __init__(self, client: BleakClient):
        """
        Initialises the MyoPod instance.

        Args:
            client: The BleakClient connected to the MyoPod device.
        """
        if None is client:
            raise ValueError("BleakClient cannot be None")
        if not client.is_connected:
            raise ValueError("BleakClient must be connected")
        self._client = client
        self._is_subscribed = False # Renamed from _is_streaming for clarity
        # Store configured/read values
        self._sync_timestamp: float | None = None
        self._current_config: StreamConfiguration | None = None
        # Store the user's handler for parsed data
        self._parsed_data_handler: Callable[['StreamDataPacket'], Any] | None = None

    # --- Internal Raw Notification Handler ---
    def _raw_notification_handler(self, sender: int, data: bytearray):
        """Internal handler for raw Bleak notifications."""
        if self._parsed_data_handler:
            packet = MyoPod._parse_stream_data(data)
            if packet is not None:
                try:
                    # Call the user's handler with the parsed packet
                    self._parsed_data_handler(packet)
                except Exception as e:
                    # Log exceptions in the user's handler to avoid breaking the stream
                    logger.error(f"Error in user-provided notification handler: {e}", exc_info=True)
            else:
                # Log if parsing failed, as the user's handler won't be called
                logger.warning(f"Failed to parse data packet (handler not called): {data.hex()}")
        else:
            # This shouldn't happen if _is_subscribed is True, but log just in case
             logger.warning(f"Received stream data but no handler is set. Data: {data.hex()}")

    async def configure_stream(self, stream_source: EmgStreamSource, compression: CompressionType = CompressionType.NONE, average_samples: int = 1, data_stream_schema: int = 0) -> None:
        """Configures the MyoPod data stream, starting or stopping it.

        Writes to the Data Stream Configuration characteristic (0x3101).
        Setting stream_source to a value other than EmgStreamSource.NONE
        tells the device to start streaming.
        Setting stream_source to EmgStreamSource.NONE tells the device to stop.

        Note: This *configures* the stream on the device. You must separately call
        `start_stream()` to subscribe to notifications and `stop_stream()` to
        unsubscribe on the client side.

        Args:
            stream_source: The desired data source (or EmgStreamSource.NONE to stop).
            compression: The desired compression type.
            average_samples: Number of samples to average (uint16).
            data_stream_schema: The desired schema version for the Data Stream characteristic (0x3102).
        """
        if self._is_subscribed:
            logger.warning("Cannot configure stream while already subscribed. Stop stream first.")
            return
        if average_samples < 1 or average_samples > 65535:
            raise ValueError("average_samples must be between 1 and 65535")
        if data_stream_schema > 255:
            raise ValueError("data_stream_schema must be 0-255")

        # Combine Active Stream (upper nibble) and Compression (lower nibble)
        active_stream_byte = (stream_source.value << 4) | compression.value

        # Warn if averaging is set for non-averaged sources (though allowed by protocol)
        if average_samples > 1 and stream_source not in [EmgStreamSource.PROCESSED_EMG, EmgStreamSource.FILTERED_EMG, EmgStreamSource.RAW_EMG, EmgStreamSource.IMU]:
            logger.warning(f"Averaging ({average_samples} samples) requested for stream source {stream_source.name}, which might not be typical.")

        # --- Construct the configuration payload (Schema Version 0 Write) ---
        # Offset 0: Data Schema Version (1 byte) = DATA_STREAM_CONFIG_SCHEMA_VERSION
        # Offset 1: Average Samples (Unsigned 16-bit, big-endian)
        # Offset 3: Active Stream / Compression Type (8-bit mask)
        # Offset 4: Data Stream Schema Version (Unsigned 8-bit)
        config_payload = struct.pack(
            '>BHBB', # Big-endian: byte, uint16, byte, byte
            DATA_STREAM_CONFIG_SCHEMA_VERSION,
            average_samples,        # uint16_t
            active_stream_byte,     # uint8_t
            data_stream_schema      # uint8_t
        )

        try:
            action = "Starting" if stream_source != EmgStreamSource.NONE else "Stopping"
            logger.debug(f"{action} stream by writing configuration {config_payload.hex()} to {DATA_STREAM_CONFIG_CHAR_UUID}")
            await self._client.write_gatt_char(DATA_STREAM_CONFIG_CHAR_UUID, config_payload, response=False)
            # Clear previous read config as it's now potentially stale
            self._current_config = None
            if stream_source != EmgStreamSource.NONE:
                 logger.info(f"MyoPod stream configured/started on device: Source={stream_source.name}, Comp={compression.name}, AvgSamples={average_samples}, StreamSchema={data_stream_schema}")
            else:
                logger.info("MyoPod stream stopped on device.")
        except Exception as e:
            logger.error(f"Failed to configure/control MyoPod stream: {e}")
            raise

    async def start_stream(self, parsed_data_handler: Callable[['StreamDataPacket'], Any]) -> None:
        """Subscribe to notifications from the Data Stream characteristic (0x3102).

        This allows the client to receive data when the device is streaming.
        The provided handler will be called with a parsed `StreamDataPacket` object
        for each valid notification received.

        It does NOT tell the device to start sending data; use `configure_stream`
        with a non-NONE source for that.

        Args:
            parsed_data_handler: The asynchronous callback function to handle incoming
                parsed data. It should accept one argument: `packet` (StreamDataPacket).
        """
        if self._is_subscribed:
            logger.warning("Already subscribed to stream notifications.")
            return
        if None is parsed_data_handler:
            raise ValueError("parsed_data_handler cannot be None")

        try:
            logger.debug(f"Subscribing to data notifications from {DATA_STREAM_CHAR_UUID}")
            # Store the user's handler first
            self._parsed_data_handler = parsed_data_handler
            # Subscribe using the internal raw handler
            await self._client.start_notify(DATA_STREAM_CHAR_UUID, self._raw_notification_handler)
            self._is_subscribed = True
            logger.info("Subscribed to MyoPod stream notifications.")
        except Exception as e:
            logger.error(f"Failed to subscribe to MyoPod stream: {e}")
            self._is_subscribed = False # Ensure state is correct on failure
            self._parsed_data_handler = None # Clear handler on failure
            raise e

    async def stop_stream(self) -> None:
        """Unsubscribe from notifications on the Data Stream characteristic (0x3102).

        This stops the client from receiving data.
        It does NOT tell the device to stop sending data; use `configure_stream`
        with EmgStreamSource.NONE for that.
        """
        if not self._is_subscribed:
            logger.warning("Not currently subscribed to stream notifications.")
            return

        try:
            logger.debug(f"Unsubscribing from data notifications from {DATA_STREAM_CHAR_UUID}")
            await self._client.stop_notify(DATA_STREAM_CHAR_UUID)
            self._is_subscribed = False
            self._parsed_data_handler = None # Clear the handler
            logger.info("Unsubscribed from MyoPod stream notifications.")
        except Exception as e:
            logger.error(f"Failed to unsubscribe from MyoPod stream: {e}")
            # Note: Client might still be considered subscribed by bleak even if device fails?
            # For safety, set state to False and clear handler.
            self._is_subscribed = False
            self._parsed_data_handler = None
            raise

    @property
    def is_subscribed(self) -> bool:
        """Returns True if the client is currently subscribed to EMG stream notifications."""
        return self._is_subscribed

    @property
    def is_connected(self) -> bool:
        """Returns True if the BleakClient is currently connected."""
        return self._client.is_connected

    # --- NEW READ METHODS ---

    async def read_only_configuration(self) -> ReadOnlyConfig:
        """Reads the Read Only Configuration characteristic (0x3100).

        Returns:
            A ReadOnlyConfig dataclass instance.
        """
        try:
            logger.debug(f"Reading read-only config from {READ_ONLY_CONFIG_CHAR_UUID}")
            data = await self._client.read_gatt_char(READ_ONLY_CONFIG_CHAR_UUID)
            logger.debug(f"Read read-only config data: {data.hex()}")

            # Parse Schema Version 0 (assuming this based on 9.2.1.1)
            # Offset 0: Read Only Schema (uint8)
            # Offset 1: Max Editable Schema (uint8)
            # Offset 2: Max Data Stream Schema (uint8)
            # Offset 3: Service Version (uint8)
            # Offset 4: Sync Timestamp (float32, big-endian)
            read_only_schema, max_edit_schema, max_stream_schema, service_ver, sync_ts = struct.unpack('>BBBBf', data)

            if READ_ONLY_CONFIG_SCHEMA_VERSION != read_only_schema:
                logger.warning(f"Unexpected read-only config schema version: {read_only_schema}. Parsing as version 0.")

            config = ReadOnlyConfig(
                read_only_schema=read_only_schema,
                max_editable_config_schema=max_edit_schema,
                max_data_stream_schema=max_stream_schema,
                service_version=service_ver,
                sync_timestamp=sync_ts
            )
            self._sync_timestamp = sync_ts # Cache the sync timestamp
            logger.info(f"Read read-only config: {config}")
            return config
        except Exception as e:
            logger.error(f"Failed to read MyoPod read-only configuration: {e}")
            raise

    async def read_stream_configuration(self) -> StreamConfiguration:
        """Reads the Data Stream Configuration characteristic (0x3101).

        Returns:
            A StreamConfiguration dataclass instance.
        """
        try:
            logger.debug(f"Reading stream config from {DATA_STREAM_CONFIG_CHAR_UUID}")
            data = await self._client.read_gatt_char(DATA_STREAM_CONFIG_CHAR_UUID)
            logger.debug(f"Read stream config data: {data.hex()}")

            # Parse Schema Version 0 Read/Notify format (assuming this based on 9.2.2.2.1)
            # Offset 0: Data Schema Version (uint8)
            # Offset 1: Average Samples (uint16, big-endian)
            # Offset 3: Active Stream / Compression Type (uint8)
            # Offset 4: Data Stream Schema Version (uint8)
            # Offset 5: Native Sample Rate (uint16, big-endian)
            # Offset 7: Conversion Factor (float32, big-endian)
            config_schema, avg_samples, active_byte, stream_schema, native_rate, conv_factor = struct.unpack('>BHBBHf', data)

            if DATA_STREAM_CONFIG_SCHEMA_VERSION != config_schema:
                 logger.warning(f"Unexpected stream config schema version: {config_schema}. Parsing as version 0.")

            config = StreamConfiguration(
                config_schema=config_schema,
                average_samples=avg_samples,
                active_stream_type_byte=active_byte,
                data_stream_schema=stream_schema,
                native_sample_rate_hz=native_rate,
                conversion_factor=conv_factor
            )
            self._current_config = config # Cache the current config
            logger.info(f"Read stream config: {config}")
            return config
        except Exception as e:
            logger.error(f"Failed to read MyoPod stream configuration: {e}")
            raise

    # --- Data Stream Parsing ---

    @staticmethod
    def _parse_stream_data(data: bytearray) -> 'StreamDataPacket | None':
        """Parses a raw data packet from the Data Stream characteristic (0x3102).

        Args:
            data: The raw bytearray received from the notification.

        Returns:
            A StreamDataPacket dataclass instance, or None if parsing fails.
        """
        try:
            # --- Parse Header (Schema Version 0) ---
            # Offset 0: Data Schema Version (uint8)
            # Offset 1: Stream Block Number (uint8)
            # Offset 2: Active Stream / Compression Type (uint8)
            # Offset 3: Stream Block Timestamp (float32, big-endian)
            # Offset 7: Conversion Factor (float32, big-endian)
            # Offset 11: Stream Data Length (uint8)
            # Offset 12: Stream Data (variable)
            header_format = '>BBBffB' # B=uint8, H=uint16, f=float32
            header_size = struct.calcsize(header_format)

            if len(data) < header_size:
                logger.error(f"Stream data packet too short for header: {len(data)} bytes")
                return None

            data_schema, block_num, active_byte, timestamp, conv_factor, data_len = struct.unpack(header_format, data[:header_size])

            if DATA_STREAM_SCHEMA_VERSION != data_schema:
                logger.warning(f"Unexpected data stream schema version: {data_schema}. Parsing as version 0.")

            if len(data) < header_size + data_len:
                logger.error(f"Stream data packet shorter than indicated data length: {len(data)} bytes, expected {header_size + data_len}")
                return None

            stream_data_bytes = data[header_size : header_size + data_len]

            # --- Parse Stream Data based on Compression Type ---
            compression_type_val = active_byte & 0x0F
            data_points = []

            try:
                compression_type = CompressionType(compression_type_val)
            except ValueError:
                logger.error(f"Unknown compression type in data packet: {compression_type_val}")
                return None # Cannot parse data without knowing compression

            if CompressionType.NONE == compression_type:
                # 32-bit float per sample (4 bytes)
                num_samples = data_len // 4
                if data_len % 4 != 0:
                     logger.warning(f"Data length {data_len} not multiple of 4 for No Compression.")
                format_string = f'>{num_samples}f' # e.g., '>5f' for 5 floats
                if num_samples > 0:
                    data_points = list(struct.unpack(format_string, stream_data_bytes[:num_samples*4]))

            elif CompressionType.INT16 == compression_type:
                # 16-bit signed int per sample (2 bytes)
                num_samples = data_len // 2
                if data_len % 2 != 0:
                    logger.warning(f"Data length {data_len} not multiple of 2 for Integer Conversion.")
                format_string = f'>{num_samples}h' # 'h' is short signed int
                if num_samples > 0:
                    data_points = list(struct.unpack(format_string, stream_data_bytes[:num_samples*2]))

            elif CompressionType.RES_LIMIT_8BIT == compression_type:
                # 8-bit signed int per sample (1 byte)
                num_samples = data_len
                format_string = f'>{num_samples}b' # 'b' is signed char
                if num_samples > 0:
                    data_points = list(struct.unpack(format_string, stream_data_bytes))

            elif CompressionType.BYTE_PACK_12BIT == compression_type:
                # 4x 12-bit signed int packed into 6 bytes
                num_frames = data_len // 6
                if data_len % 6 != 0:
                    logger.warning(f"Data length {data_len} not multiple of 6 for Byte Packing.")

                for i in range(num_frames):
                    frame_bytes = stream_data_bytes[i*6 : (i+1)*6]
                    if len(frame_bytes) < 6: continue # Should not happen if check above works

                    # Unpack the 6 bytes into 3 uint16 values (big-endian)
                    val0, val1, val2 = struct.unpack('>HHH', frame_bytes)

                    # Reconstruct the four 12-bit signed values
                    s0 = (val0 >> 4)           # Top 12 bits of val0
                    s1 = ((val0 & 0x0F) << 8) | (val1 >> 8) # Bottom 4 of val0 + Top 8 of val1
                    s2 = ((val1 & 0xFF) << 4) | (val2 >> 12) # Bottom 8 of val1 + Top 4 of val2
                    s3 = (val2 & 0x0FFF)       # Bottom 12 bits of val2

                    # Convert to signed 12-bit (handle sign extension)
                    s0 = s0 if s0 < 2048 else s0 - 4096
                    s1 = s1 if s1 < 2048 else s1 - 4096
                    s2 = s2 if s2 < 2048 else s2 - 4096
                    s3 = s3 if s3 < 2048 else s3 - 4096

                    data_points.extend([s0, s1, s2, s3])
            else:
                logger.error(f"Parsing not implemented for compression type: {compression_type.name}")

            # Apply conversion factor if data is numerical (handle potential strings etc. later if needed)
            # For now, assumes data_points contains numbers
            final_data_points = [dp * conv_factor for dp in data_points]

            return StreamDataPacket(
                data_schema=data_schema,
                block_number=block_num,
                active_stream_type_byte=active_byte,
                timestamp=timestamp,
                conversion_factor=conv_factor,
                data_points=final_data_points
            )

        except struct.error as e:
            logger.error(f"Failed to unpack stream data: {e}. Data: {data.hex()}")
            return None
        except Exception as e:
            logger.error(f"Error parsing stream data: {e}. Data: {data.hex()}")
            return None

async def main():
    # Example usage placeholder - Needs implementation
    # e.g., connect, read configs, configure stream, start stream with handler,
    # handler calls _parse_stream_data, stop stream
    pass

if "__main__" == __name__:
    asyncio.run(main()) 