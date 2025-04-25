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
	"""A class to interact with an Open Bionics Hand using a connected BleakClient."""

	def __init__(self, client: BleakClient):
		"""Initialises the Hand instance.

		Args:
			client: The connected BleakClient object for the hand.
		"""
		if not isinstance(client, BleakClient):
			raise TypeError("client must be a BleakClient instance.")
		if not client.is_connected:
			raise ValueError("BleakClient must be connected.")

		self._client = client
		# Store address from client
		self._address = client.address

	@property
	def address(self) -> str:
		"""Returns the MAC address of the hand."""
		return self._address

	async def set_digit_positions(self, positions: dict[int, float]):
		"""Sets the position of the specified digits (0.0 to 1.0).

		Args:
			positions: A dictionary mapping digit ID (0-4) to position (0.0-1.0).
					   Example: {0: 0.5, 1: 0.2} sets thumb and index positions.
		"""
		if not self._client.is_connected:
			logger.error("Cannot send command: Not connected.")
			return

		# Validate input dictionary
		if not isinstance(positions, dict):
			logger.error(f"Invalid positions format. Must be a dictionary. Received: {type(positions)}")
			return
		if not positions: # Check if dict is empty
			logger.warning("set_digit_positions called with empty positions dictionary.")
			return

		# Start payload with the specific "Set Digit Positions" sub-byte (0x01)
		payload = bytearray([0x01])
		num_digits_set = 0
		for digit_id, pos in positions.items():
			if digit_id not in DIGIT_IDS:
				logger.error(f"Invalid digit ID {digit_id} provided. Aborting command.")
				return
			if not isinstance(pos, (float, int)):
				logger.error(f"Invalid position type for digit {digit_id}: {type(pos)}. Aborting command.")
				return

			clamped_pos = max(0.0, min(1.0, float(pos)))
			# Append digit ID (1 byte) and position (4 bytes)
			payload.append(digit_id)
			payload.extend(struct.pack(">f", clamped_pos))
			num_digits_set += 1

		if num_digits_set == 0:
			logger.error("No valid digit positions provided in the dictionary.")
			return

		# Data length is the length of the entire payload (0x01 byte + N * (ID + float))
		data_length = len(payload)
		command = struct.pack(">BBBB", SCHEMA_VERSION, CMD_SET_DIGIT_POSITIONS, 0x01, data_length) + payload

		logger.debug(f"Sending Set Digit Positions command: {command.hex()}")
		try:
			await self._client.write_gatt_char(CONTROL_CHARACTERISTIC_UUID, command, response=False)
			logger.info(f"Sent Set Digit Positions command to {self.address}")
		except BleakError as e:
			logger.error(f"Failed to send command: {e}")
		except Exception as e:
			logger.error(f"An unexpected error occurred while sending command: {e}")

	async def set_grip(self, grip: GripType):
		"""Sets the hand to a predefined grip.

		Args:
			grip: The GripType enum value representing the desired grip.
		"""
		if not self._client.is_connected:
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
			logger.info(f"Sent Set Grip ({grip.name}) command to {self.address}")
		except BleakError as e:
			logger.error(f"Failed to send command: {e}")
		except Exception as e:
			logger.error(f"An unexpected error occurred while sending command: {e}") 