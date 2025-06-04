"""Example: Discover, connect to a Hand, and attempt to monitor a humidity characteristic."""

import asyncio
import logging
import sys
import os
import time
import datetime
import csv
import struct # Added for unpacking float
from typing import List, Tuple, Optional, Dict

# Add project root to path if running script directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice

from myolink import discover_devices, DeviceType, Hand # type: ignore
from myolink.discovery import ParsedAdvertisingData # type: ignore

# Attempt to import plotting libraries
try:
	import pyqtgraph as pg
	from pyqtgraph.Qt import QtCore, QtWidgets
	PYQTGRAPH_AVAILABLE = True
except ImportError:
	PYQTGRAPH_AVAILABLE = False
	print("pyqtgraph or PyQt5 not found. Plotting will be disabled. "
		  "Install them with: pip install pyqtgraph PyQt5 qasync") # Added qasync to suggestion

# --- Configuration ---
# !!! IMPORTANT: Replace with the actual UUID for your hand's humidity characteristic !!!
# This is a placeholder UUID. If the hand doesn't have a humidity service/char, this will fail.
# HUMIDITY_SERVICE_UUID = "0000181A-0000-1000-8000-00805f9b34fb" # Standard Environmental Sensing Service
# HUMIDITY_CHARACTERISTIC_UUID = "00002A6F-0000-1000-8000-00805f9b34fb" # Standard Humidity Characteristic
# These are no longer needed as the Hand class handles this internally via control commands

READ_INTERVAL_SECONDS = 1.0  # How often to read humidity
PLOT_MAX_POINTS = 1800       # Maximum number of data points to display on the plot
CSV_OUTPUT_DIR = "humidity_readings" # Directory to save CSV files

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO,
					format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Global variables for plotting ---
plot_widget: Optional[pg.PlotWidget] = None
plot_curve: Optional[pg.PlotDataItem] = None
humidity_data: List[float] = []
time_data: List[float] = []
start_time_monotonic: float = 0.0 # Renamed for clarity
# New globals for temperature
plot_curve_temp: Optional[pg.PlotDataItem] = None
temperature_data: List[float] = []

# --- Plotting Functions (if pyqtgraph is available) ---
app_instance = None # Global variable for QApplication

def ensure_qapp():
	"""Ensures a QApplication instance exists."""
	global app_instance
	app_instance = QtWidgets.QApplication.instance()
	if app_instance is None:
		# Correctly pass sys.argv or an empty list if sys.argv is not appropriate
		app_instance = QtWidgets.QApplication(sys.argv if hasattr(sys, 'argv') else [])
	return app_instance

def setup_plot():
	global plot_widget, plot_curve, humidity_data, time_data, start_time_monotonic
	global plot_curve_temp, temperature_data # Added temperature globals

	if not PYQTGRAPH_AVAILABLE:
		logger.warning("Plotting is disabled as pyqtgraph/PyQt5 is not available.")
		return

	ensure_qapp() # Ensure QApplication exists

	plot_widget = pg.PlotWidget()
	plot_widget.setWindowTitle('Live Hand Sensor Data')
	# plot_widget.setLabel('left', 'Relative Humidity (%)') # More generic label now
	plot_widget.setLabel('left', 'Sensor Value')
	plot_widget.setLabel('bottom', 'Time (s)')
	plot_widget.showGrid(x=True, y=True)
	plot_widget.addLegend() # Add legend for multiple plots

	plot_curve = plot_widget.plot(pen='y', name='Humidity (%)') # Yellow line for humidity
	plot_curve_temp = plot_widget.plot(pen='r', name='Temperature (°C)') # Red line for temperature

	humidity_data = []
	temperature_data = [] # Initialize temperature data list
	time_data = []
	start_time_monotonic = time.monotonic()

	plot_widget.show()


