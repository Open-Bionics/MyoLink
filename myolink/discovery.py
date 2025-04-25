import struct
from enum import Enum
from dataclasses import dataclass, field
import logging
from typing import List, Union, Dict, Any

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

logger = logging.getLogger(__name__)

# --- Constants ---
OPEN_BIONICS_COMPANY_ID = 0x0ABA
MANUFACTURER_DATA_TYPE = 0xFF

# --- Enums for Parsed Data ---

class Chirality(Enum):
    RIGHT_OR_CLOSE = 0
    LEFT_OR_OPEN = 1

class DeviceType(Enum):
    HERO_ARM = 0 # Not OB2
    OB2_HAND = 1
    OB2_SENSOR = 2
    RESERVED_3 = 3
    RESERVED_4 = 4
    RESERVED_5 = 5
    RESERVED_6 = 6
    RESERVED_7 = 7

class HandSize(Enum):
    EXTRA_SMALL = 0
    SMALL = 1
    MEDIUM = 2
    LARGE = 3

class HandClass(Enum):
    OB2_AIR = 0
    OB2_PRO = 1
    OB2_RUGGED = 2
    RESERVED = 3

class SensorType(Enum):
    EMG = 0
    RESERVED_1 = 1
    RESERVED_2 = 2
    RESERVED_3 = 3

class SensorAdvertisingReason(Enum):
    CONNECT_ASSOCIATED_HAND = 0
    FORM_NEW_ASSOCIATION = 1
    CONNECT_APP = 2
    REQUEST_FROM_HAND = 3

# --- Dataclasses for Parsed Data ---

@dataclass
class DeviceConfig:
    raw_byte: int
    chirality: Chirality = field(init=False)
    device_type: DeviceType = field(init=False)
    is_bootloader: bool = field(init=False)
    is_hil: bool = field(init=False)

    def __post_init__(self):
        self.chirality = Chirality(self.raw_byte & 0x01)
        self.device_type = DeviceType((self.raw_byte >> 1) & 0x07)
        # bits 4, 5, 6 are reserved
        self.is_bootloader = bool((self.raw_byte >> 6) & 0x01)
        self.is_hil = bool((self.raw_byte >> 7) & 0x01)

@dataclass
class HandSpecificData:
    raw_byte: int
    size: HandSize = field(init=False)
    hand_class: HandClass = field(init=False)

    def __post_init__(self):
        self.size = HandSize(self.raw_byte & 0x03)
        self.hand_class = HandClass((self.raw_byte >> 2) & 0x03)
        # bits 4-7 reserved

@dataclass
class SensorSpecificDataV1:
    # Schema V1 OB2 Sensor Device Specific Data
    raw_byte: int
    sensor_type: SensorType = field(init=False)

    def __post_init__(self):
        self.sensor_type = SensorType(self.raw_byte & 0x03)
        # bits 2-7 reserved

@dataclass
class SensorSpecificDataV2:
    # Schema V2 OB2 Sensor Device Specific Data
    raw_byte: int
    sensor_type: SensorType = field(init=False)
    is_open_for_association: bool = field(init=False)

    def __post_init__(self):
        self.sensor_type = SensorType(self.raw_byte & 0x03)
        self.is_open_for_association = bool((self.raw_byte >> 2) & 0x01)
        # bits 3-7 reserved

@dataclass
class SensorSpecificDataV3:
    # Schema V3 OB2 Sensor Device Specific Data
    raw_byte: int
    sensor_type: SensorType = field(init=False)
    advertising_reason: SensorAdvertisingReason = field(init=False)
    leads_on_user: bool = field(init=False)

    def __post_init__(self):
        self.sensor_type = SensorType(self.raw_byte & 0x03)
        self.advertising_reason = SensorAdvertisingReason((self.raw_byte >> 2) & 0x03)
        self.leads_on_user = bool((self.raw_byte >> 4) & 0x01)
        # bits 5-7 reserved

@dataclass
class ParsedAdvertisingData:
    schema_version: int
    device_config: DeviceConfig
    device_specific_data: Union[HandSpecificData, SensorSpecificDataV1, SensorSpecificDataV2, SensorSpecificDataV3, int]
    battery_level: int # 0-100 (%)
    # Schema V1 Specific
    association_id_v1: bytes | None = None # 6 bytes
    # Schema V2/V3 Specific
    mac_address_part: int | None = None # 4 bytes
    num_associations: int | None = None
    association_ids_v2: List[int] | None = None # List of 4-byte uints

    # Add raw manufacturer data for debugging/completeness
    raw_manufacturer_data: bytes = b''

# --- Parsing Function ---

