"""Example: Discover and control OB2 Hand digits."""

import asyncio
import logging
import sys
import os
import time # Added

# Add project root to path if running script directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bleak import BleakClient

from myolink import discover_devices, DeviceType, Hand, GripType # type: ignore

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
	hand_device = None
	hand_ad_data = None
	logger.info("Scanning for Open Bionics Hands (OB2 Hand)...")

	# Use the core discover_devices, filtering for OB2_HAND
	discovered_hands = await discover_devices(timeout=5.0, device_type=DeviceType.OB2_HAND)

	if not discovered_hands:
		logger.error("No OB2 Hand devices found.")
		return

	# Select the first discovered hand
	first_hand_info = list(discovered_hands.values())[0]
	hand_device, hand_ad_data, rssi = first_hand_info

	logger.info(f"Found Hand: {hand_device.address} ({hand_device.name}), RSSI: {rssi}")
	if hand_ad_data:
		 logger.info(f"  Details: Schema={hand_ad_data.schema_version}, "
					 f"Batt={hand_ad_data.battery_level}%, "
					 f"Config={hand_ad_data.device_config}, "
					 f"Specifics={hand_ad_data.device_specific_data}")

	logger.info(f"Connecting to {hand_device.address}...")
	async with BleakClient(hand_device) as client:
		if not client.is_connected:
			logger.error(f"Failed to connect to {hand_device.address}")
			return

		logger.info("Connected successfully.")
		# Instantiate Hand class with the connected client
		hand = Hand(client)

		try:
			logger.info("--- Interactive Digit Control ---")
			logger.info("Enter 1 to 5 digit positions (thumb, index, middle, ring, pinky)")
			logger.info("as floats between 0.0 and 1.0, separated by commas or spaces.")
			logger.info("Examples: 0.5, 0.2, 0.1  OR  0.8 1.0")
			logger.info("Type 'q' or press Ctrl+C to quit.")

			while True:
				try:
					user_input = input("\nEnter positions (or 'q' to quit): ").strip().lower()
					if 'q' == user_input:
						break

					# Replace commas with spaces, then split
					parts = user_input.replace(',', ' ').split()

					# Allow 1 to 5 values
					if not (1 <= len(parts) <= 5):
						print("Invalid input: Please provide 1 to 5 space or comma-separated values.")
						continue

					try:
						positions_list = [float(p) for p in parts]

						# Convert list to dictionary {digit_id: position}
						positions_dict = {i: pos for i, pos in enumerate(positions_list)}

						await hand.set_digit_positions(positions_dict)
						logger.info(f"Sent positions: {positions_dict}")

					except ValueError:
						print("Invalid input: Please ensure all values are valid numbers between 0.0 and 1.0.")
						continue
					except Exception as cmd_e:
						logger.error(f"Error sending command: {cmd_e}")
						continue # Allow user to try again

				except KeyboardInterrupt:
					logger.info("\nLoop interrupted by user.")
					break

		except Exception as e:
			logger.error(f"An error occurred during hand control: {e}", exc_info=True)
		finally:
			logger.info("Disconnecting...")
			# Disconnect happens automatically when exiting BleakClient context

	logger.info("Hand control example finished.")

if __name__ == "__main__":
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		logger.info("Program interrupted by user.")
	except Exception as e:
		logger.error(f"An unexpected error occurred: {e}", exc_info=True) 