def update_plot(humidity_value: Optional[float], temperature_value: Optional[float]):
	global plot_widget, plot_curve, humidity_data, time_data, plot_curve_temp, temperature_data

	if not PYQTGRAPH_AVAILABLE or plot_curve is None or plot_curve_temp is None or plot_widget is None:
		return

	current_time_sec = time.monotonic() - start_time_monotonic
	
	# Append humidity or NaN if None
	humidity_data.append(humidity_value if humidity_value is not None else float('nan'))
	# Append temperature or NaN if None
	temperature_data.append(temperature_value if temperature_value is not None else float('nan'))
	time_data.append(current_time_sec)

	# Keep only the last PLOT_MAX_POINTS
	if len(time_data) > PLOT_MAX_POINTS: # Use time_data length as reference
		humidity_data.pop(0)
		temperature_data.pop(0)
		time_data.pop(0)

	plot_curve.setData(time_data, humidity_data)
	plot_curve_temp.setData(time_data, temperature_data)


class HumidityMonitor:
	def __init__(self):
		self._client: Optional[BleakClient] = None
		self._hand_device: Optional[BLEDevice] = None
		self._hand_ad_data: Optional[ParsedAdvertisingData] = None
		self._csv_writer: Optional[csv.writer] = None
		self._csv_file = None # Type: Optional[IO[str]]
		self._monitoring_active = False
		self._read_task: Optional[asyncio.Task] = None
		self._hand_object: Optional[Hand] = None # To store the Hand instance

	async def _notification_handler(self, sender_handle: int, data: bytearray):
		"""Handles incoming BLE notifications (if characteristic supports notify)."""
		# This handler is primarily for humidity if direct characteristic notification is used.
		# For temperature, we rely on the periodic read polling the Hand object.
		humidity_value: Optional[float] = None
		try:
			# Standard Humidity characteristic is uint16, value is N * 0.01 percent
			if len(data) == 2: # Assuming this is specific to a humidity characteristic
				humidity_raw = struct.unpack("<H", data)[0] # uint16_t
				humidity_value = float(humidity_raw) / 100.0 # Convert to percentage
				logger.info(f"Received Humidity Notification: {humidity_value:.2f}%")
				self._log_to_csv(humidity_value, None) # Log with None for temperature
				if PYQTGRAPH_AVAILABLE:
					update_plot(humidity_value, None) # Update plot with None for temperature
			else:
				logger.warning(f"Received unexpected data length from humidity char: {len(data)} bytes, data: {data.hex()}")
		except struct.error:
			logger.error(f"Could not unpack humidity data: {data.hex()}")
		except Exception as e:
			logger.error(f"Error in notification handler: {e}")

	async def _read_sensor_data_periodically(self): # Renamed from _read_humidity_periodically
		"""Periodically reads sensor data (humidity and temperature) characteristic."""
		if self._client is None or not self._client.is_connected or self._hand_object is None:
			logger.error("Client not connected or Hand object not initialised, cannot read sensor data.")
			return

		sensor_read_timeout = 4.5 # seconds, give ample time for device response
		while self._monitoring_active and self._client.is_connected:
			humidity_value: Optional[float] = None
			temperature_value: Optional[float] = None

			# Read Humidity
			try:
				logger.debug(f"Attempting to get relative humidity via Hand class (timeout: {sensor_read_timeout}s)...")
				humidity_value = await self._hand_object.get_relative_humidity(timeout=sensor_read_timeout)
				if humidity_value is not None:
					logger.info(f"Read Humidity: {humidity_value:.2f}%")
				else:
					logger.warning("Failed to get humidity or timed out (received None).")
			except asyncio.TimeoutError:
				logger.warning("Timeout explicitly caught from get_relative_humidity in periodic task.")
			except BleakError as e:
				logger.error(f"BleakError while getting humidity via Hand class: {e}")
			except Exception as e: # Catch other unexpected errors for humidity
				logger.error(f"Unexpected error getting humidity: {e}", exc_info=True)

			# Read Temperature
			try:
				if hasattr(self._hand_object, 'get_temperature'):
					logger.debug(f"Attempting to get temperature via Hand class (timeout: {sensor_read_timeout}s)...")
					temperature_value = await self._hand_object.get_temperature(timeout=sensor_read_timeout) # Assumed method
					if temperature_value is not None:
						logger.info(f"Read Temperature: {temperature_value:.2f}°C")
					else:
						logger.warning("Failed to get temperature or timed out (received None).")
				else:
					logger.debug("Hand object does not have 'get_temperature' method. Skipping temperature reading.")
			except asyncio.TimeoutError:
				logger.warning("Timeout explicitly caught from get_temperature in periodic task.")
			except BleakError as e:
				logger.error(f"BleakError while getting temperature via Hand class: {e}")
			except Exception as e: # Catch other unexpected errors for temperature
				logger.error(f"Unexpected error getting temperature: {e}", exc_info=True)


			# Log and Plot
			if humidity_value is not None or temperature_value is not None: # Log if at least one value
				self._log_to_csv(humidity_value, temperature_value)
				if PYQTGRAPH_AVAILABLE:
					update_plot(humidity_value, temperature_value)
			
			await asyncio.sleep(READ_INTERVAL_SECONDS)

	def _setup_csv(self):
		if not os.path.exists(CSV_OUTPUT_DIR):
			try:
				os.makedirs(CSV_OUTPUT_DIR)
			except OSError as e:
				logger.error(f"Failed to create CSV directory {CSV_OUTPUT_DIR}: {e}")
				return

		timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
		hand_id_part = self._hand_device.address.replace(":", "").replace("-","") if self._hand_device else "unknown_hand"
		filename = f"sensor_data_{hand_id_part}_{timestamp}.csv" # Changed filename
		filepath = os.path.join(CSV_OUTPUT_DIR, filename)
		
		try:
			self._csv_file = open(filepath, 'w', newline='', encoding='utf-8')
			self._csv_writer = csv.writer(self._csv_file)
			self._csv_writer.writerow(['Timestamp', 'RelativeHumidity (%)', 'Temperature (°C)']) # Added Temperature column
			logger.info(f"Saving sensor readings to: {filepath}")
		except IOError as e:
			logger.error(f"Failed to open CSV file {filepath}: {e}")
			self._csv_file = None
			self._csv_writer = None

	def _log_to_csv(self, humidity_value: Optional[float], temperature_value: Optional[float]):
		if self._csv_writer and self._csv_file:
			try:
				timestamp = datetime.datetime.now().isoformat()
				humidity_str = f"{humidity_value:.2f}" if humidity_value is not None else ""
				temp_str = f"{temperature_value:.2f}" if temperature_value is not None else ""
				self._csv_writer.writerow([timestamp, humidity_str, temp_str])
				self._csv_file.flush() 
			except IOError as e:
				logger.error(f"Error writing to CSV: {e}")

	async def start_monitoring(self):
		logger.info("Scanning for Open Bionics Hands (OB2 Hand)...")
		# Returns Dict[str, Tuple[BLEDevice, ParsedAdvertisingData, int]]
		discovered_devices = await discover_devices(timeout=10.0, device_type=DeviceType.OB2_HAND)

		if not discovered_devices:
			logger.error("No OB2 Hand devices found.")
			return False

		first_hand_address = list(discovered_devices.keys())[0]
		self._hand_device, self._hand_ad_data, rssi = discovered_devices[first_hand_address]

		logger.info(f"Found Hand: {self._hand_device.address} ({self._hand_device.name}), RSSI: {rssi}dBm")
		if self._hand_ad_data:
			 logger.info(f"  Details: Schema={self._hand_ad_data.schema_version}, "
						 f"Batt={self._hand_ad_data.battery_level}%, "
						 f"Config={self._hand_ad_data.device_config}, "
						 f"Specifics={self._hand_ad_data.device_specific_data}")
		
		self._setup_csv()

		logger.info(f"Connecting to {self._hand_device.address}...")
		try:
			self._client = BleakClient(self._hand_device)
			await self._client.connect()

			if not self._client.is_connected:
				logger.error(f"Failed to connect to {self._hand_device.address}")
				if self._csv_file: self._csv_file.close()
				return False

			logger.info("Connected successfully.")
			# hand = Hand(self._client) # MyoLink Hand object, if needed for other controls
			self._hand_object = Hand(self._client) # Initialise the Hand object

			self._monitoring_active = True
			logger.info(f"Starting humidity monitoring. Reading every {READ_INTERVAL_SECONDS}s.")
			# logger.info(f"Attempting to use Service UUID: {HUMIDITY_SERVICE_UUID}") # Not needed anymore
			# logger.info(f"Attempting to use Characteristic UUID: {HUMIDITY_CHARACTERISTIC_UUID}") # Not needed anymore
			
			# Check if characteristic supports notify
			# can_notify = False # Not directly relevant here, Hand class manages notifications
			# can_read = False # Not directly relevant here
			# service = self._client.services.get_service(HUMIDITY_SERVICE_UUID)
			# if service:
			# 	char_obj = service.get_characteristic(HUMIDITY_CHARACTERISTIC_UUID)
			# 	if char_obj:
			# 		if "notify" in char_obj.properties:
			# 			can_notify = True
			# 		if "read" in char_obj.properties:
			# 			can_read = True
			
			# The Hand class's get_relative_humidity will handle its own notification setup
			# We just need to call it periodically.
			
			# if can_notify: # Logic simplified, Hand class handles this
			# 	try:
			# 		logger.info(f"Attempting to subscribe to notifications for {HUMIDITY_CHARACTERISTIC_UUID}...")
			# 		await self._client.start_notify(HUMIDITY_CHARACTERISTIC_UUID, self._notification_handler)
			# 		logger.info("Successfully subscribed to notifications. Will rely on notifications.")
			# 		# No separate read task needed if notifications are active
			# 	except Exception as e:
			# 		logger.error(f"Could not subscribe to characteristic {HUMIDITY_CHARACTERISTIC_UUID}: {e}. Falling back to read if possible.")
			# 		can_notify = False # Disable notify path

			# if not can_notify and can_read:
			# 	logger.info("Characteristic does not support notify or subscription failed. Starting periodic read task.")
			# 	self._read_task = asyncio.create_task(self._read_humidity_periodically())
			# elif not can_read: # Neither notify nor read
			# 	logger.error(f"Characteristic {HUMIDITY_CHARACTERISTIC_UUID} does not support Notify or Read. Cannot monitor humidity.")
			# 	self._monitoring_active = False
			
			# Start the periodic read task which now uses hand.get_relative_humidity() and hand.get_temperature()
			logger.info("Starting periodic task to call sensor reading methods.")
			self._read_task = asyncio.create_task(self._read_sensor_data_periodically()) # Updated method name


			return True # Successfully connected and started monitoring (or attempted to)

		except BleakError as e:
			logger.error(f"BleakError during connection/setup: {e}")
			if self._csv_file: self._csv_file.close()
			return False
		except Exception as e:
			logger.error(f"Unexpected error during connection/setup: {e}", exc_info=True)
			if self._csv_file: self._csv_file.close()
			return False


	async def stop_monitoring(self):
		self._monitoring_active = False
		if self._read_task and not self._read_task.done():
			self._read_task.cancel()
			try:
				await self._read_task
			except asyncio.CancelledError:
				logger.info("Read task cancelled.")
		
		if self._client and self._client.is_connected:
			logger.info("Disconnecting from hand...")
			# Check if subscribed to notifications and stop them
			# This is now handled by the Hand class's internal notification management,
			# specifically, when the BleakClient disconnects, notifications should stop.
			# If explicit stop_notify was needed, it would be part of Hand class cleanup.
			# try:
			# 	service = self._client.services.get_service(HUMIDITY_SERVICE_UUID)
			# 	if service:
			# 		char_obj = service.get_characteristic(HUMIDITY_CHARACTERISTIC_UUID)
			# 		if char_obj and "notify" in char_obj.properties: # Check if notify was possible
			# 			# Check if actually notifying (simple check, could be more robust)
			# 			# For simplicity, we try to stop if it *could* have been started.
			# 			logger.debug(f"Attempting to stop notifications for {HUMIDITY_CHARACTERISTIC_UUID}")
			# 			await self._client.stop_notify(HUMIDITY_CHARACTERISTIC_UUID)
			# 			logger.info(f"Stopped notifications for {HUMIDITY_CHARACTERISTIC_UUID} (if active).")
			# except Exception as e:
			# 	logger.warning(f"Error trying to stop notifications: {e}")

			await self._client.disconnect()
			logger.info("Disconnected.")
		
		if self._csv_file:
			self._csv_file.close()
			logger.info("CSV file closed.")

