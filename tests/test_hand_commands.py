"""Tests for Hand command encoding."""

import pytest
import struct
import asyncio # Added for asyncio.TimeoutError
from unittest.mock import MagicMock, AsyncMock, patch

from bleak.backends.device import BLEDevice
from bleak import BleakClient

# Add project root to path for testing
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Imports from myolink (ensure correct paths based on your structure)
from myolink.device.hand import (
	Hand,
	CONTROL_CHARACTERISTIC_UUID,
	SCHEMA_VERSION,
	CMD_SET_DIGIT_POSITIONS,
	DIGIT_IDS,
	GripType,
	CMD_SET_GRIP,
	CMD_GET_RELATIVE_HUMIDITY,
	ResponseStatus,
	HandCommandError
)

# --- Test Fixtures --- #

@pytest.fixture
def mock_ble_device() -> BLEDevice:
	"""Creates a mock BLEDevice."""
	# Bleak's BLEDevice often needs specific platform details or a simple address
	# Creating a basic mock or using a placeholder address might suffice
	# Adjust if BLEDevice requires more specific instantiation for tests
	mock_device = MagicMock(spec=BLEDevice)
	mock_device.address = "00:11:22:33:44:55"
	mock_device.name = "MockHand"
	return mock_device

@pytest.fixture
def mock_bleak_client() -> MagicMock:
	"""Creates a mock BleakClient with an async write_gatt_char."""
	# Use spec=BleakClient to make the mock pass isinstance checks
	mock_client = MagicMock(spec=BleakClient)
	mock_client.is_connected = True
	mock_client.write_gatt_char = AsyncMock() # Mock the async method
	mock_client.start_notify = AsyncMock()    # Still useful if _ensure_notifications_started is called
	mock_client.address = "00:11:22:33:44:55" # Add address attribute
	# Removed direct service/char mocking here, will patch _ensure_notifications_started in tests that need it
	return mock_client

@pytest.fixture
def hand_instance(mock_ble_device, mock_bleak_client) -> Hand:
	"""Creates a Hand instance with a mocked BleakClient."""
	# Pass the mock client directly to the constructor
	hand = Hand(mock_bleak_client)
	# REMOVED: hand._client = mock_bleak_client # No longer needed, done in init
	return hand

# --- Helper Function --- #

def build_expected_command(positions_dict: dict[int, float]) -> bytes:
	"""Helper to construct the expected command bytes for a given position dictionary."""
	# Start payload with the specific "Set Digit Positions" sub-byte (0x01)
	payload = bytearray([0x01])
	# Ensure consistent order for predictable byte output in tests
	for digit_id in sorted(positions_dict.keys()):
		if digit_id not in DIGIT_IDS:
			continue # Should not happen if test data is valid
		pos = max(0.0, min(1.0, positions_dict[digit_id])) # Apply clamping like in the method
		# Append digit ID (1 byte) and position (4 bytes)
		payload.append(digit_id)
		payload.extend(struct.pack(">f", pos))

	# Data length is the length of the entire payload (0x01 byte + N * (ID + float))
	data_length = len(payload)
	command_header = struct.pack(">BBBB", SCHEMA_VERSION, CMD_SET_DIGIT_POSITIONS, 0x01, data_length)
	return command_header + payload

# --- Test Cases --- #

@pytest.mark.asyncio
async def test_ShouldEncodeCorrectly_WhenSettingAllDigitPositions(hand_instance, mock_bleak_client):
	"""Verify command encoding for setting all 5 digits."""
	# Use dictionary format
	positions_dict = {0: 0.1, 1: 0.2, 2: 0.3, 3: 0.4, 4: 0.5}
	expected_command = build_expected_command(positions_dict)

	await hand_instance.set_digit_positions(positions_dict)

	mock_bleak_client.write_gatt_char.assert_awaited_once_with(
		CONTROL_CHARACTERISTIC_UUID,
		expected_command,
		response=False
	)

@pytest.mark.asyncio
async def test_ShouldEncodeCorrectly_WhenSettingPartialDigitPositions(hand_instance, mock_bleak_client):
	"""Verify command encoding for setting fewer than 5 digits."""
	# Use dictionary format
	positions_dict = {0: 0.8, 1: 0.9}
	expected_command = build_expected_command(positions_dict)

	await hand_instance.set_digit_positions(positions_dict)

	mock_bleak_client.write_gatt_char.assert_awaited_once_with(
		CONTROL_CHARACTERISTIC_UUID,
		expected_command,
		response=False
	)

