"""Core BLE discovery and connection logic."""

import asyncio
import logging
from typing import List, Dict, Optional, Tuple

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# Import necessary elements from discovery
from .discovery import parse_advertisement_data, ParsedAdvertisingData, DeviceType, OPEN_BIONICS_COMPANY_ID

# Configure logging
# logging.basicConfig(level=logging.INFO) # Configuration should ideally happen in application code
logger = logging.getLogger(__name__)

async def discover_devices(
	timeout: float = 5.0,
	device_type: Optional[DeviceType] = None
) -> Dict[str, Tuple[BLEDevice, ParsedAdvertisingData, int]]:
	"""Scans for BLE devices advertising Open Bionics manufacturer data.

	Args:
		timeout: Scanning duration in seconds.
		device_type: Optional filter to return only devices of a specific type (e.g., DeviceType.OB2_HAND).

	Returns:
		A dictionary mapping device addresses (str) to tuples containing the
		BLEDevice object, the ParsedAdvertisingData object, and the RSSI (int).
	"""
	devices_found: Dict[str, Tuple[BLEDevice, ParsedAdvertisingData, int]] = {}
	logger.info(f"Starting scan for Open Bionics devices (timeout={timeout}s)...")

	# Define an inner callback for detection
	def detection_callback(device: BLEDevice, advertisement_data: AdvertisementData):
		# Check if it's an Open Bionics device first by Company ID in manufacturer data
		parsed_ad = parse_advertisement_data(advertisement_data)

		if parsed_ad:
			rssi = advertisement_data.rssi # Get RSSI from AdvertisementData
			# Log detection
			logger.debug(f"Detected OB Device: {device.address} ({device.name}) - "
						 f"Type: {parsed_ad.device_config.device_type.name}, "
						 f"Batt: {parsed_ad.battery_level}%, RSSI: {rssi}"
			)
			# Apply optional device type filter
			if device_type is None or parsed_ad.device_config.device_type == device_type:
				if device.address not in devices_found:
					logger.info(f"Found matching device: {device.address} ({device.name}) - Type: {parsed_ad.device_config.device_type.name}")
					# Store device, parsed data, and RSSI
					devices_found[device.address] = (device, parsed_ad, rssi)
			else:
				 logger.debug(f"Device {device.address} is type {parsed_ad.device_config.device_type.name}, but filtering for {device_type.name}. Skipping.")
		# else: Not an OB device or parsing failed (logged in parse_advertisement_data)

	# Scan using the callback
	scanner = None # Initialise scanner to None
	try:
		scanner = BleakScanner(detection_callback=detection_callback)
		await scanner.start()
		await asyncio.sleep(timeout)
		# Stop after timeout, check scanner exists
		if scanner:
			await scanner.stop()
	except Exception as e:
		logger.error(f"Error during BLE scan: {e}")
		# Attempt to stop scanner if error occurred and scanner exists
		if scanner:
			try:
				await scanner.stop()
			except Exception:
				pass # Ignore errors during cleanup stop
	finally:
		# Final check to ensure stop is called if scanner was created
		if scanner:
			try:
				# Check scanner state before stopping? Bleak docs are unclear if needed.
				# For now, just attempt stop again if scanner exists.
				await scanner.stop()
			except Exception as e:
				# Avoid logging duplicate errors if stop already failed in try/except blocks
				# logger.error(f"Error stopping scanner in finally block: {e}")
				pass


	logger.info(f"Scan finished. Found {len(devices_found)} matching Open Bionics devices.")
	return devices_found

# Future additions:
# - Base Device class
# - Connection logic
# - Error handling classes 