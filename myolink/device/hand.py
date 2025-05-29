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
CMD_GET_RELATIVE_HUMIDITY = 0x0A # Get Relative Humidity Command ID

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
		self._humidity_future: Optional[asyncio.Future[float]] = None
		self._notification_registration_lock = asyncio.Lock()
		self._notifications_started = False # To track if start_notify has been called successfully

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

	async def _ensure_notifications_started(self):
		"""Ensures that notifications are started on the control characteristic.
		Uses a lock to prevent multiple concurrent attempts.
		Raises an exception if notification setup fails.
		"""
		if self._notifications_started: # Already started and presumably successful
			return

		async with self._notification_registration_lock:
			# Double-check after acquiring the lock
			if self._notifications_started:
				return
			
			if not self._client.is_connected:
				logger.error(f"[{self.address}] Cannot start notifications: Client not connected.")
				raise BleakError("Client not connected, cannot start notifications.")

			# --- Log properties of the control characteristic for debugging ---
			control_char_found = False
			for service in self._client.services:
				for char in service.characteristics:
					if char.uuid == CONTROL_CHARACTERISTIC_UUID:
						logger.info(f"[{self.address}] Found Control Characteristic {char.uuid}: Handle={char.handle}, Properties={char.properties}")
						control_char_found = True
						if "notify" not in char.properties:
							logger.error(f"[{self.address}] CRITICAL: Control Characteristic {char.uuid} DOES NOT support 'notify' property. Properties: {char.properties}")
							# self._notifications_started = False # Not strictly needed here as we'd raise below
							raise BleakError(f"Control Characteristic {CONTROL_CHARACTERISTIC_UUID} does not support notifications.")
						break
				if control_char_found:
					break
			
			if not control_char_found:
				logger.error(f"[{self.address}] CRITICAL: Control Characteristic {CONTROL_CHARACTERISTIC_UUID} not found on the device.")
				# self._notifications_started = False
				raise BleakError(f"Control Characteristic {CONTROL_CHARACTERISTIC_UUID} not found.")
			# --- End logging properties ---

			try:
				logger.info(f"[{self.address}] Attempting to subscribe to notifications on {CONTROL_CHARACTERISTIC_UUID}")
				await self._client.start_notify(CONTROL_CHARACTERISTIC_UUID, self._control_notification_handler)
				self._notifications_started = True
				logger.info(f"[{self.address}] Successfully subscribed to notifications on {CONTROL_CHARACTERISTIC_UUID}.")
			except Exception as e:
				logger.error(f"[{self.address}] Failed to subscribe to notifications on {CONTROL_CHARACTERISTIC_UUID}: {e}")
				self._notifications_started = False # Ensure it's marked as not started on failure
				raise # Re-raise the exception to signal failure to the caller

	def _control_notification_handler(self, sender_handle: int, data: bytearray):
		"""
		Handles notifications from the CONTROL_CHARACTERISTIC_UUID.
		It tries to parse incoming data as humidity if a request is pending.
		"""
		logger.info(f"[{self.address}] RX NOTIFY RAW (Handle: {sender_handle}, Char: {CONTROL_CHARACTERISTIC_UUID}): {data.hex()} (Length: {len(data)})")

		# Check if there's an active future waiting for humidity data
		active_future = self._humidity_future
		if active_future and not active_future.done():
			# Expected response format for Get Relative Humidity (0x0A):
			# Byte 0: Schema Version
			# Byte 1: Command ID (0x0A)
			# Byte 2: Command Status (0x00 for success)
			# Byte 3: Data Length (0x04)
			# Bytes 4-7: Relative Humidity (float)
			# Total expected length = 8 bytes
			
			if len(data) >= 8: # Minimum length for the full expected response
				schema_ver, cmd_id_resp, status, length = data[0:4]
				
				if cmd_id_resp == CMD_GET_RELATIVE_HUMIDITY:
					logger.info(f"[{self.address}] Notification matches Humidity CMD ID (0x{cmd_id_resp:02X}). Status: 0x{status:02X}, Declared Length: {length}")
					if status == 0x00: # Assuming 0x00 is success
						if length == 0x04:
							try:
								# Unpack the float from bytes 4-7
								humidity_value = struct.unpack(">f", data[4:8])[0] # Big-endian float
								logger.info(f"[{self.address}] Parsed humidity from 8-byte notification (big-endian): {humidity_value:.2f}%")
								active_future.set_result(humidity_value)
							except struct.error as e:
								logger.error(f"[{self.address}] Failed to unpack 4-byte float from humidity response: {e}. Data payload: {data[4:8].hex()}")
								active_future.set_exception(ValueError(f"Invalid humidity data float format: {data[4:8].hex()}"))
							except Exception as e: # Catch any other errors during unpacking/set_result
								logger.error(f"[{self.address}] Error processing humidity data: {e}")
								if not active_future.done(): # Check again as set_exception could be called by another path
									active_future.set_exception(e)
						else:
							logger.warning(f"[{self.address}] Humidity response success status, but unexpected data length: {length}. Expected 4.")
							# active_future.set_exception(ValueError(f"Humidity response unexpected data length {length}"))
					else:
						logger.error(f"[{self.address}] Humidity command 0x{cmd_id_resp:02X} failed with status 0x{status:02X}. Full response: {data.hex()}")
						active_future.set_exception(RuntimeError(f"Humidity command failed with status 0x{status:02X}"))
				# else:
				# This notification is not for CMD_GET_RELATIVE_HUMIDITY, even if a humidity future is pending.
				# Could be a response to a different command that was interleaved, or an unsolicited status.
				# logger.debug(f"[{self.address}] Notification is not a humidity response (CMD ID mismatch). CMD_ID: 0x{cmd_id_resp:02X}")

			elif len(data) == 4: # Previous assumption, now less likely for humidity but good to log if it happens
				logger.warning(f"[{self.address}] Received 4-byte notification while humidity future pending. Data: {data.hex()}. This is not the expected 8-byte format.")
				# If this were a valid but different command's response, we might handle it.
				# For humidity, this is unexpected according to the new format.
				# To avoid stalling the future indefinitely on an unexpected short packet,
				# we might set an exception if no other longer packet arrives.
				# However, the timeout on `wait_for` in `get_relative_humidity` will handle this.

			# else: (len(data) < 4 or other lengths not matching 8 or 4)
				# logger.debug(f"[{self.address}] Notification for active humidity future had unexpected length {len(data)}. Data: {data.hex()}. Expected 8 bytes for humidity response.")
				# The main timeout in get_relative_humidity will handle cases of no valid packet.
		else:
			# No active humidity future, or it's already resolved.
			# This could be an unsolicited notification or a response for a different, future mechanism.
			logger.debug(f"[{self.address}] Received notification, but no pending humidity future or it was already resolved: {data.hex()}")

	async def get_relative_humidity(self, timeout: float = 5.0) -> Optional[float]:
		"""
		Sends a command to the hand to get the relative humidity.

		The response (a 32-bit float) is expected via a notification on the
		CONTROL_CHARACTERISTIC_UUID.

		Args:
			timeout: Time in seconds to wait for the humidity data.

		Returns:
			The relative humidity as a float, or None if an error occurs or times out.
		"""
		if not self._client.is_connected:
			logger.error(f"[{self.address}] Cannot get humidity: Not connected.")
			return None

		try:
			# Ensure notifications are started. This will raise an exception on failure.
			await self._ensure_notifications_started()
		except Exception as e:
			logger.error(f"[{self.address}] Notification setup failed, cannot get humidity: {e}")
			return None # Cannot proceed if notifications aren't working

		# Check if a humidity request is already in progress
		if self._humidity_future and not self._humidity_future.done():
			logger.warning(f"[{self.address}] Humidity request already in progress. Please try again later.")
			# Or raise asyncio.InvalidStateError("Humidity request already in progress")
			return None

		# Create a new Future for this specific request
		current_request_future = asyncio.Future()
		self._humidity_future = current_request_future

		# Command Structure: Schema | Command ID | Control Byte | Data Length | Payload
		# For Get Relative Humidity:
		#   Schema_Version (0x00)
		#   CMD_GET_RELATIVE_HUMIDITY (0x0A)
		#   Control Byte (assumed 0x01, similar to other commands for consistency)
		#   Data Length (0x00 - no payload for this request)
		control_byte = 0x01 # Changed back to 0x01 for testing with longer timeout
		command = struct.pack(">BBBB", SCHEMA_VERSION, CMD_GET_RELATIVE_HUMIDITY, control_byte, 0x00)

		try:
			logger.info(f"[{self.address}] Sending 'Get Relative Humidity' (ID: 0x{CMD_GET_RELATIVE_HUMIDITY:02X}, Control: 0x{control_byte:02X}) command: {command.hex()}")
			await self._client.write_gatt_char(CONTROL_CHARACTERISTIC_UUID, command, response=False)

			# Wait for the notification handler to set the result of current_request_future
			humidity_value = await asyncio.wait_for(current_request_future, timeout=timeout) # Default timeout is now 5.0s from method signature
			return humidity_value
		except asyncio.TimeoutError:
			logger.error(f"[{self.address}] Timeout ({timeout}s) waiting for humidity data notification.")
			if not current_request_future.done():
				current_request_future.set_exception(asyncio.TimeoutError("Timeout waiting for humidity data"))
			return None
		except BleakError as e:
			logger.error(f"[{self.address}] BleakError during Get Relative Humidity command: {e}")
			if not current_request_future.done():
				current_request_future.set_exception(e)
			return None
		except Exception as e:
			logger.error(f"[{self.address}] Unexpected error in get_relative_humidity: {e}", exc_info=True)
			if not current_request_future.done():
				current_request_future.set_exception(e)
			return None
		finally:
			# Clean up: if self._humidity_future is still pointing to the future for *this* call, clear it.
			if self._humidity_future is current_request_future:
				self._humidity_future = None 