@pytest.mark.asyncio
async def test_ShouldClampValues_WhenSettingDigitPositionsOutOfBounds(hand_instance, mock_bleak_client):
	"""Verify positions are clamped to the 0.0-1.0 range."""
	# Use dictionary format
	positions_dict = {0: -0.5, 1: 1.5, 2: 0.5}
	clamped_positions_dict = {0: 0.0, 1: 1.0, 2: 0.5} # Expected values after clamping
	expected_command = build_expected_command(clamped_positions_dict)

	await hand_instance.set_digit_positions(positions_dict)

	mock_bleak_client.write_gatt_char.assert_awaited_once_with(
		CONTROL_CHARACTERISTIC_UUID,
		expected_command,
		response=False
	)

@pytest.mark.asyncio
async def test_ShouldNotSend_WhenNotConnected(hand_instance, mock_bleak_client):
	"""Verify command is not sent if the client is not connected."""
	# Simulate disconnected client state correctly
	mock_bleak_client.is_connected = False
	positions_dict = {0: 0.5}

	# Patch logger to capture error messages
	with patch('myolink.device.hand.logger') as mock_logger:
		await hand_instance.set_digit_positions(positions_dict)

	mock_bleak_client.write_gatt_char.assert_not_awaited()
	# The actual log message includes the address and more specific text
	expected_log_message = f"[{mock_bleak_client.address}] Cannot send Set Digit Positions: Not connected."
	mock_logger.error.assert_called_once_with(expected_log_message)

@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_positions", [
	{},           # Empty dict
	# Dictionary with too many items isn't strictly invalid for the dict format, but good to test?
	# {0: 0.1, 1: 0.2, 2: 0.3, 3: 0.4, 4: 0.5, 5: 0.6}, # Invalid digit ID 5 handled internally
	"not a dict", # Wrong type
	{0: 0.5, "abc": 0.5}, # Invalid key type
	{0: 0.5, 1: "abc"}   # Invalid value type
])
async def test_ShouldLogErrors_WhenInputIsInvalid(hand_instance, mock_bleak_client, invalid_positions):
	"""Verify errors/warnings are logged for various invalid inputs."""
	# Patch logger to capture error messages
	with patch('myolink.device.hand.logger') as mock_logger:
		await hand_instance.set_digit_positions(invalid_positions)

	mock_bleak_client.write_gatt_char.assert_not_awaited()
	# Check if either error or warning was called, as some invalid inputs might just warn 

# --- Tests for set_grip --- #

@pytest.mark.asyncio
async def test_ShouldEncodeCorrectly_WhenSettingGrip(hand_instance, mock_bleak_client):
	"""Verify command encoding for setting a grip."""
	desired_grip = GripType.POINT
	# Expected payload for Set Grip is just the grip ID byte.
	grip_id_byte = desired_grip.value
	request_payload = bytes([grip_id_byte])

	# Expected command: Schema | Command ID | Control Byte (IsRequest=1) | Data Length | Payload
	control_byte_request = 0x01 # IsRequest = 1
	data_length = len(request_payload)
	expected_command_packet = struct.pack(">BBBB", SCHEMA_VERSION, CMD_SET_GRIP, control_byte_request, data_length) + request_payload

	await hand_instance.set_grip(desired_grip)

	mock_bleak_client.write_gatt_char.assert_awaited_once_with(
		CONTROL_CHARACTERISTIC_UUID,
		expected_command_packet,
		response=False
	)

@pytest.mark.asyncio
async def test_ShouldNotSendGrip_WhenNotConnected(hand_instance, mock_bleak_client):
	"""Verify set_grip command is not sent if the client is not connected."""
	mock_bleak_client.is_connected = False
	desired_grip = GripType.HOOK

	with patch('myolink.device.hand.logger') as mock_logger:
		await hand_instance.set_grip(desired_grip)

	mock_bleak_client.write_gatt_char.assert_not_awaited()
	# Assuming similar log message format as set_digit_positions
	expected_log_message = f"[{mock_bleak_client.address}] Cannot send Set Grip: Not connected."
	mock_logger.error.assert_called_once_with(expected_log_message)

