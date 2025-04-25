"""Tests for Hand command encoding."""

import pytest
import struct
from unittest.mock import MagicMock, AsyncMock, patch

from bleak.backends.device import BLEDevice

# Add project root to path for testing
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Imports from myolink (ensure correct paths based on your structure)
from myolink.device.hand import (
	Hand,
	CONTROL_CHARACTERISTIC_UUID,
	SCHEMA_VERSION,
	CMD_SET_DIGIT_POSITIONS,
	DIGIT_IDS
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
	mock_client = MagicMock()
	mock_client.is_connected = True
	mock_client.write_gatt_char = AsyncMock() # Mock the async method
	return mock_client

@pytest.fixture
def hand_instance(mock_ble_device, mock_bleak_client) -> Hand:
	"""Creates a Hand instance with a mocked BleakClient."""
	hand = Hand(mock_ble_device)
	# Inject the mock client directly for testing command sending
	hand._client = mock_bleak_client
	return hand

# --- Helper Function --- #

def build_expected_command(positions: list[float]) -> bytes:
	"""Helper to construct the expected command bytes for given positions."""
	payload = bytearray()
	payload.append(0x01) # Set Digit Positions Subcommand
	num_digits = len(positions)
	for i in range(num_digits):
		digit_id = DIGIT_IDS[i]
		pos = max(0.0, min(1.0, positions[i])) # Apply clamping like in the method
		payload.append(digit_id)
		payload.extend(struct.pack(">f", pos))

	data_length = len(payload)
	command_header = struct.pack("<BBBB", SCHEMA_VERSION, CMD_SET_DIGIT_POSITIONS, 0x01, data_length)
	return command_header + payload

# --- Test Cases --- #

@pytest.mark.asyncio
async def test_ShouldEncodeCorrectly_WhenSettingAllDigitPositions(hand_instance, mock_bleak_client):
	"""Verify command encoding for setting all 5 digits."""
	positions = [0.1, 0.2, 0.3, 0.4, 0.5]
	expected_command = build_expected_command(positions)

	await hand_instance.set_digit_positions(positions)

	mock_bleak_client.write_gatt_char.assert_awaited_once_with(
		CONTROL_CHARACTERISTIC_UUID,
		expected_command,
		response=False
	)

@pytest.mark.asyncio
async def test_ShouldEncodeCorrectly_WhenSettingPartialDigitPositions(hand_instance, mock_bleak_client):
	"""Verify command encoding for setting fewer than 5 digits."""
	positions = [0.8, 0.9]
	expected_command = build_expected_command(positions)

	await hand_instance.set_digit_positions(positions)

	mock_bleak_client.write_gatt_char.assert_awaited_once_with(
		CONTROL_CHARACTERISTIC_UUID,
		expected_command,
		response=False
	)

@pytest.mark.asyncio
async def test_ShouldClampValues_WhenSettingDigitPositionsOutOfBounds(hand_instance, mock_bleak_client):
	"""Verify positions are clamped to the 0.0-1.0 range."""
	positions = [-0.5, 1.5, 0.5]
	clamped_positions = [0.0, 1.0, 0.5] # Expected values after clamping
	expected_command = build_expected_command(clamped_positions)

	await hand_instance.set_digit_positions(positions)

	mock_bleak_client.write_gatt_char.assert_awaited_once_with(
		CONTROL_CHARACTERISTIC_UUID,
		expected_command,
		response=False
	)

@pytest.mark.asyncio
async def test_ShouldNotSend_WhenNotConnected(hand_instance, mock_bleak_client):
	"""Verify command is not sent if the client is not connected."""
	hand_instance._client = None # Simulate not connected
	positions = [0.5]

	# Patch logger to capture error messages
	with patch('myolink.device.hand.logger') as mock_logger:
		await hand_instance.set_digit_positions(positions)

	mock_bleak_client.write_gatt_char.assert_not_awaited()
	mock_logger.error.assert_called_once_with("Cannot send command: Not connected.")

@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_positions", [
	[],           # Empty list
	[0.1, 0.2, 0.3, 0.4, 0.5, 0.6], # Too many values
	"not a list", # Wrong type
	[0.5, "abc", 0.5] # Invalid type within list
])
async def test_ShouldLogErrors_WhenInputIsInvalid(hand_instance, mock_bleak_client, invalid_positions):
	"""Verify errors are logged for various invalid inputs."""
	# Patch logger to capture error messages
	with patch('myolink.device.hand.logger') as mock_logger:
		await hand_instance.set_digit_positions(invalid_positions)

	mock_bleak_client.write_gatt_char.assert_not_awaited()
	mock_logger.error.assert_called()
	# We could be more specific about the error message if needed 