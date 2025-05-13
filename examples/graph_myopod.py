import asyncio
import sys
import numpy as np
from collections import deque
import time
from bleak import BleakClient, BleakError
from myolink.core import discover_devices
from myolink.discovery import DeviceType, Chirality
from myolink import MyoPod, EmgStreamSource, CompressionType
from myolink.myopod import StreamDataPacket

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets
from qasync import QEventLoop, asyncSlot
import functools

# constants
PLOT_DURATION_S = 10.0
SAMPLE_RATE_HZ = 200
BUFFER_SIZE = int(PLOT_DURATION_S * SAMPLE_RATE_HZ * 1.5)
MAX_DEVICES = 4 # Max devices to connect simultaneously
# Revert to explicit colour list
PLOT_COLOURS = ['#0085ca', '#ff991b', '#7ac943', '#8a3ffc'] # Blue, Orange, Green, Purple

class MyoPodStreamer(QtWidgets.QWidget):
	def __init__(self):
		super().__init__()
		self.setWindowTitle("MyoPod Real-time EMG Data")
		self.resize(1200, 700)

		# --- Main Layout (Sidebar + Plot) ---
		main_layout = QtWidgets.QHBoxLayout(self)
		sidebar_layout = QtWidgets.QVBoxLayout()
		sidebar_widget = QtWidgets.QWidget()
		sidebar_widget.setLayout(sidebar_layout)
		sidebar_widget.setMaximumWidth(400)

		# --- Sidebar Widgets (Actual Implementation) ---
		# Device List
		sidebar_layout.addWidget(QtWidgets.QLabel("Discovered Devices:"))
		self.device_list_widget = QtWidgets.QListWidget()
		sidebar_layout.addWidget(self.device_list_widget)

		# Connection Button
		self.connect_selected_btn = QtWidgets.QPushButton("Connect Selected")
		sidebar_layout.addWidget(self.connect_selected_btn)

		sidebar_layout.addSpacing(20)

		# Stream Configuration (Single Global Set)
		sidebar_layout.addWidget(QtWidgets.QLabel("Global Stream Configuration:"))
		config_layout = QtWidgets.QGridLayout()
		self.stream_type_dropdown = QtWidgets.QComboBox()
		self.compression_dropdown = QtWidgets.QComboBox()
		self.avg_samples_spin = QtWidgets.QSpinBox()
		self.avg_samples_spin.setMinimum(1)
		self.avg_samples_spin.setMaximum(1000)
		self.avg_samples_spin.setValue(100) # Default to 100
		self.apply_config_btn = QtWidgets.QPushButton("Apply")
		config_layout.addWidget(QtWidgets.QLabel("Stream:"), 0, 0)
		config_layout.addWidget(self.stream_type_dropdown, 0, 1)
		config_layout.addWidget(QtWidgets.QLabel("Comp:"), 1, 0)
		config_layout.addWidget(self.compression_dropdown, 1, 1)
		config_layout.addWidget(QtWidgets.QLabel("Avg Samples:"), 2, 0)
		config_layout.addWidget(self.avg_samples_spin, 2, 1)
		sidebar_layout.addLayout(config_layout)
		sidebar_layout.addWidget(self.apply_config_btn)

		sidebar_layout.addStretch(1) # Push Quit button to bottom
		self.quit_btn = QtWidgets.QPushButton("Quit")
		sidebar_layout.addWidget(self.quit_btn)

		# --- Populate global config dropdowns ---
		for st in EmgStreamSource:
			if st != EmgStreamSource.NONE:
				self.stream_type_dropdown.addItem(st.name, st)
		for ct in CompressionType:
			self.compression_dropdown.addItem(ct.name, ct)
		# Default compression to INT16
		default_comp_idx = self.compression_dropdown.findData(CompressionType.INT16)
		if default_comp_idx >= 0:
			self.compression_dropdown.setCurrentIndex(default_comp_idx)
		# Default stream to PROCESSED_EMG
		default_stream_idx = self.stream_type_dropdown.findData(EmgStreamSource.PROCESSED_EMG)
		if default_stream_idx >= 0:
			self.stream_type_dropdown.setCurrentIndex(default_stream_idx)

		# --- plot area (Keep for now) ---
		self.plot_widget = pg.GraphicsLayoutWidget()
		self.plot = self.plot_widget.addPlot(title="EMG Signal")
		self.plot.setLabel('left', "EMG Reading")
		self.plot.setLabel('bottom', "Time (s)")
		self.plot.setXRange(-PLOT_DURATION_S, 0)
		# Enable performance optimisations
		self.plot.setClipToView(True) # Clip data to visible range
		self.plot.setDownsampling(mode='peak') # Enable downsampling
		# Curves will be managed dynamically later

		# Add right y-axis for converted values (Keep for now, needs rework)
		self.right_axis = pg.AxisItem('right')
		# Remove redundant manual addition - showAxis handles it.
		# self.plot.layout.addItem(self.right_axis, 2, 2)
		self.plot.showAxis('right')
		self.plot.getAxis('right').setStyle(showValues=True)
		self.plot_widget.ci.layout.setColumnFixedWidth(2, 15)

		# --- Add sidebar and plot to main layout ---
		main_layout.addWidget(sidebar_widget)
		main_layout.addWidget(self.plot_widget, 1)

		# --- Data Structures (Revised) ---
		self.discovered_devices = {} # address -> (BLEDevice, parsed_ad, rssi, last_seen)
		self.connected_devices = {} # address -> dict {client, myopod, data_deque, timestamp_deque, curve, colour, native_rate, conv_factor, streaming, notification_queue}

		# --- Timers (Keep relevant ones) ---
		self.plot_timer = QtCore.QTimer()
		self.plot_timer.timeout.connect(self.update_plot)
		self.plot_timer.start(100)	# update frequency (100Hz)

		self.scan_timer = QtCore.QTimer()
		self.scan_timer.timeout.connect(lambda: asyncio.create_task(self.background_scan()))
		self.scan_timer.start(300)

		# --- signals (Connect new widgets) ---
		self.quit_btn.clicked.connect(self.close)
		self.connect_selected_btn.clicked.connect(self.on_connect_selected_btn_wrapper)
		self.apply_config_btn.clicked.connect(lambda: asyncio.create_task(self.apply_global_stream_config()))
		self.device_list_widget.itemSelectionChanged.connect(self.update_connect_btn_text)

		# Initial UI state update
		self.update_device_list()

	def log(self, msg):
		print(msg)

	def update_plot(self):
		"""Updates all active plot curves with new data from the queues."""
		# self.log("[DEBUG] update_plot called") # Optional high-frequency log
		for address, device_info in list(self.connected_devices.items()):
			notification_queue = device_info.get('notification_queue')
			data_deque = device_info.get('data_deque')
			timestamp_deque = device_info.get('timestamp_deque')
			curve = device_info.get('curve')

			if not notification_queue or not data_deque or not timestamp_deque or not curve:
				continue

			# Process all pending notifications from the queue for this device
			packets_processed = 0
			# self.log(f"[DEBUG] Checking queue for {address}") # Optional high-frequency log
			while not notification_queue.empty():
				try:
					packet: StreamDataPacket = notification_queue.get_nowait()
					if packet and packet.data_points:
						current_time = time.perf_counter()
						num_points = len(packet.data_points)
						data_deque.extend(packet.data_points)
						timestamp_deque.extend([current_time] * num_points)
						packets_processed += 1
				except asyncio.QueueEmpty:
					break
				except Exception as e:
					self.log(f"[ERROR] Error processing packet from queue for {address}: {e}")
					break

			if packets_processed > 0:
				self.log(f"[DEBUG] Processed {packets_processed} packets from queue for {address}")

			# Update plot only if data exists
			# self.log(f"[DEBUG] Deque size for {address} before plot: {len(data_deque)}") # Optional high-frequency log
			if data_deque:
				last_ts = timestamp_deque[-1]
				x_data = [t - last_ts for t in timestamp_deque]
				y_data = list(data_deque)
				curve.setData(x_data, y_data)
			elif curve:
				curve.setData([], [])

	async def background_scan(self):
		"""Scans for devices and updates the shared discovery pool."""
		try:
			discovered = await discover_devices(timeout=0.2, device_type=DeviceType.OB2_SENSOR)
			current_time = time.time()
			newly_discovered = False
			disappeared = False
			
			# Keep track of addresses seen in this scan
			seen_in_scan = set(discovered.keys())

			# Update the shared discovery pool with newly found or updated devices
			for address, (ble_device, parsed_ad, rssi) in discovered.items():
				if address not in self.discovered_devices:
					self.log(f"Discovered: {address} | {ble_device.name} | RSSI: {rssi}")
					newly_discovered = True
				# Store device info along with last seen time
				self.discovered_devices[address] = (ble_device, parsed_ad, rssi, current_time)
				
			# Remove devices from discovered_devices if not seen for >6s AND not connected
			to_remove_from_discovered = []
			for addr, (_, _, _, last_seen) in self.discovered_devices.items():
				if current_time - last_seen > 6 and addr not in self.connected_devices:
					to_remove_from_discovered.append(addr)
					
			for addr in to_remove_from_discovered:
				if addr in self.discovered_devices:
					self.log(f"Device {addr} disappeared from scan (and not connected). Removing from discovered list.")
					self.discovered_devices.pop(addr)
					disappeared = True # Flag that a device was removed
			
			# Update list widget if there was a discovery or disappearance of non-connected devices
			# Also update if connection status might have changed (handled implicitly by update_device_list checking self.connected_devices)
			# We need to check if any *visible* device info changed (RSSI, name etc.) or if the set of known devices changed.
			# Let's simplify and just update if anything changed or connections exist.
			if newly_discovered or disappeared or self.connected_devices:
				self.update_device_list()
			
		except Exception as e:
			self.log(f"[DEBUG] Scan error: {e}")

	def update_device_list(self):
		"""Populates the list widget with discovered devices and checkboxes."""
		self.device_list_widget.blockSignals(True) # Block signals during update
		self.device_list_widget.clear()

		# Combine addresses from discovered and connected devices
		all_known_addresses = set(self.discovered_devices.keys()) | set(self.connected_devices.keys())

		if not all_known_addresses:
			item = QtWidgets.QListWidgetItem("Scanning...")
			item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsUserCheckable) # Not checkable
			self.device_list_widget.addItem(item)
		else:
			# Sort devices by address for consistent order
			sorted_addresses = sorted(list(all_known_addresses))
			for address in sorted_addresses:
				is_connected = address in self.connected_devices
				is_discovered = address in self.discovered_devices
				
				label = f"{address}"
				tooltip_text = f"Address: {address}"
				name = "(unknown)"
				chirality_str = "?"
				rssi = "N/A"
				battery = None
				
				# Get info preferably from discovered_devices if available
				if is_discovered:
					ble_device, parsed_ad, rssi_val, _ = self.discovered_devices[address]
					name = ble_device.name or "(unknown)"
					rssi = f"{rssi_val} dBm"
					if parsed_ad:
						chirality = parsed_ad.device_config.chirality
						chirality_str = "Open" if Chirality.LEFT_OR_OPEN == chirality else "Close"
						if hasattr(parsed_ad, 'battery_level'):
							battery = parsed_ad.battery_level
				elif is_connected:
					# If connected but not discovered, use minimal info
					label += " | (Connected, not advertising)"
				else:
					# Should not happen with combined set logic, but skip if it does
					continue 

				# Update label and tooltip with discovered info if available
				label = f"{address} | {name} | {chirality_str}"
				tooltip_text = f"Address: {address}\nName: {name}\nType: Sensor ({chirality_str})\nRSSI: {rssi}"
				if battery is not None:
					tooltip_text += f"\nBattery: {battery}%"
				
				# Add status to tooltip
				if is_connected:
					status = "Connected"
					if not is_discovered:
						status += " (Not Advertising)"
						# Maybe dim the text or change icon slightly?
					tooltip_text += f"\nStatus: {status}"
				elif is_discovered:
					tooltip_text += "\nStatus: Discovered"
				# else: # Should be unreachable
				# 	tooltip_text += "\nStatus: Unknown"

				item = QtWidgets.QListWidgetItem(label)
				item.setData(QtCore.Qt.ItemDataRole.UserRole, address) # Store address
				item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable) # Make checkable
				item.setToolTip(tooltip_text) # Set the tooltip
				
				# Set check state based only on connection status now
				if is_connected:
					item.setCheckState(QtCore.Qt.CheckState.Checked)
					# Change text colour to match plot
					colour_str = self.connected_devices[address].get('colour', '#000000') # Default black if colour missing
					try:
						item.setForeground(pg.mkColor(colour_str)) # Use pg.mkColor for convenience
					except Exception as e:
						self.log(f"[WARN] Failed to set text color for {address}: {e}")
						item.setForeground(pg.mkColor('k')) # Fallback to black
				else:
					item.setCheckState(QtCore.Qt.CheckState.Unchecked)
					# Ensure default text colour if not connected
					item.setForeground(pg.mkColor('k')) # Use pg.mkColor for black
					
				# Bold text if connected
				if is_connected:
					font = item.font()
					font.setBold(True)
					item.setFont(font)
					
				self.device_list_widget.addItem(item)
		
		self.device_list_widget.blockSignals(False) # Re-enable signals

		# After updating the list, update the connect/disconnect button text
		self.update_connect_btn_text()

		# If nothing is selected, auto-select the first checked device (if any)
		if not self.device_list_widget.selectedItems():
			for i in range(self.device_list_widget.count()):
				item = self.device_list_widget.item(i)
				if item.checkState() == QtCore.Qt.CheckState.Checked:
					self.device_list_widget.setCurrentItem(item)
					break

	def update_connect_btn_text(self):
		"""Updates the connect/disconnect button text based on checked state of devices."""
		checked_addresses = set()
		for i in range(self.device_list_widget.count()):
			item = self.device_list_widget.item(i)
			if item.checkState() == QtCore.Qt.CheckState.Checked:
				address = item.data(QtCore.Qt.ItemDataRole.UserRole)
				if address:
					checked_addresses.add(address)
		if not checked_addresses:
			self.connect_selected_btn.setText("Connect Selected")
			return
		all_connected = all(addr in self.connected_devices for addr in checked_addresses)
		if all_connected:
			self.connect_selected_btn.setText("Disconnect")
		else:
			self.connect_selected_btn.setText("Connect Selected")

	def on_connect_selected_btn_wrapper(self):
		asyncio.create_task(self.on_connect_selected_btn())

	async def on_connect_selected_btn(self):
		"""Connects or disconnects all checked devices as appropriate."""
		checked_addresses = []
		for i in range(self.device_list_widget.count()):
			item = self.device_list_widget.item(i)
			if item.checkState() == QtCore.Qt.CheckState.Checked:
				address = item.data(QtCore.Qt.ItemDataRole.UserRole)
				if address:
					checked_addresses.append(address)
		if not checked_addresses:
			self.log("No device selected.")
			return
		# If all checked are connected, disconnect them
		if all(addr in self.connected_devices for addr in checked_addresses):
			self.log(f"Disconnecting devices: {checked_addresses}")
			disconnect_tasks = [self.disconnect_device(addr) for addr in checked_addresses]
			await asyncio.gather(*disconnect_tasks, return_exceptions=True)
		else:
			# Connect all checked that are not connected, up to max
			to_connect = [addr for addr in checked_addresses if addr not in self.connected_devices]
			available_slots = MAX_DEVICES - len(self.connected_devices)
			if available_slots <= 0:
				self.log(f"Cannot connect more devices: Max devices ({MAX_DEVICES}) already connected.")
				return
			to_connect = to_connect[:available_slots]
			self.log(f"Connecting devices: {to_connect}")
			connect_tasks = [self.connect_device(addr) for addr in to_connect]
			await asyncio.gather(*connect_tasks, return_exceptions=True)
		self.update_device_list()

	async def connect_device(self, address):
		"""Connects to a single device, sets up stream, plot curve, and notification queue."""
		if address not in self.discovered_devices:
			self.log(f"Error connecting to {address}: Not found in discovered devices.")
			return
		if address in self.connected_devices:
			self.log(f"Error connecting to {address}: Already connected.")
			return
		if len(self.connected_devices) >= MAX_DEVICES:
			self.log(f"Error connecting to {address}: Max devices ({MAX_DEVICES}) already connected.")
			return

		ble_device, _, _, _ = self.discovered_devices[address]
		self.log(f"Connecting to {address}...")

		# --- Stop scanning timer if this is the first connection ---
		if not self.connected_devices: # Check before adding the new device
			self.log("First device connecting, stopping background scan.")
			self.scan_timer.stop()

		try:
			client = BleakClient(ble_device)
			await client.connect()
			if not client.is_connected:
				self.log(f"Failed to connect to {address}")
				# Restart scanning if connection failed and no other devices are connected
				if not self.connected_devices:
					self.log("Connection failed, restarting background scan.")
					self.scan_timer.start(300)
				return

			myopod = MyoPod(client)

			# --- Assign Plot Curve and Colour ---
			assigned_colour = PLOT_COLOURS[len(self.connected_devices) % len(PLOT_COLOURS)]
			# Create pen directly from hex string
			curve = self.plot.plot(pen=assigned_colour)
			self.log(f"Assigned colour {assigned_colour} to {address}")

			# --- Create Notification Queue ---
			notification_queue = asyncio.Queue()

			# --- Store Connection Info (including queue) ---
			device_info = {
				'client': client,
				'myopod': myopod,
				'data_deque': deque(maxlen=BUFFER_SIZE),
				'timestamp_deque': deque(maxlen=BUFFER_SIZE),
				'curve': curve,
				'colour': assigned_colour,
				'native_rate': SAMPLE_RATE_HZ, # Placeholder, updated later
				'conv_factor': None,
				'streaming': False,
				'notification_queue': notification_queue # Add the queue
			}
			self.connected_devices[address] = device_info

			# --- Apply Global Stream Config ---
			# Read global config from UI
			stream_type = self.stream_type_dropdown.currentData()
			compression = self.compression_dropdown.currentData()
			avg_samples = self.avg_samples_spin.value()

			self.log(f"[{address}] Configuring stream: {stream_type.name}, {compression.name}, avg={avg_samples}")
			await myopod.configure_stream(
				stream_source=stream_type,
				compression=compression,
				average_samples=avg_samples
			)

			# --- Start Stream Notifications ---
			# Handler now just puts packet onto the queue
			bound_handler = functools.partial(self.notification_handler, address)
			self.log(f"[{address}] Starting stream subscription...")
			await myopod.start_stream(bound_handler)
			self.connected_devices[address]['streaming'] = True # Mark as streaming

			# --- Read Initial Config from Device ---
			try:
				stream_conf = await myopod.read_stream_configuration()
				self.connected_devices[address]['native_rate'] = stream_conf.native_sample_rate_hz
				self.connected_devices[address]['conv_factor'] = stream_conf.conversion_factor
				self.log(f"[{address}] Config read: native={stream_conf.native_sample_rate_hz}Hz, conv={stream_conf.conversion_factor:.4g}")
			except Exception as e:
				self.log(f"[{address}] Failed to read stream config after connect: {e}")

			self.log(f"Successfully connected and streaming from {address}")

		except BleakError as e:
			self.log(f"Connection to {address} failed (BleakError): {e}")
			# Ensure cleanup and potentially restart scanning
			await self.handle_connection_failure(address)
		except Exception as e:
			self.log(f"Error during connection to {address}: {e}")
			# Ensure cleanup and potentially restart scanning
			await self.handle_connection_failure(address)
		finally:
			self.update_device_list()

	async def handle_connection_failure(self, address):
		"""Handles cleanup after a connection attempt fails."""
		# Device might have been partially added to connected_devices if error occurred late
		if address in self.connected_devices:
			await self.disconnect_device(address) # Use existing disconnect logic for cleanup
		else:
			# If it wasn't even added, just check if scanning needs restarting
			if not self.connected_devices and not self.scan_timer.isActive():
				self.log("Connection failed, restarting background scan.")
				self.scan_timer.start(300)

	async def disconnect_device(self, address):
		"""Disconnects a single device and cleans up resources."""
		if address not in self.connected_devices:
			self.log(f"Cannot disconnect {address}: Not currently connected.")
			return

		self.log(f"Disconnecting {address}...")
		device_info = self.connected_devices.pop(address) # Remove from dict immediately
		myopod = device_info.get('myopod')
		client = device_info.get('client')
		curve = device_info.get('curve')

		# Remove plot curve
		if curve:
			try:
				self.plot.removeItem(curve)
			except Exception as e:
				self.log(f"[{address}] Error removing plot curve: {e}")

		# Stop stream and disconnect client
		if myopod:
			try:
				if myopod.is_subscribed:
					await myopod.stop_stream()
			except Exception as e:
				self.log(f"[{address}] Error stopping stream during disconnect: {e}")
			# Clear MyoPod object
			myopod = None # Redundant as we popped dict entry, but safe

		if client:
			try:
				if client.is_connected:
					await client.disconnect()
			except Exception as e:
				self.log(f"[{address}] Error disconnecting client: {e}")
			# Clear client object
			client = None

		self.log(f"Disconnect of {address} complete.")
		# Restart scanning timer if this was the last connected device
		if not self.connected_devices and not self.scan_timer.isActive():
			self.log("Last device disconnected, restarting background scan.")
			self.scan_timer.start(300)

		# Update UI list after disconnect is fully processed
		self.update_device_list()

	def notification_handler(self, address, packet: StreamDataPacket):
		"""Minimal handler: Puts received packet onto the device's queue."""
		try:
			if address in self.connected_devices:
				queue = self.connected_devices[address].get('notification_queue')
				if queue:
					queue.put_nowait(packet)
					# self.log(f"[DEBUG] Queued packet for {address} (Points: {len(packet.data_points) if packet else 0})") # Optional high-frequency log
		except asyncio.QueueFull:
			self.log(f"[WARN] Notification queue full for {address}. Discarding packet.")
		except Exception as e:
			self.log(f"[ERROR] Exception in notification_handler for {address}: {e}")

	async def apply_global_stream_config(self):
		"""Applies the global stream configuration to all connected devices sequentially."""
		stream_type = self.stream_type_dropdown.currentData()
		compression = self.compression_dropdown.currentData()
		avg_samples = self.avg_samples_spin.value()
		self.log(f"Applying global config sequentially: Type={stream_type.name}, Comp={compression.name}, Avg={avg_samples}")

		# Make a copy of addresses to iterate over, as applying config might modify the dict indirectly
		connected_addresses = list(self.connected_devices.keys())
		
		if not connected_addresses:
			self.log("No connected devices to apply config to.")
			return

		self.log(f"Applying config to {len(connected_addresses)} device(s)...")
		for address in connected_addresses:
			if address in self.connected_devices: # Check if still connected
				device_info = self.connected_devices[address]
				myopod = device_info.get('myopod')
				if myopod and myopod.is_connected:
					self.log(f"Applying config to {address}...")
					await self._apply_config_to_single_device(address, myopod, stream_type, compression, avg_samples)
					await asyncio.sleep(0.1) # Small delay between devices
				else:
					self.log(f"[{address}] Skipping config apply, not connected.")
			else:
				self.log(f"[{address}] Skipping config apply, disconnected during process.")
		
		self.log("Global config application finished.")

	async def _apply_config_to_single_device(self, address, myopod: MyoPod, stream_type: EmgStreamSource, compression: CompressionType, avg_samples: int):
		"""Helper to apply configuration and restart stream for one device."""
		try:
			# Stop stream if running
			if myopod.is_subscribed:
				await myopod.stop_stream()
				self.connected_devices[address]['streaming'] = False
			
			# Clear plot data for this device
			self.clear_plot_data(address)
			
			# Configure stream on device
			self.log(f"[{address}] Applying config: {stream_type.name}, {compression.name}, avg={avg_samples}")
			await myopod.configure_stream(
				stream_source=stream_type,
				compression=compression,
				average_samples=avg_samples
			)
			
			# Re-start stream subscription
			bound_handler = functools.partial(self.notification_handler, address)
			await myopod.start_stream(bound_handler)
			self.connected_devices[address]['streaming'] = True
			
			# Re-read config to update local state (native rate, conv factor)
			try:
				stream_conf = await myopod.read_stream_configuration()
				self.connected_devices[address]['native_rate'] = stream_conf.native_sample_rate_hz
				self.connected_devices[address]['conv_factor'] = stream_conf.conversion_factor
				self.log(f"[{address}] Config updated & re-read: native={stream_conf.native_sample_rate_hz}Hz, conv={stream_conf.conversion_factor:.4g}")
			except Exception as e:
				self.log(f"[{address}] Failed to re-read stream config after update: {e}")
				
		except Exception as e:
			self.log(f"[{address}] Failed to apply stream config: {e}")
			# Attempt to disconnect if config fails badly?
			# await self.disconnect_device(address)

	def clear_plot_data(self, address):
		"""Clears plot data deques for a specific device."""
		if address in self.connected_devices:
			self.connected_devices[address]['data_deque'].clear()
			self.connected_devices[address]['timestamp_deque'].clear()
			curve = self.connected_devices[address].get('curve')
			if curve:
				curve.setData([], []) # Also clear the visual curve
		else:
			self.log(f"[WARN] Cannot clear plot data for unknown address: {address}")

	def closeEvent(self, event):
		"""Ensures proper cleanup of timers and connections on exit."""
		self.log("Close event triggered. Stopping timers and disconnecting...")
		# Stop all timers first
		self.plot_timer.stop()
		self.scan_timer.stop()
		# Stop any other timers if they existed

		# Get the existing qasync event loop
		loop = asyncio.get_event_loop()

		async def cleanup():
			# Get addresses before iterating as disconnect modifies the dict
			addresses_to_disconnect = list(self.connected_devices.keys())
			if not addresses_to_disconnect:
				self.log("No devices were connected.")
				return
				
			self.log(f"Disconnecting {len(addresses_to_disconnect)} device(s) sequentially: {addresses_to_disconnect}")
			# Disconnect sequentially
			for addr in addresses_to_disconnect:
				self.log(f"Disconnecting {addr}...")
				try:
					await self.disconnect_device(addr)
					await asyncio.sleep(0.1) # Small delay between disconnects
				except Exception as e:
					self.log(f"Error during sequential disconnect of {addr}: {e}")
			
			self.log("Disconnect tasks finished.")

		# Run the cleanup within the existing loop if it's running
		if loop.is_running():
			self.log("Event loop is running, scheduling cleanup.")
			cleanup_future = asyncio.ensure_future(cleanup(), loop=loop)
			
			# Add a callback for logging/errors
			def _cleanup_done(task):
				try:
					task.result() # Check for exceptions during cleanup
					self.log("Async cleanup task completed successfully.")
				except Exception as e:
					self.log(f"Error during async cleanup task: {e}")
				# finally: # Avoid stopping the main GUI loop here
				# 	if loop.is_running():
				# 		self.log("Stopping event loop after cleanup.")
				# 		loop.stop()
			
			cleanup_future.add_done_callback(_cleanup_done)
		else:
			self.log("Event loop not running, attempting synchronous cleanup.")
			# Attempt synchronous cleanup if loop isn't running (less ideal)
			try:
				sync_loop = asyncio.new_event_loop()
				asyncio.set_event_loop(sync_loop)
				sync_loop.run_until_complete(cleanup())
				sync_loop.close()
				asyncio.set_event_loop(loop) # Restore original loop context if needed
			except RuntimeError as e:
				self.log(f"Error during synchronous cleanup attempt: {e}")

		self.log("Accepting close event.")
		event.accept()

if __name__ == "__main__":
	print("Starting application...")
	app = QtWidgets.QApplication(sys.argv)
	loop = QEventLoop(app)
	asyncio.set_event_loop(loop)
	window = MyoPodStreamer()
	window.show()
	with loop:
		loop.run_forever()
	print("Application finished.") 