@pytest.mark.asyncio
async def test_ShouldLogErrors_WhenSetGripInputIsInvalid(hand_instance, mock_bleak_client):
	"""Verify errors are logged for invalid grip type input."""
	invalid_grip = "not_a_grip_type" # An invalid type

	with patch('myolink.device.hand.logger') as mock_logger:
		await hand_instance.set_grip(invalid_grip)

	mock_bleak_client.write_gatt_char.assert_not_awaited()
	# Check for the specific error log from set_grip
	expected_log_message = f"[{mock_bleak_client.address}] Invalid grip type: {invalid_grip}. Must be a GripType enum member."
	mock_logger.error.assert_called_once_with(expected_log_message)

# --- Tests for get_relative_humidity --- #

@pytest.mark.asyncio
async def test_ShouldSendCorrectCommand_WhenGettingHumidity(hand_instance, mock_bleak_client):
	"""Verify correct command is sent for get_relative_humidity and None is returned on timeout."""
	# Expected command: Schema | Command ID | Control Byte (IsRequest=1) | Data Length (0) | No Payload
	control_byte_request = 0x01 # IsRequest = 1
	data_length = 0x00
	expected_command_packet = struct.pack(">BBBB", SCHEMA_VERSION, CMD_GET_RELATIVE_HUMIDITY, control_byte_request, data_length)

	# Patch _ensure_notifications_started for this test to avoid dealing with service discovery complexities
	with patch.object(hand_instance, '_ensure_notifications_started', new_callable=AsyncMock) as mock_ensure_notify, \
	     patch('myolink.device.hand.logger') as mock_logger: # Patch logger to check error log
		
		# Call get_relative_humidity, expecting it to handle the timeout internally and return None
		result = await hand_instance.get_relative_humidity(timeout=0.1) # Use a short timeout

		assert result is None, "Expected None when get_relative_humidity times out internally"

		# Assert that _ensure_notifications_started was called
		mock_ensure_notify.assert_awaited_once()

		# Assert that the correct command was written by _send_command_and_process_response
		mock_bleak_client.write_gatt_char.assert_awaited_once_with(
			CONTROL_CHARACTERISTIC_UUID,
			expected_command_packet,
			response=False
		)

		# Assert that the timeout error was logged by get_relative_humidity
		# The actual HandCommandError from _send_command_and_process_response contains the address and command ID
		# e.g. Timeout for CMD 0x0A
		# get_relative_humidity logs: f"[{self.address}] Failed to get relative humidity: {e}"
		# So we need to check for a log message that contains these parts.
		found_log = False
		for call_args in mock_logger.error.call_args_list:
			logged_message = call_args[0][0] # First positional argument of the call
			if f"[{hand_instance.address}] Failed to get relative humidity" in logged_message and \
			   f"Timeout for CMD 0x{CMD_GET_RELATIVE_HUMIDITY:02X}" in logged_message:
				found_log = True
				break
		assert found_log, f"Expected log message about timeout for CMD 0x{CMD_GET_RELATIVE_HUMIDITY:02X} was not found"

@pytest.mark.asyncio
async def test_ShouldReturnHumidity_WhenSuccessfulResponseReceived(hand_instance, mock_bleak_client):
	"""Verify get_relative_humidity returns float when _send_command_and_process_response succeeds."""
	expected_humidity = 35.5
	test_timeout = 3.0

	# Patch _send_command_and_process_response. _ensure_notifications_started is internal to it.
	with patch.object(hand_instance, '_send_command_and_process_response', new_callable=AsyncMock, return_value=expected_humidity) as mock_send_cmd:

		result = await hand_instance.get_relative_humidity(timeout=test_timeout)

		assert result == expected_humidity, "get_relative_humidity did not return the expected humidity value."
		# mock_ensure_notify.assert_awaited_once() # Removed: _send_command_and_process_response is mocked, so its internals like _ensure_notifications are not called.
		mock_send_cmd.assert_awaited_once_with(
			command_id=CMD_GET_RELATIVE_HUMIDITY,
			request_payload=b'',
			timeout=test_timeout
		)