async def async_main_wrapper():
	monitor = HumidityMonitor()
	
	if PYQTGRAPH_AVAILABLE:
		setup_plot() # Setup plot window (needs QApplication)

	success = await monitor.start_monitoring()

	if not success:
		logger.error("Could not start humidity monitoring.")
		# Clean up QApplication if it was created for plotting
		if PYQTGRAPH_AVAILABLE and QtWidgets.QApplication.instance():
			QtWidgets.QApplication.instance().quit()
		return

	try:
		# Keep alive while monitoring, especially if using notifications
		# The read_task itself has its own loop if used.
		# If only notifications, this loop keeps the program running.
		while monitor._monitoring_active and monitor._client and monitor._client.is_connected:
			await asyncio.sleep(0.1) # General keep-alive and event processing
			# if PYQTGRAPH_AVAILABLE and QtWidgets.QApplication.instance():
			# 	QtWidgets.QApplication.instance().processEvents() # Process Qt events - qasync should handle this
		logger.info("Monitoring loop ended (e.g. disconnected or error in setup).")

	except KeyboardInterrupt:
		logger.info("Keyboard interrupt received. Stopping...")
	finally:
		logger.info("Shutting down monitor...")
		await monitor.stop_monitoring()
		if PYQTGRAPH_AVAILABLE and QtWidgets.QApplication.instance():
			# Ensure plot widget is closed if it exists and is visible
			if plot_widget and plot_widget.isVisible():
				plot_widget.close()
			QtWidgets.QApplication.instance().quit() # Quit the Qt app
			logger.info("Qt Application instance quit.")


