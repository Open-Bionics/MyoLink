"""Represents an Open Bionics Hero Hand (RGD or PRO)."""

import asyncio
import struct
import logging
from typing import List, Optional, Tuple, Any, Dict
from enum import Enum

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

# Configure logging
logger = logging.getLogger(__name__)

# --- Enums and Exceptions specific to Hand Control ---
class ResponseStatus(Enum):
	"""Status codes for command responses."""
	SUCCESS = 0x00
	ERR_INVALID_CMD = 0x01
	ERR_INVALID_PARAM = 0x02
	ERR_INVALID_DATA_LEN = 0x03
	ERR_INVALID_DATA = 0x04
	ERR_INTERNAL = 0x05
	# Add other specific error codes as they are discovered/documented

class HandCommandError(BleakError):
	"""Custom exception for hand command failures."""
	def __init__(self, message: str, status: Optional[ResponseStatus] = None, raw_response: Optional[bytes] = None):
		super().__init__(message)
		self.status = status
		self.raw_response = raw_response

	def __str__(self):
		base_str = super().__str__()
		if self.status:
			base_str += f" (Status: {self.status.name})"
		if self.raw_response:
			base_str += f" (Raw: {self.raw_response.hex()})"
		return base_str

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
		# self._humidity_future: Optional[asyncio.Future[float]] = None # Replaced by _pending_command_futures
		self._notification_registration_lock = asyncio.Lock()
		self._notifications_started = False # To track if start_notify has been called successfully
		# For a more generic command/response system:
		self._pending_command_futures: Dict[int, asyncio.Future] = {} # Key: Command ID

	@property
	def address(self) -> str:
		"""Returns the MAC address of the hand."""
		return self._address

	async def set_digit_positions(self, positions: dict[int, float]):
		"""Sets the position of the specified digits (0.0 to 1.0).
		This is treated as a fire-and-forget command; no response is awaited.
		"""
		if not self._client.is_connected:
			logger.error(f"[{self.address}] Cannot send Set Digit Positions: Not connected.")
			return # Or raise error

		if not isinstance(positions, dict) or not positions:
			logger.error(f"[{self.address}] Invalid/empty positions for Set Digit Positions: {positions}")
			return # Or raise error
		
		payload_data = bytearray([0x01]) # Specific sub-byte for CMD_SET_DIGIT_POSITIONS command type
		num_digits_set = 0
		for digit_id, pos_val in positions.items():
			if digit_id not in DIGIT_IDS:
				logger.error(f"[{self.address}] Invalid digit ID {digit_id}. Aborting Set Digit Positions.")
				return # Or raise
			if not isinstance(pos_val, (float, int)):
				logger.error(f"[{self.address}] Invalid position type for digit {digit_id}: {type(pos_val)}. Aborting.")
				return # Or raise

			clamped_pos = max(0.0, min(1.0, float(pos_val)))
			payload_data.append(digit_id)
			payload_data.extend(struct.pack(">f", clamped_pos)) # Big-endian float for position
			num_digits_set += 1

		if num_digits_set == 0:
			logger.error(f"[{self.address}] No valid digit positions provided.")
			return # Or raise

		# Command Structure: Schema | Command ID | Control Byte (IsRequest=1) | Data Length | Payload
		control_byte_request = 0x01 # IsRequest = 1
		data_length = len(payload_data)
		command_packet = struct.pack(">BBBB", SCHEMA_VERSION, CMD_SET_DIGIT_POSITIONS, control_byte_request, data_length) + payload_data

		try:
			logger.info(f"[{self.address}] Sending Set Digit Positions (CMD 0x{CMD_SET_DIGIT_POSITIONS:02X}, Fire-and-forget): {command_packet.hex()}")
			await self._client.write_gatt_char(CONTROL_CHARACTERISTIC_UUID, command_packet, response=False)
			logger.debug(f"[{self.address}] Set Digit Positions command sent.")
		except BleakError as e:
			logger.error(f"[{self.address}] BleakError during Set Digit Positions: {e}")
			# Optionally re-raise or handle as appropriate for a fire-and-forget failure
		except Exception as e:
			logger.error(f"[{self.address}] Unexpected error during Set Digit Positions: {e}", exc_info=True)

	async def set_grip(self, grip: GripType):
		"""Sets the hand to a predefined grip.
		This is treated as a fire-and-forget command; no response is awaited.
		"""
		if not self._client.is_connected:
			logger.error(f"[{self.address}] Cannot send Set Grip: Not connected.")
			return # Or raise

		if not isinstance(grip, GripType):
			logger.error(f"[{self.address}] Invalid grip type: {grip}. Must be a GripType enum member.")
			return # Or raise

		grip_id_byte = grip.value
		request_payload = bytes([grip_id_byte])

		# Command Structure: Schema | Command ID | Control Byte (IsRequest=1) | Data Length | Payload
		control_byte_request = 0x01 # IsRequest = 1
		data_length = len(request_payload)
		command_packet = struct.pack(">BBBB", SCHEMA_VERSION, CMD_SET_GRIP, control_byte_request, data_length) + request_payload

		try:
			logger.info(f"[{self.address}] Sending Set Grip (CMD 0x{CMD_SET_GRIP:02X}, Fire-and-forget): {command_packet.hex()}")
			await self._client.write_gatt_char(CONTROL_CHARACTERISTIC_UUID, command_packet, response=False)
			logger.debug(f"[{self.address}] Set Grip ({grip.name}) command sent.")
		except BleakError as e:
			logger.error(f"[{self.address}] BleakError during Set Grip: {e}")
		except Exception as e:
			logger.error(f"[{self.address}] Unexpected error during Set Grip: {e}", exc_info=True)

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
			# Ensure case-insensitive comparison for UUIDs
			target_char_uuid_lower = CONTROL_CHARACTERISTIC_UUID.lower()

			for service in self._client.services:
				for char in service.characteristics:
					if char.uuid.lower() == target_char_uuid_lower: # Case-insensitive match
						logger.info(f"[{self.address}] Found Control Characteristic {char.uuid}: Handle={char.handle}, Properties={char.properties}")
						control_char_found = True
						if "notify" not in char.properties:
							logger.error(f"[{self.address}] CRITICAL: Control Characteristic {char.uuid} DOES NOT support 'notify' property. Properties: {char.properties}")
							raise BleakError(f"Control Characteristic {CONTROL_CHARACTERISTIC_UUID} does not support notifications.")
						break
				if control_char_found:
					break
			
			if not control_char_found:
				logger.error(f"[{self.address}] CRITICAL: Control Characteristic {CONTROL_CHARACTERISTIC_UUID} (target: {target_char_uuid_lower}) not found on the device.")
				logger.info(f"[{self.address}] Listing all discovered services and characteristics for debugging:")
				for service_obj in self._client.services: 
					logger.info(f"[{self.address}]   Service: {service_obj.uuid} ({service_obj.description})")
					for char_obj in service_obj.characteristics: 
						logger.info(f"[{self.address}]     Characteristic: {char_obj.uuid} ({char_obj.description}), Properties: {char_obj.properties}, Handle: {char_obj.handle}")
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
		It tries to parse incoming data based on pending command futures.
		"""
		logger.info(f"[{self.address}] RX NOTIFY RAW (H: {sender_handle}, C: {CONTROL_CHARACTERISTIC_UUID.split('-')[0]}...): {data.hex()} (Len: {len(data)})")

		if len(data) < 4: # Minimum length for Schema, CMD_ID, Status, Data_Length
			logger.warning(f"[{self.address}] Notification too short to parse header (Len: {len(data)}). Data: {data.hex()}")
			return

		schema_ver, cmd_id_resp, status_byte, data_len_resp = data[0:4]
		response_payload = data[4:] # This will be empty if data_len_resp is 0 and len(data) is 4

		# Validate actual payload length against declared data_len_resp
		if len(response_payload) != data_len_resp:
			logger.warning(f"[{self.address}] Payload length mismatch for CMD 0x{cmd_id_resp:02X}. Declared: {data_len_resp}, Actual: {len(response_payload)}. Full data: {data.hex()}")
			# We might still proceed if the future handler for this cmd_id knows how to deal with it,
			# or we could early exit / set a generic error on a future if one is found.

		try:
			# Bit 7 is IsRequest (0 for response), Bits 0-2 are Response Status
			is_response_type = not (status_byte & 0x80) # 0 if response, 1 if request
			raw_status_bits = status_byte & 0x07 # Extract bits 0-2
			response_status = ResponseStatus(raw_status_bits)
		except ValueError:
			logger.error(f"[{self.address}] Unknown ResponseStatus value {raw_status_bits} from status byte 0x{status_byte:02X} for CMD 0x{cmd_id_resp:02X}.")
			# Find a future for this cmd_id_resp and set an error, because we can't proceed with an unknown status.
			unknown_status_future = self._pending_command_futures.get(cmd_id_resp)
			if unknown_status_future and not unknown_status_future.done():
				unknown_status_future.set_exception(HandCommandError(f"Unknown response status {raw_status_bits}", status=None, raw_response=data))
			return

		if not is_response_type:
			logger.warning(f"[{self.address}] Received notification that is marked as a 'request' (not a response) for CMD 0x{cmd_id_resp:02X}. Status byte: 0x{status_byte:02X}. Ignoring.")
			return

		logger.debug(f"[{self.address}] Parsed Notification: CMD_ID=0x{cmd_id_resp:02X}, StatusByte=0x{status_byte:02X} (IsResp={is_response_type}, Status={response_status.name}), DeclaredLen={data_len_resp}, PayloadLen={len(response_payload)}")

		# --- Process based on Command ID --- 
		future_for_cmd = self._pending_command_futures.get(cmd_id_resp)

		if not future_for_cmd or future_for_cmd.done():
			logger.warning(f"[{self.address}] Received response for CMD 0x{cmd_id_resp:02X}, but no pending/active future found or future already done. Status: {response_status.name}. Data: {data.hex()}")
			return

		# At this point, we have an active future for this cmd_id_resp
		if cmd_id_resp == CMD_GET_RELATIVE_HUMIDITY:
			if response_status == ResponseStatus.SUCCESS:
				if data_len_resp == 4 and len(response_payload) >= 4:
					try:
						humidity_value = struct.unpack(">f", response_payload[:4])[0]
						logger.info(f"[{self.address}] Parsed humidity: {humidity_value:.2f}% for CMD 0x{cmd_id_resp:02X}")
						future_for_cmd.set_result(humidity_value)
					except struct.error as e:
						logger.error(f"[{self.address}] Failed to unpack float for CMD 0x{cmd_id_resp:02X}: {e}. Payload: {response_payload[:4].hex()}")
						future_for_cmd.set_exception(HandCommandError(f"Invalid float format for humidity: {response_payload[:4].hex()}", status=response_status, raw_response=data))
				else:
					logger.warning(f"[{self.address}] Humidity CMD 0x{cmd_id_resp:02X} success, but data length mismatch. Declared: {data_len_resp}, Payload: {len(response_payload)}. Expected 4 data bytes.")
					future_for_cmd.set_exception(HandCommandError(f"Humidity success but data length mismatch (declared {data_len_resp}, payload {len(response_payload)}, expected 4)", status=response_status, raw_response=data))
			else: # Error status for humidity command
				logger.error(f"[{self.address}] Humidity CMD 0x{cmd_id_resp:02X} failed with status {response_status.name} (StatusByte: 0x{status_byte:02X}).")
				future_for_cmd.set_exception(HandCommandError(f"Humidity command failed: {response_status.name}", status=response_status, raw_response=data))
		
		# --- Generalized handling for other command responses ---
		else: # For commands other than CMD_GET_RELATIVE_HUMIDITY
			if response_status == ResponseStatus.SUCCESS:
				# For commands that only return status, payload might be empty (data_len_resp == 0)
				# Or they might return some data. The future's result type will depend on the command.
				# We've already logged a warning if data_len_resp != len(response_payload)
				logger.info(f"[{self.address}] Command 0x{cmd_id_resp:02X} successful. Status: {response_status.name}. Payload: {response_payload.hex()}")
				future_for_cmd.set_result(response_payload if data_len_resp > 0 else True)
			else: # Error status for this other command
				logger.error(f"[{self.address}] Command 0x{cmd_id_resp:02X} failed with status {response_status.name} (StatusByte: 0x{status_byte:02X}).")
				future_for_cmd.set_exception(HandCommandError(f"Command 0x{cmd_id_resp:02X} failed: {response_status.name}", status=response_status, raw_response=data))

	async def _send_command_and_process_response(
		self, 
		command_id: int,
		request_payload: bytes,
		# expected_response_cmd_id: int, # Usually same as command_id for direct responses
		# expected_response_data_len: Optional[int] = 0, # None if variable, 0 if only status
		timeout: float = 5.0
	) -> Any: # Return type depends on command, could be bool, bytes, float, etc.
		"""
		Internal helper to send a command and await its specific response notification.
		The _control_notification_handler is responsible for parsing the header, 
		checking status, and resolving the future associated with this command_id.
		"""
		if not self._client.is_connected:
			logger.error(f"[{self.address}] Cannot send CMD 0x{command_id:02X}: Not connected.")
			raise BleakError("Client not connected.")

		await self._ensure_notifications_started() # Raises on failure

		if self._pending_command_futures.get(command_id) and \
		   not self._pending_command_futures[command_id].done():
			logger.warning(f"[{self.address}] Request for CMD_ID 0x{command_id:02X} already in progress.")
			# Or raise an error to prevent concurrent identical commands if not desired
			raise HandCommandError(f"Command 0x{command_id:02X} already in progress.")

		current_request_future = asyncio.Future()
		self._pending_command_futures[command_id] = current_request_future

		# Command Structure: Schema | Command ID | Control Byte (IsRequest=1) | Data Length | Payload
		control_byte_request = 0x01 # IsRequest = 1
		data_length = len(request_payload)
		command_packet = struct.pack(">BBBB", SCHEMA_VERSION, command_id, control_byte_request, data_length) + request_payload
		
		try:
			logger.info(f"[{self.address}] Sending CMD 0x{command_id:02X} (Ctrl:0x{control_byte_request:02X}, Len:{data_length}): {command_packet.hex()}")
			await self._client.write_gatt_char(CONTROL_CHARACTERISTIC_UUID, command_packet, response=False)

			# Wait for the _control_notification_handler to set the result of current_request_future
			# The handler will parse the response specific to this command_id
			response_data = await asyncio.wait_for(current_request_future, timeout=timeout)
			return response_data # This will be what the handler's set_result() provided

		except asyncio.TimeoutError:
			logger.error(f"[{self.address}] Timeout ({timeout}s) waiting for response to CMD 0x{command_id:02X}.")
			if not current_request_future.done(): # Should be done by timeout, but ensure
				current_request_future.set_exception(HandCommandError(f"Timeout for CMD 0x{command_id:02X}", status=None))
			raise HandCommandError(f"Timeout for CMD 0x{command_id:02X}", status=None) # Re-raise as HandCommandError
		except BleakError as e: # Catch Bleak specific errors during write or notification issues
			logger.error(f"[{self.address}] BleakError during CMD 0x{command_id:02X}: {e}")
			if not current_request_future.done():
				current_request_future.set_exception(HandCommandError(f"BleakError for CMD 0x{command_id:02X}: {e}", status=None))
			raise HandCommandError(f"BleakError for CMD 0x{command_id:02X}: {e}", status=None) from e
		except HandCommandError: # Re-raise HandCommandErrors (e.g. from notification handler)
			raise
		except Exception as e: # Catch other unexpected errors
			logger.error(f"[{self.address}] Unexpected error during CMD 0x{command_id:02X}: {e}", exc_info=True)
			if not current_request_future.done():
				current_request_future.set_exception(HandCommandError(f"Unexpected error for CMD 0x{command_id:02X}: {e}", status=None))
			raise HandCommandError(f"Unexpected error for CMD 0x{command_id:02X}: {e}", status=None) from e
		finally:
			if self._pending_command_futures.get(command_id) is current_request_future:
				del self._pending_command_futures[command_id]

	async def get_relative_humidity(self, timeout: float = 5.0) -> Optional[float]:
		"""
		Sends a command to the hand to get the relative humidity.
		The response (a 32-bit float) is expected via a notification.
		"""
		# For Get Relative Humidity: No request payload
		request_payload = b''
		try:
			# The _control_notification_handler will parse the float for CMD_GET_RELATIVE_HUMIDITY
			# and set it as the result of the future.
			humidity_value = await self._send_command_and_process_response(
				command_id=CMD_GET_RELATIVE_HUMIDITY,
				request_payload=request_payload,
				timeout=timeout
			)
			if isinstance(humidity_value, float):
				return humidity_value
			else: # Should not happen if handler is correct for this CMD_ID
				logger.error(f"[{self.address}] Get Relative Humidity returned unexpected type: {type(humidity_value)}. Value: {humidity_value}")
				return None
		except HandCommandError as e:
			logger.error(f"[{self.address}] Failed to get relative humidity: {e}")
			return None
		except Exception as e: # Catch any other unexpected errors from the call
			logger.error(f"[{self.address}] Unexpected exception when getting humidity: {e}", exc_info=True)
			return None 