@pytest.mark.asyncio
async def test_ShouldReturnNone_WhenHumidityCommandFails(hand_instance, mock_bleak_client):
	"""Verify get_relative_humidity returns None when _send_command_and_process_response raises HandCommandError."""
	test_timeout = 3.0
	simulated_status = ResponseStatus.ERR_INTERNAL
	simulated_error = HandCommandError("Simulated command failure", status=simulated_status)

	# Patch _send_command_and_process_response. _ensure_notifications_started is internal to it.
	with patch.object(hand_instance, '_send_command_and_process_response', new_callable=AsyncMock, side_effect=simulated_error) as mock_send_cmd, \
	     patch('myolink.device.hand.logger') as mock_logger:

		result = await hand_instance.get_relative_humidity(timeout=test_timeout)

		assert result is None, "get_relative_humidity did not return None on HandCommandError."
		# mock_ensure_notify.assert_awaited_once() # Removed
		mock_send_cmd.assert_awaited_once_with(
			command_id=CMD_GET_RELATIVE_HUMIDITY,
			request_payload=b'',
			timeout=test_timeout
		)

		found_log = False
		for call_args in mock_logger.error.call_args_list:
			logged_message = str(call_args[0][0]) # Get the logged message string
			# Check if the log contains the address and the string representation of the simulated error
			if f"[{hand_instance.address}] Failed to get relative humidity" in logged_message and \
			   str(simulated_error) in logged_message: # The __str__ of HandCommandError includes details
				found_log = True
				break
		assert found_log, "Expected log message for HandCommandError was not found."

@pytest.mark.asyncio
async def test_ShouldReturnNone_WhenHumidityCommandTimesOut(hand_instance, mock_bleak_client):
	"""Verify get_relative_humidity returns None when _send_command_and_process_response indicates a timeout."""
	test_timeout = 0.1
	simulated_timeout_error = HandCommandError(f"Timeout for CMD 0x{CMD_GET_RELATIVE_HUMIDITY:02X}", status=None)

	# Patch _send_command_and_process_response. _ensure_notifications_started is internal to it.
	with patch.object(hand_instance, '_send_command_and_process_response', new_callable=AsyncMock, side_effect=simulated_timeout_error) as mock_send_cmd, \
	     patch('myolink.device.hand.logger') as mock_logger:

		result = await hand_instance.get_relative_humidity(timeout=test_timeout)

		assert result is None, "get_relative_humidity did not return None on command timeout."
		# mock_ensure_notify.assert_awaited_once() # Removed
		mock_send_cmd.assert_awaited_once_with(
			command_id=CMD_GET_RELATIVE_HUMIDITY,
			request_payload=b'',
			timeout=test_timeout
		)

		found_log = False
		for call_args in mock_logger.error.call_args_list:
			logged_message = str(call_args[0][0])
			if f"[{hand_instance.address}] Failed to get relative humidity" in logged_message and \
			   str(simulated_timeout_error) in logged_message:
				found_log = True
				break
		assert found_log, f"Expected log message for timeout error ({simulated_timeout_error}) was not found."

@pytest.mark.asyncio
async def test_ShouldNotGetHumidity_WhenNotConnected(hand_instance, mock_bleak_client):
	"""Verify get_relative_humidity doesn't try to send and returns None if not connected."""
	mock_bleak_client.is_connected = False

	# _ensure_notifications_started should not be called if disconnected check works early.
	# _send_command_and_process_response should also not be called.
	with patch.object(hand_instance, '_send_command_and_process_response', new_callable=AsyncMock) as mock_send_cmd, \
	     patch.object(hand_instance, '_ensure_notifications_started', new_callable=AsyncMock) as mock_ensure_notify, \
	     patch('myolink.device.hand.logger') as mock_logger:

		result = await hand_instance.get_relative_humidity(timeout=1.0)

		assert result is None, "Expected None when called while disconnected."
		mock_ensure_notify.assert_not_awaited() # Should not even try to start notifications
		mock_send_cmd.assert_not_awaited()      # Should not attempt to send command
		
		# Assert that the "Not connected" error was logged by get_relative_humidity itself
		expected_log_message = f"[{hand_instance.address}] Cannot get humidity: Not connected."
		mock_logger.error.assert_any_call(expected_log_message)

# --- Tests for _control_notification_handler --- #

