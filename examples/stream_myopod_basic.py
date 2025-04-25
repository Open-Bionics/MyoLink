import asyncio
import logging
import time

from bleak import BleakScanner, BleakClient

# Import discovery elements
from myolink.discovery import (
    parse_advertisement_data, DeviceType, HandSpecificData,
    SensorSpecificDataV2, SensorSpecificDataV3
)
from myolink.myopod import (MyoPod, EmgStreamSource, CompressionType,
                          StreamDataPacket, READ_ONLY_CONFIG_CHAR_UUID,
                          DATA_STREAMING_SERVICE_UUID)

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TARGET_DEVICE_NAME = "MyoPod" # Name matching can be less reliable
RUN_DURATION_SECONDS = 10

# --- Notification Handler ---
def handle_emg_data(_sender: int, data: bytearray):
    """Callback function to handle incoming EMG data notifications."""
    packet: StreamDataPacket | None = MyoPod._parse_stream_data(data)
    if packet is not None:
        # Example: Print the block number and the first few data points
        points_str = ", ".join(f"{p:.2f}" for p in packet.data_points[:5])
        if len(packet.data_points) > 5:
            points_str += "..."
        logger.info(
            f"Block {packet.block_number}: TS={packet.timestamp:.3f}s, " 
            f"Src={packet.active_stream_source.name}, Comp={packet.compression_type.name}, " 
            f"Factor={packet.conversion_factor:.4f}, Points=[{points_str}] ({len(packet.data_points)} samples)"
        )
    else:
        logger.warning(f"Failed to parse data packet: {data.hex()}")


async def main():
    myopod_device = None
    myopod_ad_data = None
    logger.info(f"Scanning for Open Bionics MyoPods (OB2 Sensors)...")
    # Scan specifically for devices advertising the OB Company ID
    async with BleakScanner(detection_callback=None) as scanner:
        async for device, ad_data in scanner.advertisement_data():
            parsed_ad = parse_advertisement_data(ad_data)

            if parsed_ad:
                # Log discovered OB devices and their basic info
                dev_type = parsed_ad.device_config.device_type
                chirality = parsed_ad.device_config.chirality
                batt = parsed_ad.battery_level
                logger.debug(f"Found OB Device: {device.address} ({device.name}) - "
                             f"Type: {dev_type.name}, Chirality: {chirality.name}, Batt: {batt}%")

                # Check if it's an OB2 Sensor (MyoPod)
                if DeviceType.OB2_SENSOR == dev_type:
                    logger.info(f"Found MyoPod: {device.address} ({device.name}) - Batt: {batt}%")
                    # You could add more filtering here based on chirality, sensor type, etc.
                    # from parsed_ad.device_config or parsed_ad.device_specific_data
                    myopod_device = device
                    myopod_ad_data = parsed_ad # Store parsed data
                    break # Stop scanning once found

    if myopod_device is None:
        logger.error(f"No suitable MyoPod found.")
        return

    # Log more details about the selected device
    if myopod_ad_data:
        logger.info(f"Selected MyoPod Details: Schema={myopod_ad_data.schema_version}, "
                    f"Config={myopod_ad_data.device_config}, "
                    f"Specifics={myopod_ad_data.device_specific_data}")

    logger.info(f"Connecting to {myopod_device.address}...")
    async with BleakClient(myopod_device) as client:
        if not client.is_connected:
            logger.error(f"Failed to connect to {myopod_device.address}")
            return

        logger.info("Connected successfully.")
        myopod = MyoPod(client)

        try:
            # 1. Read initial configurations (optional but good practice)
            logger.info("Reading initial configurations...")
            try:
                read_only_conf = await myopod.read_only_configuration()
                logger.info(f"Read-Only Config: {read_only_conf}")
                stream_conf = await myopod.read_stream_configuration()
                logger.info(f"Initial Stream Config: {stream_conf}")
            except Exception as e:
                logger.warning(f"Could not read initial configs: {e}")

            # 2. Configure the desired stream to START transmission
            # Example: Raw EMG data, compressed to 16-bit integers, averaging every 5 samples
            target_source = EmgStreamSource.RAW_EMG
            target_compression = CompressionType.INT16
            target_avg_samples = 5 # Set to 1 for no averaging

            logger.info(f"Configuring stream AND telling device to start sending: Source={target_source.name}, "
                        f"Comp={target_compression.name}, AvgSamples={target_avg_samples}")
            await myopod.configure_stream(
                stream_source=target_source,
                compression=target_compression,
                average_samples=target_avg_samples
                # data_stream_schema=0 # Defaulting to schema 0
            )
            await asyncio.sleep(0.1) # Short delay after configuring

            # 3. Subscribe to notifications to actually RECEIVE the data
            logger.info("Subscribing to stream notifications...")
            await myopod.start_stream(handle_emg_data)

            # 4. Run for a defined duration
            logger.info(f"Receiving data for {RUN_DURATION_SECONDS} seconds...")
            start_time = time.monotonic()
            while time.monotonic() - start_time < RUN_DURATION_SECONDS:
                # Check connection status periodically
                if not client.is_connected:
                    logger.warning("Device disconnected unexpectedly.")
                    break
                await asyncio.sleep(0.1) # Small delay to prevent busy-waiting

        except Exception as e:
            logger.error(f"An error occurred during streaming: {e}")
        finally:
            # 5. Stop the stream - IMPORTANT: Tell device to stop FIRST, then unsubscribe

            # Tell the device to stop sending data
            logger.info("Telling device to stop sending stream data...")
            try:
                await myopod.configure_stream(EmgStreamSource.NONE)
            except Exception as e:
                logger.error(f"Error telling device to stop stream: {e}")

            # Unsubscribe the client from notifications
            if myopod.is_subscribed:
                logger.info("Unsubscribing from stream notifications...")
                try:
                    await myopod.stop_stream()
                except Exception as e:
                    logger.error(f"Error unsubscribing from stream: {e}")

            logger.info("Disconnecting...")

    logger.info("Basic MyoPod streaming example finished.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user.") 