def main():
	if PYQTGRAPH_AVAILABLE:
		try:
			import qasync # Try to import qasync
			
			app = ensure_qapp() # Ensure QApplication is created

			loop = qasync.QEventLoop(app)
			asyncio.set_event_loop(loop)
			
			logger.info("Using qasync for Qt event loop integration.")
			
			with loop:
				loop.run_until_complete(async_main_wrapper())
			
			logger.info("qasync event loop finished.")

		except ImportError:
			logger.warning("qasync library not found. GUI might not be responsive. "
						   "Install with 'pip install qasync'. Falling back to basic asyncio run.")
			# Fallback if qasync is not available
			ensure_qasync = False
			if PYQTGRAPH_AVAILABLE: # Still ensure qapp if plotting
				ensure_qapp()

			asyncio.run(async_main_wrapper())
			
			# If not using qasync, and plot is up, we might need to manually run exec_
			# This part is tricky if asyncio.run() has already completed.
			if PYQTGRAPH_AVAILABLE and QtWidgets.QApplication.instance() and plot_widget and plot_widget.isVisible():
				logger.info("Plot window might be open. Close it to exit if qasync was not used.")
				# QtWidgets.QApplication.instance().exec_() # This might block if called after asyncio.run
	else:
		# No plotting, just run asyncio
		asyncio.run(async_main_wrapper())

if __name__ == "__main__":
	try:
		main()
	except Exception as e: # Catch-all for unexpected errors during main() setup
		logger.error(f"Critical error in main execution: {e}", exc_info=True)
	finally:
		logger.info("Application shutdown.") 