@pytest.mark.asyncio # Async because it interacts with futures
async def test_NotificationHandler_ShouldSetResult_ForSuccessfulHumidityResponse(hand_instance):
	"""Verify handler correctly parses successful humidity notification and sets future result."""
	# Simulate a successful humidity notification (Schema=0, CMD_ID=0x0A, Status=0x00, Length=0x04, Payload=float)
	simulated_humidity_value = 42.75
	payload = struct.pack(">f", simulated_humidity_value)
	# Header: Schema | CMD_ID | Status (Success=0, IsRequest=0) | Length
	header = struct.pack(">BBBB", SCHEMA_VERSION, CMD_GET_RELATIVE_HUMIDITY, 0x00, len(payload))
	simulated_data = bytearray(header + payload)
	sender_handle = 33 # Dummy handle

	# Create and register a future for the humidity command
	humidity_future = asyncio.Future()
	hand_instance._pending_command_futures[CMD_GET_RELATIVE_HUMIDITY] = humidity_future

	# Call the notification handler
	hand_instance._control_notification_handler(sender_handle, simulated_data)

	# Assert the future is done and has the correct result
	assert humidity_future.done(), "Humidity future should be done after successful notification."
	assert not humidity_future.cancelled(), "Humidity future should not be cancelled."
	assert humidity_future.exception() is None, "Humidity future should not have an exception."
	assert humidity_future.result() == simulated_humidity_value, "Humidity future result should match parsed float."

	# Clean up the future from the instance's dict after testing (optional but good practice)
	if CMD_GET_RELATIVE_HUMIDITY in hand_instance._pending_command_futures:
		del hand_instance._pending_command_futures[CMD_GET_RELATIVE_HUMIDITY]

@pytest.mark.asyncio
async def test_NotificationHandler_ShouldSetException_ForFailedHumidityResponse(hand_instance):
	"""Verify handler sets exception for failed humidity notification."""
	# Simulate a failed humidity notification (e.g., Invalid Command status)
	simulated_status = ResponseStatus.ERR_INVALID_CMD.value
	payload = b'\x00\x00\x00\x00' # Dummy payload
	# Header: Schema | CMD_ID | Status (InvalidCmd=1, IsRequest=0) | Length (still declared 4)
	header = struct.pack(">BBBB", SCHEMA_VERSION, CMD_GET_RELATIVE_HUMIDITY, simulated_status, len(payload))
	simulated_data = bytearray(header + payload)
	sender_handle = 33 # Dummy handle

	# Create and register a future for the humidity command
	humidity_future = asyncio.Future()
	hand_instance._pending_command_futures[CMD_GET_RELATIVE_HUMIDITY] = humidity_future

	# Call the notification handler
	hand_instance._control_notification_handler(sender_handle, simulated_data)

	# Assert the future is done and has a HandCommandError exception
	assert humidity_future.done(), "Humidity future should be done after failed notification."
	assert not humidity_future.cancelled(), "Humidity future should not be cancelled."
	assert humidity_future.exception() is not None, "Humidity future should have an exception."
	assert isinstance(humidity_future.exception(), HandCommandError), "Exception should be HandCommandError."
	assert humidity_future.exception().status == ResponseStatus.ERR_INVALID_CMD, "HandCommandError status should match simulated status."

	if CMD_GET_RELATIVE_HUMIDITY in hand_instance._pending_command_futures:
		del hand_instance._pending_command_futures[CMD_GET_RELATIVE_HUMIDITY]

@pytest.mark.asyncio
async def test_NotificationHandler_ShouldSetException_ForHumidityLengthMismatch(hand_instance):
	"""Verify handler sets exception for humidity notification with incorrect data length."""
	# Simulate a humidity notification with success status but wrong length
	simulated_humidity_value = 50.0
	payload = struct.pack(">f", simulated_humidity_value) + b'\xAA' # Add extra byte
	# Header: Schema | CMD_ID | Status (Success=0, IsRequest=0) | Length (declared as 4, but payload is 5)
	# The handler checks declared vs actual. Let's declare 4 but send 5 payload bytes.
	header = struct.pack(">BBBB", SCHEMA_VERSION, CMD_GET_RELATIVE_HUMIDITY, 0x00, 4)
	simulated_data = bytearray(header + payload)
	sender_handle = 33 # Dummy handle

	# Create and register a future
	humidity_future = asyncio.Future()
	hand_instance._pending_command_futures[CMD_GET_RELATIVE_HUMIDITY] = humidity_future

	with patch('myolink.device.hand.logger') as mock_logger:
		# Call the handler
		hand_instance._control_notification_handler(sender_handle, simulated_data)

	# Assert the future is done and has a HandCommandError exception
	assert humidity_future.done(), "Humidity future should be done after length mismatch notification."
	assert not humidity_future.cancelled(), "Humidity future should not be cancelled."
	assert humidity_future.exception() is not None, "Humidity future should have an exception."
	assert isinstance(humidity_future.exception(), HandCommandError), "Exception should be HandCommandError."
	# Assert error message indicates length mismatch
	assert "Payload length mismatch" in str(humidity_future.exception()), "Error message should mention length mismatch."
	
	# Check for warning log about length mismatch
	found_warning_log = False
	for call_args in mock_logger.warning.call_args_list:
		logged_message = str(call_args[0][0])
		if "Payload length mismatch" in logged_message and f"CMD 0x{CMD_GET_RELATIVE_HUMIDITY:02X}" in logged_message:
			found_warning_log = True
			break
	assert found_warning_log, "Expected warning log for payload length mismatch was not found."


	if CMD_GET_RELATIVE_HUMIDITY in hand_instance._pending_command_futures:
		del hand_instance._pending_command_futures[CMD_GET_RELATIVE_HUMIDITY]

