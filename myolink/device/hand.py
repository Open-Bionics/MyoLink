"""Represents an Open Bionics Hero Hand (RGD or PRO)."""

import asyncio
import struct
import logging
from typing import List, Optional, Tuple
from enum import Enum

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

# Configure logging
logger = logging.getLogger(__name__)

# Hand Service and Characteristic UUIDs
CONTROL_SERVICE_UUID = "0B0B4000-FEED-DEAD-BEE5-0BE9B1091C50"
CONTROL_CHARACTERISTIC_UUID = "0B0B4102-FEED-DEAD-BEE5-0BE9B1091C50"

# Command IDs
CMD_SET_DIGIT_POSITIONS = 0x06
CMD_SET_GRIP = 0x07

# Digit IDs (assuming standard order)
DIGIT_THUMB = 0x00
DIGIT_INDEX = 0x01
DIGIT_MIDDLE = 0x02
DIGIT_RING = 0x03
DIGIT_PINKY = 0x04

DIGIT_IDS = [DIGIT_THUMB, DIGIT_INDEX, DIGIT_MIDDLE, DIGIT_RING, DIGIT_PINKY]

# Control Schema Version
SCHEMA_VERSION = 0x00

# Grip Types Enum
class GripType(Enum):
	RELAX = 0x00
	POINT = 0x01
	HOOK = 0x02
	PINCH = 0x03
	CYLINDRICAL = 0x04
	TRIPOD = 0x05
	# Add other standard grips as needed

class Hand:
	"""A class to interact with an Open Bionics Hand."""

	def __init__(self, device: BLEDevice):
		"""Initialises the Hand instance.

		Args:
			device: The BLEDevice object representing the hand.
		"""
		self._device = device
		self._client: Optional[BleakClient] = None

	@property
	def address(self) -> str:
		"""Returns the MAC address of the hand."""
		return self._device.address

	@property
	def name(self) -> Optional[str]:
		"""Returns the advertised name of the hand."""
		return self._device.name

	async def connect(self) -> bool:
		"""Connects to the hand."""
		if self._client and self._client.is_connected:
			logger.info(f"Already connected to {self.name} ({self.address})")
			return True

		logger.info(f"Connecting to {self.name} ({self.address})...")
		try:
			self._client = BleakClient(self._device)
			await self._client.connect()
			logger.info(f"Connected successfully to {self.name} ({self.address})")
			return self._client.is_connected
		except BleakError as e:
			logger.error(f"Failed to connect to {self.name} ({self.address}): {e}")
			self._client = None
			return False
		except Exception as e:
			logger.error(f"An unexpected error occurred during connection: {e}")
			self._client = None
			return False

	async def disconnect(self):
		"""Disconnects from the hand."""
		if self._client and self._client.is_connected:
			logger.info(f"Disconnecting from {self.name} ({self.address})...")
			try:
				await self._client.disconnect()
				logger.info(f"Disconnected from {self.name} ({self.address})")
			except BleakError as e:
				logger.error(f"Error during disconnection: {e}")
			except Exception as e:
				logger.error(f"An unexpected error occurred during disconnection: {e}")
		else:
			logger.info(f"Already disconnected from {self.name} ({self.address})")
		self._client = None

	async def set_digit_positions(self, positions: List[float]):
		"""Sets the position of the specified digits (0.0 to 1.0).

		Digits are set starting from the thumb.
		Example: [0.5] sets thumb position.
		Example: [0.5, 0.2] sets thumb and index positions.

		Args:
			positions: A list of 1 to 5 float values (thumb, index, ...),
					   each between 0.0 and 1.0.
		"""
		if not (self._client and self._client.is_connected):
			logger.error("Cannot send command: Not connected.")
			return

		# Validate input list length
		if not (isinstance(positions, list) and 1 <= len(positions) <= 5):
			logger.error(f"Invalid positions format. Must be a list of 1 to 5 floats. Received: {positions}")
			return

		# Validate and clamp individual positions
		clamped_positions = []
		for i, pos in enumerate(positions):
			if not isinstance(pos, (float, int)):
				logger.error(f"Invalid position type at index {i}: {type(pos)}. Must be float or int.")
				return
			clamped_pos = max(0.0, min(1.0, float(pos)))
			clamped_positions.append(clamped_pos)

		# Prepare payload only for the provided digits
		payload = bytearray()
		num_digits_to_set = len(clamped_positions)
		for i in range(num_digits_to_set):
			digit_id = DIGIT_IDS[i] # Get corresponding digit ID
			pos = clamped_positions[i]
			payload.append(digit_id)
			# Pack float as big-endian 32-bit float (>f)
			payload.extend(struct.pack(">f", pos))

		data_length = len(payload)

		# Construct final command
		# Schema | Command ID | Is Request | Data Length | Payload
		command = struct.pack(">BBBB", SCHEMA_VERSION, CMD_SET_DIGIT_POSITIONS, 0x01, data_length) + payload

		logger.debug(f"Sending Set Digit Positions command: {command.hex()}")
		try:
			await self._client.write_gatt_char(CONTROL_CHARACTERISTIC_UUID, command, response=False)
			logger.info(f"Sent Set Digit Positions command to {self.name} ({self.address})")
		except BleakError as e:
			logger.error(f"Failed to send command: {e}")
		except Exception as e:
			logger.error(f"An unexpected error occurred while sending command: {e}")

	async def set_grip(self, grip: GripType):
		"""Sets the hand to a predefined grip.

		Args:
			grip: The GripType enum value representing the desired grip.
		"""
		if not (self._client and self._client.is_connected):
			logger.error("Cannot send command: Not connected.")
			return

		if not isinstance(grip, GripType):
			logger.error(f"Invalid grip type: {grip}. Must be a GripType enum member.")
			return

		# Payload for Set Grip (Schema 0) is just the grip ID byte
		grip_id = grip.value
		payload = bytes([grip_id])
		data_length = len(payload)

		# Construct final command
		# Schema | Command ID | Is Request | Data Length | Payload
		command = struct.pack(">BBBB", SCHEMA_VERSION, CMD_SET_GRIP, 0x01, data_length) + payload

		logger.debug(f"Sending Set Grip command: {command.hex()}")
		try:
			await self._client.write_gatt_char(CONTROL_CHARACTERISTIC_UUID, command, response=False)
			logger.info(f"Sent Set Grip ({grip.name}) command to {self.name} ({self.address})")
		except BleakError as e:
			logger.error(f"Failed to send command: {e}")
		except Exception as e:
			logger.error(f"An unexpected error occurred while sending command: {e}")

	# --- Async Context Manager --- #
	async def __aenter__(self):
		"""Enters the asynchronous context manager, connecting to the hand."""
		await self.connect()
		return self

	async def __aexit__(self, exc_type, exc_val, exc_tb):
		"""Exits the asynchronous context manager, disconnecting from the hand."""
		await self.disconnect() 