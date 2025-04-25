"""Example: Connect to a Hand and control individual digit positions."""

import asyncio
import sys
import logging

# Add project root to path if running script directly
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from myolink import discover_devices, Hand # type: ignore
from bleak.backends.device import BLEDevice

# Configure basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def select_device(prompt: str = "Select a device:") -> BLEDevice:
	"""Scans for devices and prompts the user to select one."""
	print("Scanning for devices...")
	discovered_devices = await discover_devices(timeout=5.0)

	if 0 == len(discovered_devices):
		print("No devices found. Make sure your device is powered on and advertising.")
		sys.exit(1)

	print("\nDiscovered devices:")
	for i, device in enumerate(discovered_devices):
		print(f"  {i + 1}. {device.name} ({device.address})")

	print("\nNote: Ensure the selected device is paired with your OS for a stable connection.")

	while True:
		try:
			selection = int(input(f"\n{prompt} (Enter number): "))
			if 1 <= selection <= len(discovered_devices):
				return discovered_devices[selection - 1]
			else:
				print("Invalid selection. Please try again.")
		except ValueError:
			print("Invalid input. Please enter a number.")
		except KeyboardInterrupt:
			print("\nOperation cancelled by user.")
			sys.exit(0)

async def main():
	try:
		selected_ble_device = await select_device("Select the Hand to control:")
		hand = Hand(selected_ble_device)

		print(f"\nConnecting to {hand.name} ({hand.address})...")
		async with hand:
			if not (hand._client and hand._client.is_connected):
				print(f"Failed to connect to {hand.name}. Exiting.")
				return

			print(f"Successfully connected to {hand.name}.")
			print("Enter 5 digit positions (thumb, index, middle, ring, pinky) as floats between 0.0 and 1.0, separated by commas or spaces.")
			print("Examples: 0.5, 0.0, 1.0, 0.2, 0.8  OR  0.5 0.0 1.0 0.2 0.8")
			print("Type 'q' or press Ctrl+C to quit.")

			while True:
				try:
					user_input = input("\nEnter positions (or 'q' to quit): ").strip().lower()
					if 'q' == user_input:
						break

					# Replace commas with spaces, then split by whitespace
					parts = user_input.replace(',', ' ').split()

					# Allow 1 to 5 values
					if not (1 <= len(parts) <= 5):
						print("Invalid input: Please provide 1 to 5 space or comma-separated values.")
						continue

					try:
						positions = [float(p) for p in parts]
						# Basic validation (more detailed validation is in the Hand class)
						if any(p < 0.0 or p > 1.0 for p in positions):
							print("Invalid input: Positions must be between 0.0 and 1.0.")
							continue

						await hand.set_digit_positions(positions)
						print(f"Sent positions: {positions}")

					except ValueError:
						print("Invalid input: Please ensure all values are valid numbers.")
						continue

				except asyncio.CancelledError:
					# Likely caused by KeyboardInterrupt during an await operation within the loop
					logger.info("Operation cancelled, proceeding to disconnect.")
				except KeyboardInterrupt:
					print("\nOperation cancelled by user.")
					break
				except Exception as e:
					logger.error(f"An error occurred while sending command: {e}")
					# Allow loop to continue or break depending on severity? For now, continue.
					continue

	except asyncio.CancelledError:
		# Likely caused by KeyboardInterrupt during an await operation within the loop
		logger.info("Operation cancelled, proceeding to disconnect.")
	except KeyboardInterrupt:
		print("\nOperation cancelled by user.")
	except Exception as e:
		logger.error(f"An unexpected error occurred: {e}")
	finally:
		print("Exiting application.")

if __name__ == "__main__":
	asyncio.run(main()) 