"""Core BLE discovery and connection logic."""

import asyncio
import logging
from typing import List, Optional

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Open Bionics Company ID
OPEN_BIONICS_COMPANY_ID = 0x0ABA

async def discover_devices(timeout: float = 5.0) -> List[BLEDevice]:
	"""Scans for BLE devices advertising the Open Bionics Company ID."""
	logger.info(f"Scanning for Open Bionics devices (ID: {OPEN_BIONICS_COMPANY_ID:#06x}) for {timeout} seconds...")
	devices_found: List[BLEDevice] = []

	def detection_callback(device: BLEDevice, advertisement_data: AdvertisementData):
		# Using manufacturer_data. A device might advertise multiple company IDs
		if OPEN_BIONICS_COMPANY_ID in advertisement_data.manufacturer_data:
			logger.info(f"Found Open Bionics device: {device.name} ({device.address})")
			# Add only if not already discovered to avoid duplicates during continuous scanning
			if device not in devices_found:
				devices_found.append(device)

	scanner = None # Initialise scanner to None
	try:
		scanner = BleakScanner(detection_callback=detection_callback)
		await scanner.start()
		await asyncio.sleep(timeout) # Scan for the specified duration
		# Stop is now reliably called in finally
	except Exception as e:
		logger.error(f"An error occurred during scanning: {e}")
	finally:
		# Ensure scanner stops even if errors occur during sleep or start
		if scanner is not None:
			try:
				await scanner.stop()
			except Exception as stop_e:
				# Log error during stop, but don't crash the discovery
				logger.error(f"Error trying to stop scanner: {stop_e}")

	logger.info(f"Scan complete. Found {len(devices_found)} device(s).")
	return devices_found

# Future additions:
# - Base Device class
# - Connection logic
# - Error handling classes 