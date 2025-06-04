import asyncio
import logging
import time
import sys # Added for sys.path modification
import os # Added for sys.path modification

# Add project root to path if running script directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bleak import BleakScanner, BleakClient

# Import discovery elements
from myolink.discovery import parse_advertisement_data, DeviceType
from myolink.device.hand import Hand, GripType

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TARGET_DEVICE_NAME = "Hero" # Name matching can be less reliable

async def main():
    hand_device = None
    hand_ad_data = None
    logger.info("Scanning for Open Bionics Hands (OB2 Hand)...")

    async with BleakScanner(detection_callback=None) as scanner:
        async for device, ad_data in scanner.advertisement_data():
            parsed_ad = parse_advertisement_data(ad_data)

            if parsed_ad:
                # Log discovered OB devices
                dev_type = parsed_ad.device_config.device_type
                chirality = parsed_ad.device_config.chirality
                batt = parsed_ad.battery_level
                logger.debug(f"Found OB Device: {device.address} ({device.name}) - "
                             f"Type: {dev_type.name}, Chirality: {chirality.name}, Batt: {batt}%")

                # Check if it's an OB2 Hand
                if DeviceType.OB2_HAND == dev_type:
                    logger.info(f"Found OB2 Hand: {device.address} ({device.name}) - Batt: {batt}%")
                    # Add more specific filtering if needed (e.g., based on chirality)
                    hand_device = device
                    hand_ad_data = parsed_ad
                    break # Stop scanning once a hand is found

    if hand_device is None:
        logger.error("No suitable OB2 Hand found.")
        return

    # Log more details about the selected hand
    if hand_ad_data:
         logger.info(f"Selected Hand Details: Schema={hand_ad_data.schema_version}, "
                     f"Config={hand_ad_data.device_config}, "
                     f"Specifics={hand_ad_data.device_specific_data}")

    logger.info(f"Connecting to {hand_device.address}...")
    async with BleakClient(hand_device) as client:
        if not client.is_connected:
            logger.error(f"Failed to connect to {hand_device.address}")
            return

        logger.info("Connected successfully.")
        hand = Hand(client)

        try:
            logger.info("Setting position for Thumb and Index finger...")
            await hand.set_digit_positions({0: 1.0, 1: 0.8}) # Thumb rotation=100% (1.0), Index flexion=80% (0.8)
            await asyncio.sleep(2)

            logger.info("Executing Point grip...")
            await hand.set_grip(GripType.POINT)
            await asyncio.sleep(3)

            logger.info("Setting all digits to 20%...")
            await hand.set_digit_positions({0: 0.2, 1: 0.2, 2: 0.2, 3: 0.2, 4: 0.2}) # All digits to 0.2 (20%)
            await asyncio.sleep(2)

            logger.info("Executing Relax grip...")
            await hand.set_grip(GripType.RELAX)
            await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"An error occurred during hand control: {e}")
        finally:
            logger.info("Disconnecting...")
            # Disconnect happens automatically when exiting BleakClient context

    logger.info("Hand control example finished.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user.") 