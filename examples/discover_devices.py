"""Example: Discover MyoLink devices."""

import asyncio
import sys

# Add project root to path if running script directly
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from myolink import discover_devices # type: ignore

async def main():
	print("Discovering MyoLink devices...")
	discovered = await discover_devices(timeout=10.0)

	if 0 == len(discovered):
		print("No MyoLink devices found.")
	else:
		print("\nFound devices:")
		for i, device in enumerate(discovered):
			print(f"  {i + 1}. {device.name} ({device.address})")

if __name__ == "__main__":
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		print("\nScan stopped by user.")
	except Exception as e:
		print(f"An error occurred: {e}") 