def parse_advertisement_data(ad_data: AdvertisementData) -> ParsedAdvertisingData | None:
    """Parses Open Bionics specific advertising data.

    Args:
        ad_data: The AdvertisementData object from Bleak scanner.

    Returns:
        A ParsedAdvertisingData object if Open Bionics data is found and parsed,
        otherwise None.
    """
    if not ad_data or not ad_data.manufacturer_data:
        return None

    mfg_data = ad_data.manufacturer_data.get(OPEN_BIONICS_COMPANY_ID)

    if mfg_data is None:
        return None # Not an Open Bionics device (based on Company ID)

    try:
        if len(mfg_data) < 1: # Need at least schema version
            logger.warning(f"OB Mfg data too short: {mfg_data.hex()}")
            return None

        schema_version = mfg_data[0]
        parsed_data = None

        # --- Schema Version 1 Parsing (Deprecated but for completeness) ---
        if 1 == schema_version:
            # Expected length = 1 schema + 1 config + 1 specific + 1 battery + 6 assoc = 10 bytes
            if len(mfg_data) < 10:
                logger.warning(f"OB Mfg data schema V1 too short: {len(mfg_data)} bytes, expected 10. Data: {mfg_data.hex()}")
                return None

            dev_config_byte = mfg_data[1]
            dev_specific_byte = mfg_data[2]
            battery = mfg_data[3]
            assoc_id_v1 = mfg_data[4:10]

            dev_config = DeviceConfig(dev_config_byte)
            dev_specific_parsed: Any = dev_specific_byte # Default to raw int if type unknown

            if DeviceType.OB2_HAND == dev_config.device_type:
                dev_specific_parsed = HandSpecificData(dev_specific_byte)
            elif DeviceType.OB2_SENSOR == dev_config.device_type:
                dev_specific_parsed = SensorSpecificDataV1(dev_specific_byte)
            # else: Hero Arm or Reserved - keep raw byte

            parsed_data = ParsedAdvertisingData(
                schema_version=schema_version,
                device_config=dev_config,
                device_specific_data=dev_specific_parsed,
                battery_level=battery,
                association_id_v1=assoc_id_v1,
                raw_manufacturer_data=mfg_data
            )

        # --- Schema Version 2/3 Parsing ---
        elif schema_version in [2, 3]:
            # Expected length = 1 schema + 1 config + 1 specific + 1 battery + 4 mac + 1 num_assoc + (4 * n_assoc)
            # Minimum length (0 associations) = 9 bytes
            if len(mfg_data) < 9:
                logger.warning(f"OB Mfg data schema V{schema_version} too short: {len(mfg_data)} bytes, min 9. Data: {mfg_data.hex()}")
                return None

            dev_config_byte = mfg_data[1]
            dev_specific_byte = mfg_data[2]
            battery = mfg_data[3]
            mac_part = struct.unpack('>I', mfg_data[4:8])[0] # 4-byte uint, big-endian
            num_assoc = mfg_data[8]

            expected_len = 9 + (4 * num_assoc)
            if len(mfg_data) < expected_len:
                logger.warning(f"OB Mfg data schema V{schema_version} too short for associations: {len(mfg_data)} bytes, expected {expected_len}. Data: {mfg_data.hex()}")
                # Continue parsing what we have, but assoc list will be empty/truncated
                num_assoc = (len(mfg_data) - 9) // 4 # Adjust num_assoc based on actual data length

            assoc_ids_v2 = []
            for i in range(num_assoc):
                start_idx = 9 + (i * 4)
                end_idx = start_idx + 4
                if end_idx <= len(mfg_data):
                    assoc_id = struct.unpack('>I', mfg_data[start_idx:end_idx])[0]
                    assoc_ids_v2.append(assoc_id)

            dev_config = DeviceConfig(dev_config_byte)
            dev_specific_parsed: Any = dev_specific_byte # Default to raw int

            if DeviceType.OB2_HAND == dev_config.device_type:
                dev_specific_parsed = HandSpecificData(dev_specific_byte)
            elif DeviceType.OB2_SENSOR == dev_config.device_type:
                if 3 == schema_version:
                    dev_specific_parsed = SensorSpecificDataV3(dev_specific_byte)
                else: # Schema version 2
                    dev_specific_parsed = SensorSpecificDataV2(dev_specific_byte)
            # else: Hero Arm or Reserved - keep raw byte

            parsed_data = ParsedAdvertisingData(
                schema_version=schema_version,
                device_config=dev_config,
                device_specific_data=dev_specific_parsed,
                battery_level=battery,
                mac_address_part=mac_part,
                num_associations=num_assoc,
                association_ids_v2=assoc_ids_v2,
                raw_manufacturer_data=mfg_data
            )

        else:
            logger.warning(f"Unsupported OB Mfg data schema version: {schema_version}. Data: {mfg_data.hex()}")
            return None

        return parsed_data

    except struct.error as e:
        logger.error(f"Failed to unpack OB Mfg data: {e}. Data: {mfg_data.hex()}")
        return None
    except Exception as e:
        logger.error(f"Error parsing OB Mfg data: {e}. Data: {mfg_data.hex()}")
        return None 