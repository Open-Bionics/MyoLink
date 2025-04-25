"""Example: Discover MyoLink devices."""

import asyncio
import sys
import logging # Add logging

# Add project root to path if running script directly
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from myolink import discover_devices, DeviceType, ParsedAdvertisingData, Chirality # type: ignore
from bleak.backends.device import BLEDevice # Added
from typing import Tuple # Added

# Configure basic logging for the example
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
	logger.info("Discovering all Open Bionics devices (Hands and MyoPods)...")
	# Returns Dict[str, Tuple[BLEDevice, ParsedAdvertisingData]]
	discovered_devices = await discover_devices(timeout=10.0)

	if not discovered_devices:
		logger.info("No Open Bionics devices found.")
	else:
		logger.info(f"\nFound {len(discovered_devices)} Open Bionics device(s):")
		i = 1
		# Iterate through the dictionary values (the tuples)
		# Now includes RSSI in the tuple
		for address, (device, parsed_ad, rssi) in discovered_devices.items():
			print(f"\n--- Device {i} ---")
			print(f"  Address: {address}")
			print(f"  Name: {device.name}")
			print(f"  RSSI: {rssi}") # Use RSSI from the tuple
			if parsed_ad:
				print(f"  Device Type: {parsed_ad.device_config.device_type.name}")
				print(f"  Battery: {parsed_ad.battery_level}%")
				# Conditional Chirality Label
				if parsed_ad.device_config.device_type == DeviceType.OB2_HAND:
					chirality_str = "RIGHT" if parsed_ad.device_config.chirality == Chirality.RIGHT_OR_CLOSE else "LEFT"
					print(f"  Chirality: {chirality_str}")
				elif parsed_ad.device_config.device_type == DeviceType.OB2_SENSOR:
					chirality_str = "CLOSE" if parsed_ad.device_config.chirality == Chirality.RIGHT_OR_CLOSE else "OPEN"
					print(f"  Chirality: {chirality_str}")
				else:
					# Fallback for other types (e.g., HERO_ARM)
					print(f"  Chirality Raw: {parsed_ad.device_config.chirality.name}")

				print(f"  Adv Schema: {parsed_ad.schema_version}")
				# Optionally print more details based on type
				if parsed_ad.device_config.device_type == DeviceType.OB2_HAND:
					print(f"  Hand Specifics: {parsed_ad.device_specific_data}")
				elif parsed_ad.device_config.device_type == DeviceType.OB2_SENSOR:
					 print(f"  Sensor Specifics: {parsed_ad.device_specific_data}")
			else:
				# Should not happen if discover_devices works correctly, but good practice
				print("  (Could not parse Open Bionics advertising data)")
			i += 1

if __name__ == "__main__":
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		logger.info("\nScan stopped by user.")
	except Exception as e:
		logger.error(f"An error occurred: {e}", exc_info=True) # Log traceback 