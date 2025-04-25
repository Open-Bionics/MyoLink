"""Tests for Hand command encoding."""

import pytest
import struct
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
	GripType
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
	mock_client.address = "00:11:22:33:44:55" # Add address attribute
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
	mock_logger.error.assert_called_once_with("Cannot send command: Not connected.")

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