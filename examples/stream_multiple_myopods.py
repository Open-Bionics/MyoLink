import asyncio
import logging
import time
import functools
from typing import Dict, Tuple

from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from myolink.discovery import (
    parse_advertisement_data, DeviceType, ParsedAdvertisingData
)
from myolink.myopod import (
    MyoPod, EmgStreamSource, CompressionType,
    StreamDataPacket, # Import StreamDataPacket
    READ_ONLY_CONFIG_CHAR_UUID,
    DATA_STREAMING_SERVICE_UUID
)

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TARGET_DEVICE_NAME_PREFIX = "MyoPod" # Find devices starting with this name
MAX_DEVICES_TO_CONNECT = 2
RUN_DURATION_SECONDS = 15

# --- Notification Handler (Modified) ---
def handle_multi_emg_data(device_address: str, packet: StreamDataPacket):
    """Callback function to handle incoming parsed EMG data packets from multiple devices."""
    # packet is already parsed by MyoPod.start_stream
    # Example: Print block number and first few points, identifying the device
    points_str = ", ".join(f"{p:.2f}" for p in packet.data_points[:3])
    if len(packet.data_points) > 3:
        points_str += "..."
    logger.info(
        f"[{device_address}] Block {packet.block_number}: "
        f"Src={packet.active_stream_source.name}, Comp={packet.compression_type.name}, "
        f"Points=[{points_str}] ({len(packet.data_points)})"
    )


async def connect_and_stream(device_info: Tuple[BLEDevice, ParsedAdvertisingData]) -> None:
    """Manages connection, configuration, and streaming for a single MyoPod."""
    device, parsed_ad = device_info
    log_prefix = f"[{device.address}]"
    logger.info(f"{log_prefix} ({parsed_ad.device_config.chirality.name} Sensor, Batt: {parsed_ad.battery_level}%) Attempting to connect...")

    try:
        async with BleakClient(device) as client:
            if not client.is_connected:
                logger.error(f"{log_prefix} Failed to connect.")
                return

            logger.info(f"{log_prefix} Connected successfully.")
            myopod = MyoPod(client)

            try:
                # Configure stream (e.g., Processed EMG, No Compression)
                # This write also tells the device to START sending
                target_source = EmgStreamSource.PROCESSED_EMG
                target_compression = CompressionType.NONE
                logger.info(f"{log_prefix} Configuring stream to start: {target_source.name}, {target_compression.name}")
                await myopod.configure_stream(target_source, target_compression)
                await asyncio.sleep(0.1) # Short delay

                # Create a handler specific to this device using functools.partial
                # The handler now expects (device_address, packet)
                handler = functools.partial(handle_multi_emg_data, device.address)

                # Subscribe to receive notifications, passing the handler
                logger.info(f"{log_prefix} Subscribing to notifications...")
                await myopod.start_stream(handler)

                # Stream for the duration (or until disconnect)
                start_time = time.monotonic()
                while time.monotonic() - start_time < RUN_DURATION_SECONDS:
                    if not client.is_connected:
                        logger.warning(f"{log_prefix} Device disconnected.")
                        break
                    await asyncio.sleep(0.2)

            except Exception as e:
                logger.error(f"{log_prefix} Error during streaming: {e}")
            finally:
                # Stop sequence: Tell device to stop, then unsubscribe client
                logger.info(f"{log_prefix} Telling device to stop sending...")
                try:
                    await myopod.configure_stream(EmgStreamSource.NONE)
                except Exception as e:
                    logger.error(f"{log_prefix} Error telling device to stop: {e}")

                if myopod.is_subscribed:
                    logger.info(f"{log_prefix} Unsubscribing client...")
                    try:
                        await myopod.stop_stream()
                    except Exception as e:
                        logger.error(f"{log_prefix} Error unsubscribing: {e}")

                logger.info(f"{log_prefix} Disconnecting...")

    except Exception as e:
        logger.error(f"{log_prefix} Connection or setup failed: {e}")


async def main():
    # Store tuples of (BLEDevice, ParsedAdvertisingData)
    myopod_devices_info: Dict[str, Tuple[BLEDevice, ParsedAdvertisingData]] = {}
    logger.info(f"Scanning for up to {MAX_DEVICES_TO_CONNECT} Open Bionics MyoPods (OB2 Sensors)...")

    async with BleakScanner(detection_callback=None) as scanner:
        async for device, ad_data in scanner.advertisement_data():
            parsed_ad = parse_advertisement_data(ad_data)

            if parsed_ad and DeviceType.OB2_SENSOR == parsed_ad.device_config.device_type:
                if device.address not in myopod_devices_info:
                     logger.info(f"Found MyoPod: {device.address} ({device.name}) - "
                                 f"Type: {parsed_ad.device_config.device_type.name}, "
                                 f"Chirality: {parsed_ad.device_config.chirality.name}, "
                                 f"Batt: {parsed_ad.battery_level}%")
                     myopod_devices_info[device.address] = (device, parsed_ad)
                     if len(myopod_devices_info) >= MAX_DEVICES_TO_CONNECT:
                        logger.info(f"Found maximum number ({MAX_DEVICES_TO_CONNECT}) of MyoPods. Stopping scan.")
                        break # Stop scanning once we have enough

    if not myopod_devices_info:
        logger.error("No MyoPod devices found.")
        return

    logger.info(f"Found {len(myopod_devices_info)} MyoPods. Connecting and streaming concurrently...")

    # Create connection and streaming tasks for each device
    # Pass the tuple (device, parsed_ad) to connect_and_stream
    tasks = [connect_and_stream(dev_info) for dev_info in myopod_devices_info.values()]

    # Run tasks concurrently
    await asyncio.gather(*tasks)

    logger.info("Multiple MyoPod streaming example finished.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user.") 