"""Example: Discover and connect to a MyoLink Hand."""

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
		selected_ble_device = await select_device("Select the Hand you want to connect to:")
		hand = Hand(selected_ble_device)

		print(f"\nAttempting to connect to {hand.name} ({hand.address})...")
		# Use async with for automatic connection/disconnection
		async with hand:
			# Check connection status after attempting
			if hand._client and hand._client.is_connected: # Accessing protected member for status check in example
				print(f"Successfully connected to {hand.name}. Press Ctrl+C to disconnect.")
				# Keep connection alive until user interrupts
				await asyncio.sleep(3600) # Keep alive for an hour or until Ctrl+C
			else:
				print(f"Failed to connect to {hand.name}.")

	except asyncio.CancelledError:
		# This occurs when asyncio.sleep is cancelled, typically by KeyboardInterrupt
		logger.info("Sleep cancelled, proceeding to disconnect.")
	except KeyboardInterrupt:
		print("\nDisconnecting due to user interruption...")
	except Exception as e:
		logger.error(f"An unexpected error occurred: {e}")
	finally:
		print("Connection closed.")

if __name__ == "__main__":
	asyncio.run(main()) 