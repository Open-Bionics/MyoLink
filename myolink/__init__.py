"""MyoLink Core Library"""

__version__ = "0.0.1"

from .core import discover_devices
# Import other core components, MyoPod as they are created 

from .device.hand import Hand, GripType
from .myopod import MyoPod, EmgStreamSource, CompressionType, StreamDataPacket
from .discovery import (
    parse_advertisement_data,
    ParsedAdvertisingData,
    DeviceConfig,
    DeviceType,
    Chirality,
    HandSpecificData,
    HandClass,
    HandSize,
    SensorSpecificDataV1,
    SensorSpecificDataV2,
    SensorSpecificDataV3,
    SensorType,
    SensorAdvertisingReason
)

# Optional: Define __all__ for cleaner imports from the package level
__all__ = [
    'Hand', 'GripType',
    'MyoPod', 'EmgStreamSource', 'CompressionType', 'StreamDataPacket',
    # Discovery exports
    'parse_advertisement_data', 'ParsedAdvertisingData', 'DeviceConfig',
    'DeviceType', 'Chirality', 'HandSpecificData', 'HandClass', 'HandSize',
    'SensorSpecificDataV1', 'SensorSpecificDataV2', 'SensorSpecificDataV3',
    'SensorType', 'SensorAdvertisingReason'
] 