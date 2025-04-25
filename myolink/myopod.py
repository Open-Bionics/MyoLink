import asyncio
from bleak import BleakClient

class MyoPod:
    """Represents a MyoPod EMG sensor device."""

    def __init__(self, client: BleakClient):
        """
        Initialises the MyoPod instance.

        Args:
            client: The BleakClient connected to the MyoPod device.
        """
        if None is client:
            raise ValueError("BleakClient cannot be None")
        self._client = client

    # TODO: Add methods for MyoPod interaction (configuration, data streaming)

async def main():
    # Example usage placeholder
    pass

if "__main__" == __name__:
    asyncio.run(main()) 