@pytest.mark.asyncio
async def test_NotificationHandler_ShouldSetException_ForHumidityInvalidFloatData(hand_instance):
	"""Verify handler sets exception for humidity notification with invalid float data."""
	# Simulate a successful humidity notification with invalid float bytes
	payload = b'\xFF\xFF\xFF\xFF' # Invalid float bytes
	# Header: Schema | CMD_ID | Status (Success=0, IsRequest=0) | Length
	header = struct.pack(">BBBB", SCHEMA_VERSION, CMD_GET_RELATIVE_HUMIDITY, 0x00, len(payload))
	simulated_data = bytearray(header + payload)
	sender_handle = 33 # Dummy handle

	# Create and register a future
	humidity_future = asyncio.Future()
	hand_instance._pending_command_futures[CMD_GET_RELATIVE_HUMIDITY] = humidity_future

	with patch('myolink.device.hand.logger') as mock_logger:
		# Call the handler
		hand_instance._control_notification_handler(sender_handle, simulated_data)

	# Assert the future is done and has a HandCommandError exception (wrapping struct.error)
	assert humidity_future.done(), "Humidity future should be done after invalid float data notification."
	assert not humidity_future.cancelled(), "Humidity future should not be cancelled."
	assert humidity_future.exception() is not None, "Humidity future should have an exception."
	assert "Received invalid humidity float value" in str(humidity_future.exception()), "Error message should mention invalid float format."

	# Check for error log about unpacking failure
	found_error_log = False
	for call_args in mock_logger.error.call_args_list:
		logged_message = str(call_args[0][0])
		if "Received invalid humidity float value" in logged_message and f"CMD 0x{CMD_GET_RELATIVE_HUMIDITY:02X}" in logged_message:
			found_error_log = True
			break
	assert found_error_log, "Expected error log for invalid float value was not found."


	if CMD_GET_RELATIVE_HUMIDITY in hand_instance._pending_command_futures:
		del hand_instance._pending_command_futures[CMD_GET_RELATIVE_HUMIDITY]

@pytest.mark.asyncio
async def test_NotificationHandler_ShouldHandleUnsolicitedNotification(hand_instance):
	"""Verify handler logs a warning for unsolicited notifications (no pending future)."""
	# Simulate a notification for a command ID that doesn't have a pending future
	cmd_id_unsolicited = 0xFF # A dummy command ID unlikely to have a future
	payload = b'\x01\x02\x03\x04'
	header = struct.pack(">BBBB", SCHEMA_VERSION, cmd_id_unsolicited, 0x00, len(payload))
	simulated_data = bytearray(header + payload)
	sender_handle = 33

	# Ensure no future exists for this command ID
	assert cmd_id_unsolicited not in hand_instance._pending_command_futures, "Pre-condition check failed: Future should not exist."

	with patch('myolink.device.hand.logger') as mock_logger:
		# Call the handler
		hand_instance._control_notification_handler(sender_handle, simulated_data)

	# Assert no futures were created/modified and a warning was logged
	assert cmd_id_unsolicited not in hand_instance._pending_command_futures, "No new future should be created."
	# Check for warning log about unsolicited response
	mock_logger.warning.assert_called_once()
	logged_message = str(mock_logger.warning.call_args[0][0])
	assert f"Received response for CMD 0x{cmd_id_unsolicited:02X}, but no pending/active future found" in logged_message

