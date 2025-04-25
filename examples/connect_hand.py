"""Example: Discover and connect to an OB2 Hand."""

import asyncio
import logging
import sys
import os

# Add project root to path if running script directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bleak import BleakClient

from myolink import discover_devices, DeviceType, Hand # type: ignore

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
	hand_device = None
	hand_ad_data = None
	logger.info("Scanning for Open Bionics Hands (OB2 Hand)...")

	# Use the core discover_devices, filtering for OB2_HAND
	# Returns Dict[str, Tuple[BLEDevice, ParsedAdvertisingData, int]]
	discovered_hands = await discover_devices(timeout=5.0, device_type=DeviceType.OB2_HAND)

	if not discovered_hands:
		logger.error("No OB2 Hand devices found.")
		return

	# Select the first discovered hand
	# Extract the tuple (device, parsed_ad, rssi) from the dict values
	first_hand_info = list(discovered_hands.values())[0]
	hand_device, hand_ad_data, rssi = first_hand_info # Unpack the tuple

	logger.info(f"Found Hand: {hand_device.address} ({hand_device.name}), RSSI: {rssi}")
	if hand_ad_data:
		 logger.info(f"  Details: Schema={hand_ad_data.schema_version}, "
					 f"Batt={hand_ad_data.battery_level}%, "
					 f"Config={hand_ad_data.device_config}, "
					 f"Specifics={hand_ad_data.device_specific_data}")

	logger.info(f"Connecting to {hand_device.address}...")
	# **Important:** Use the BLEDevice object (hand_device), not the Hand class instance yet
	async with BleakClient(hand_device) as client:
		if not client.is_connected:
			logger.error(f"Failed to connect to {hand_device.address}")
			return

		logger.info("Connected successfully.")
		# You could optionally instantiate the Hand class here if needed
		# hand = Hand(client) # Note: Hand class currently takes BleakClient
		# logger.info(f"Instantiated Hand object for {hand.address}")

		# Keep connection open for a short time
		await asyncio.sleep(2)

		logger.info("Disconnecting...")
		# Disconnect happens automatically when exiting BleakClient context

	logger.info("Connect Hand example finished.")

if __name__ == "__main__":
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		logger.info("Program interrupted by user.")
	except Exception as e:
		logger.error(f"An unexpected error occurred: {e}", exc_info=True) 