@pytest.mark.asyncio
async def test_NotificationHandler_ShouldHandleNotificationTooShort(hand_instance):
	"""Verify handler logs a warning for notifications too short to parse header."""
	# Simulate a notification with less than 4 bytes
	simulated_data = bytearray(b'\x01\x02\x03') # Only 3 bytes
	sender_handle = 33

	with patch('myolink.device.hand.logger') as mock_logger:
		# Call the handler
		hand_instance._control_notification_handler(sender_handle, simulated_data)

	# Assert a warning is logged and handler returns early
	mock_logger.warning.assert_called_once()
	logged_message = str(mock_logger.warning.call_args[0][0])
	assert "Notification too short to parse header" in logged_message

	# No futures should be affected, no errors should be set.

@pytest.mark.asyncio
async def test_NotificationHandler_ShouldHandleNotificationMarkedAsRequest(hand_instance):
	"""Verify handler logs a warning and ignores notifications marked as requests."""
	# Simulate a notification with the IsRequest bit set (e.g., Status=0x80)
	cmd_id = 0xFE # Dummy command ID
	payload = b'\xAA\xBB'
	# Header: Schema | CMD_ID | Status (IsRequest=1, any status bits) | Length
	header = struct.pack(">BBBB", SCHEMA_VERSION, cmd_id, 0x80, len(payload)) # Status 0x80 sets IsRequest bit
	simulated_data = bytearray(header + payload)
	sender_handle = 33

	# Even if a future exists, it should be ignored because it's a request
	dummy_future = asyncio.Future()
	hand_instance._pending_command_futures[cmd_id] = dummy_future

	with patch('myolink.device.hand.logger') as mock_logger:
		# Call the handler
		hand_instance._control_notification_handler(sender_handle, simulated_data)

	# Assert the future is NOT done/affected and a warning is logged
	assert not dummy_future.done(), "Future should not be done for request notification."
	mock_logger.warning.assert_called_once()
	logged_message = str(mock_logger.warning.call_args[0][0])
	assert "Received notification that is marked as a 'request'" in logged_message

	if cmd_id in hand_instance._pending_command_futures:
		# Clean up manually as handler ignored it
		del hand_instance._pending_command_futures[cmd_id]

@pytest.mark.asyncio
async def test_NotificationHandler_ShouldSetException_ForUnknownResponseStatus(hand_instance):
	"""Verify handler sets exception for notifications with unknown ResponseStatus."""
	# Simulate a notification with a valid header but an unknown status bit combination (e.g., 0x07)
	cmd_id = 0x0A # Use Humidity CMD ID as it has specific processing, though generalized path should handle it too
	unknown_raw_status = 0x07 # Bits 0-2 are 111, which is not a defined ResponseStatus
	payload = b'\x00\x00\x00\x00' # Dummy payload (length 4)
	# Header: Schema | CMD_ID | Status (unknown_raw_status, IsRequest=0) | Length
	header = struct.pack(">BBBB", SCHEMA_VERSION, cmd_id, unknown_raw_status, len(payload))
	simulated_data = bytearray(header + payload)
	sender_handle = 33

	# Create and register a future
	cmd_future = asyncio.Future()
	hand_instance._pending_command_futures[cmd_id] = cmd_future

	with patch('myolink.device.hand.logger') as mock_logger:
		# Call the handler
		hand_instance._control_notification_handler(sender_handle, simulated_data)

	# Assert the future is done and has a HandCommandError exception (wrapping ValueError)
	assert cmd_future.done(), "Future should be done after unknown status notification."
	assert not cmd_future.cancelled(), "Future should not be cancelled."
	assert cmd_future.exception() is not None, "Future should have an exception."
	assert isinstance(cmd_future.exception(), HandCommandError), "Exception should be HandCommandError."
	# Check the wrapped exception type or message
	assert isinstance(cmd_future.exception().__cause__, ValueError), "Cause exception should be ValueError."
	assert "Unknown response status" in str(cmd_future.exception()), "Error message should mention unknown status."

	# Check for error log about unknown status
	mock_logger.error.assert_called_once()
	logged_message = str(mock_logger.error.call_args[0][0])
	assert "Unknown ResponseStatus value" in logged_message and f"CMD 0x{cmd_id:02X}" in logged_message


	if cmd_id in hand_instance._pending_command_futures:
		del hand_instance._pending_command_futures[cmd_id]
