import asyncio
import sys
import numpy as np
from collections import deque
import time
from bleak import BleakClient, BleakError
from myolink.core import discover_devices
from myolink.discovery import DeviceType, Chirality
from myolink import MyoPod, EmgStreamSource, CompressionType

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets
from qasync import QEventLoop, asyncSlot

# constants
PLOT_DURATION_S = 10.0
SAMPLE_RATE_HZ = 200
BUFFER_SIZE = int(PLOT_DURATION_S * SAMPLE_RATE_HZ)

class MyoPodStreamer(QtWidgets.QWidget):
	def __init__(self):
		super().__init__()
		self.setWindowTitle("MyoPod Real-time EMG Data")
		self.resize(1000, 700)

		# --- ui elements ---
		header = QtWidgets.QHBoxLayout()
		self.colour_btn = QtWidgets.QPushButton()
		self.colour_btn.setFixedSize(16, 16)
		self.colour_btn.setStyleSheet("background-color: #0085ca; border: 1px solid #c0c0c0;")
		self.colour_btn.clicked.connect(self.on_colour_btn_clicked)
		header.addWidget(self.colour_btn)
		header.addWidget(QtWidgets.QLabel("Device:"))
		self.device_dropdown = QtWidgets.QComboBox()
		self.device_dropdown.setMinimumWidth(400)
		self.stream_type_dropdown = QtWidgets.QComboBox()
		self.compression_dropdown = QtWidgets.QComboBox()
		self.avg_samples_spin = QtWidgets.QSpinBox()
		self.avg_samples_spin.setMinimum(1)
		self.avg_samples_spin.setMaximum(1000)
		self.avg_samples_spin.setValue(100)
		self.avg_samples_label = QtWidgets.QLabel("Avg Samples:")
		self.effective_rate_label = QtWidgets.QLabel("Rate: ? Hz")
		self.native_rate_label = QtWidgets.QLabel("Native: ? Hz")
		self.conv_factor_label = QtWidgets.QLabel("Conv: ?")
		self.connect_btn = QtWidgets.QPushButton("Connect")
		self.quit_btn = QtWidgets.QPushButton("Quit")
		header.addWidget(self.device_dropdown)
		header.addWidget(self.connect_btn)
		header.addWidget(QtWidgets.QLabel("Stream:"))
		header.addWidget(self.stream_type_dropdown)
		header.addWidget(QtWidgets.QLabel("Compression:"))
		header.addWidget(self.compression_dropdown)
		header.addWidget(self.avg_samples_label)
		header.addWidget(self.avg_samples_spin)
		header.addWidget(self.effective_rate_label)
		header.addWidget(self.native_rate_label)
		header.addWidget(self.conv_factor_label)
		header.addWidget(self.quit_btn)
		header.addStretch(1)

		# populate stream type and compression dropdowns
		for st in EmgStreamSource:
			if st != EmgStreamSource.NONE:
				self.stream_type_dropdown.addItem(st.name, st)
		for ct in CompressionType:
			self.compression_dropdown.addItem(ct.name, ct)

		# --- plot area ---
		self.plot_widget = pg.GraphicsLayoutWidget()
		self.plot = self.plot_widget.addPlot(title="EMG Signal")
		self.plot.setLabel('left', "EMG Reading")
		self.plot.setLabel('bottom', "Time (s)")
		self.plot.setXRange(-PLOT_DURATION_S, 0)
		self.curve = self.plot.plot(pen='#0085ca')
		self.update_plot_title()

		# --- layout ---
		layout = QtWidgets.QVBoxLayout()
		layout.addLayout(header)
		layout.addWidget(self.plot_widget)
		self.setLayout(layout)

		# --- data ---
		self.devices = {}  # address -> (BLEDevice, parsed_ad, rssi, last_seen)
		self.current_client = None
		self.current_myopod = None
		self.current_address = None
		self.emg_data = deque(maxlen=BUFFER_SIZE)
		self.timestamps = deque(maxlen=BUFFER_SIZE)
		self.timer = QtCore.QTimer()
		self.timer.timeout.connect(self.update_plot)
		self.timer.start(50)
		self.streaming = False

		# --- device scan timer ---
		self.scan_timer = QtCore.QTimer()
		self.scan_timer.timeout.connect(lambda: asyncio.create_task(self.background_scan()))
		self.scan_timer.start(300) # scan every 300 ms

		# --- signals ---
		self.device_dropdown.currentIndexChanged.connect(self.on_device_selected)
		self.connect_btn.clicked.connect(self.on_connect_btn)
		self.quit_btn.clicked.connect(self.close)
		self.avg_samples_spin.valueChanged.connect(self.on_stream_config_ui_changed)
		self.stream_type_dropdown.currentIndexChanged.connect(self.on_stream_config_ui_changed)
		self.compression_dropdown.currentIndexChanged.connect(self.on_stream_config_ui_changed)

		# --- debounce timer for stream config ---
		self.debounce_timer = QtCore.QTimer()
		self.debounce_timer.setSingleShot(True)
		self.debounce_timer.setInterval(400)  # 400 ms debounce
		self.debounce_timer.timeout.connect(self.apply_stream_config)

		# --- connecting animation timer ---
		self.connecting_anim_timer = QtCore.QTimer()
		self.connecting_anim_timer.setInterval(300)
		self.connecting_anim_timer.timeout.connect(self.update_connecting_animation)
		self._connecting_anim_step = 0

		# --- connecting timeout timer ---
		self.connecting_timeout_timer = QtCore.QTimer()
		self.connecting_timeout_timer.setSingleShot(True)
		self.connecting_timeout_timer.setInterval(10000)  # 10 seconds
		self.connecting_timeout_timer.timeout.connect(self.handle_connecting_timeout)

		self.update_connect_btn()
		self.update_effective_rate()

	def log(self, msg):
		print(msg)

	def clear_plot(self):
		self.emg_data.clear()
		self.timestamps.clear()
		self.curve.setData([], [])

	def update_plot(self):
		if self.timestamps and 0 < len(self.timestamps):
			now = self.timestamps[-1]
			x_data = np.array([t - now for t in self.timestamps])
			y_data = np.array(self.emg_data)
			self.curve.setData(x_data, y_data)

	def emg_notification_handler(self, sender, data):
		if data:
			try:
				from myolink.myopod import MyoPod
				packet = MyoPod._parse_stream_data(data)
				if packet is not None:
					if packet.data_points:
						value = packet.data_points[0]
						current_time = time.perf_counter()
						self.emg_data.append(value)
						self.timestamps.append(current_time)
				else:
					print(f"[DEBUG] Failed to parse packet: {data.hex()}")
			except Exception as e:
				print(f"[DEBUG] Exception in notification handler: {e}")

	async def background_scan(self):
		from myolink.core import discover_devices
		from myolink.discovery import DeviceType
		try:
			discovered = await discover_devices(timeout=0.2, device_type=DeviceType.OB2_SENSOR)
			current_time = time.time()
			for address, (ble_device, parsed_ad, rssi) in discovered.items():
				if address not in self.devices:
					self.log(f"Discovered: {address} | {ble_device.name} | RSSI: {rssi}")
				self.devices[address] = (ble_device, parsed_ad, rssi, current_time)
			self.update_device_dropdown()
		except Exception as e:
			self.log(f"[DEBUG] Scan error: {e}")
		# Remove devices not seen for >6s
		to_remove = [addr for addr, (_, _, _, last_seen) in self.devices.items() if time.time() - last_seen > 6]
		for addr in to_remove:
			self.log(f"Device disappeared: {addr}")
			self.devices.pop(addr)
		self.update_device_dropdown()

	def update_device_dropdown(self):
		current = self.device_dropdown.currentData()
		self.device_dropdown.blockSignals(True)
		self.device_dropdown.clear()
		if not self.devices:
			self.device_dropdown.addItem("Scanning for MyoPods...", None)
		else:
			for address, (ble_device, parsed_ad, rssi, _) in self.devices.items():
				name = ble_device.name or "(unknown)"
				chirality = parsed_ad.device_config.chirality
				chirality_str = "Open (2 LEDs)" if Chirality.LEFT_OR_OPEN == chirality else "Close (1 LED)"
				label = f"{address} | {name} | RSSI: {rssi} | {chirality_str}"
				self.device_dropdown.addItem(label, address)
		if current:
			idx = self.device_dropdown.findData(current)
			if idx >= 0:
				self.device_dropdown.setCurrentIndex(idx)
		self.device_dropdown.blockSignals(False)
		self.update_connect_btn()

	def update_connect_btn(self):
		selected_idx = self.device_dropdown.currentIndex()
		selected_address = self.device_dropdown.itemData(selected_idx) if selected_idx >= 0 else None
		if hasattr(self, 'connecting_in_progress') and self.connecting_in_progress:
			# If connecting, keep button disabled and show animation
			return
		if self.current_myopod and self.current_myopod.is_connected:
			self.connect_btn.setText("Disconnect")
			self.connect_btn.setEnabled(True)
		elif selected_address and selected_address in self.devices:
			self.connect_btn.setText("Connect")
			self.connect_btn.setEnabled(True)
		else:
			self.connect_btn.setText("Connect")
			self.connect_btn.setEnabled(False)

	def start_connecting_animation(self):
		self.connecting_in_progress = True
		self._connecting_anim_step = 0
		self.connect_btn.setEnabled(False)
		self.connecting_anim_timer.start()
		self.connecting_timeout_timer.start()
		self.update_connecting_animation()

	def stop_connecting_animation(self):
		self.connecting_in_progress = False
		self.connecting_anim_timer.stop()
		self.connecting_timeout_timer.stop()
		self.update_connect_btn()

	def update_connecting_animation(self):
		# Animate the button label: Connecting., Connecting.., Connecting...
		steps = ["Connecting.", "Connecting..", "Connecting...", "Connecting"]
		label = steps[self._connecting_anim_step % len(steps)]
		self.connect_btn.setText(label)
		self._connecting_anim_step += 1

	def handle_connecting_timeout(self):
		self.connecting_in_progress = False
		self.connecting_anim_timer.stop()
		self.connect_btn.setText("Connect")
		self.connect_btn.setEnabled(True)
		self.log("Connection timed out. Please try again.")

	@asyncSlot(int)
	async def on_device_selected(self, idx):
		if 0 > idx or not self.device_dropdown.count():
			return
		address = self.device_dropdown.itemData(idx)
		if not address or address not in self.devices:
			return
		# If we are connected to a different device, disconnect, but do not auto-connect
		if self.current_myopod and self.current_myopod.is_connected and self.current_address != address:
			await self.disconnect_current()
		self.update_connect_btn()

	@asyncSlot()
	async def on_connect_btn(self):
		self.update_connect_btn() # Always update before starting
		if self.current_myopod and self.current_myopod.is_connected:
			await self.disconnect_current()
			self.update_connect_btn()
		else:
			idx = self.device_dropdown.currentIndex()
			if idx >= 0:
				address = self.device_dropdown.itemData(idx)
				if address:
					self.start_connecting_animation()
					await self.connect_and_stream(address)
					self.stop_connecting_animation()

	def update_effective_rate(self):
		try:
			# Try to use the last known native sample rate
			native_rate = getattr(self, 'last_native_rate', SAMPLE_RATE_HZ)
			avg_samples = self.avg_samples_spin.value()
			if avg_samples < 1:
				avg_samples = 1
			effective_rate = native_rate / avg_samples
			self.effective_rate_label.setText(f"Rate: {effective_rate:.1f} Hz")
			self.native_rate_label.setText(f"Native: {native_rate} Hz")
			conv = getattr(self, 'last_conv_factor', None)
			if conv is not None:
				self.conv_factor_label.setText(f"Conv: {conv:.4g}")
			else:
				self.conv_factor_label.setText("Conv: ?")
		except Exception:
			self.effective_rate_label.setText("Rate: ? Hz")
			self.native_rate_label.setText("Native: ? Hz")
			self.conv_factor_label.setText("Conv: ?")

	@asyncSlot()
	async def connect_and_stream(self, address):
		self.update_connect_btn()
		await self.disconnect_current()
		self.update_connect_btn()
		ble_device, parsed_ad, rssi, _ = self.devices[address]
		self.log(f"Connecting to MyoPod: {address} (Name: {ble_device.name}, RSSI: {rssi})...")
		try:
			client = BleakClient(ble_device)
			await client.connect()
			if not client.is_connected:
				self.log(f"Failed to connect to {address}")
				self.stop_connecting_animation()
				return
			self.current_client = client
			myopod = MyoPod(client)
			self.current_myopod = myopod
			self.current_address = address
			self.clear_plot()
			
			# Configure stream with optimal settings
			stream_type = self.stream_type_dropdown.currentData()
			compression = self.compression_dropdown.currentData()
			avg_samples = self.avg_samples_spin.value()
			
			# Use INT16 compression for better performance
			if compression == CompressionType.NONE:
				compression = CompressionType.INT16
				self.compression_dropdown.setCurrentText(compression.name)
			
			print(f"[DEBUG] Configuring stream: {stream_type.name}, {compression.name}, avg={avg_samples}")
			await myopod.configure_stream(
				stream_source=stream_type,
				compression=compression,
				average_samples=avg_samples
			)
			
			# Set up notification handler with minimal processing
			print(f"[DEBUG] Starting stream (subscribing to notifications)")
			await myopod.start_stream(self.emg_notification_handler)
			
			# Read and cache stream config
			try:
				stream_conf = await myopod.read_stream_configuration()
				native_rate = stream_conf.native_sample_rate_hz
				conv = stream_conf.conversion_factor
				self.last_native_rate = native_rate
				self.last_conv_factor = conv
				self.update_effective_rate()
				self.log(f"Stream config: native_rate={native_rate}Hz, conv_factor={conv}, avg_samples={stream_conf.average_samples}, stream_type={stream_conf.active_stream_source.name}, compression={stream_conf.compression_type.name}")
			except Exception as e:
				self.log(f"[DEBUG] Could not read stream config: {e}")
			
			self.streaming = True
			self.stop_connecting_animation()
			self.update_connect_btn()
			self.log("Streaming EMG data... Click 'Disconnect' to stop or select another device.")
		except Exception as e:
			self.log(f"Error connecting/streaming: {e}")
			self.stop_connecting_animation()
			self.update_connect_btn()
			await self.disconnect_current()

	@asyncSlot()
	async def disconnect_current(self):
		if self.current_myopod:
			try:
				# First stop any active stream
				if self.current_myopod.is_subscribed:
					await self.current_myopod.stop_stream()
				
				# Configure stream to NONE to ensure device stops sending data
				await self.current_myopod.configure_stream(EmgStreamSource.NONE)
				self.log("Stopped streaming and disconnected from MyoPod.")
			except Exception as e:
				self.log(f"Error during disconnect: {e}")
		self.current_myopod = None
		if self.current_client:
			try:
				await self.current_client.disconnect()
			except Exception:
				pass
		self.current_client = None
		self.current_address = None
		self.streaming = False
		self.clear_plot()
		self.update_connect_btn()
		self.last_native_rate = SAMPLE_RATE_HZ
		self.last_conv_factor = None
		self.update_effective_rate()

	def closeEvent(self, event):
		# stop all timers
		self.timer.stop()
		self.scan_timer.stop()
		self.debounce_timer.stop()
		self.connecting_anim_timer.stop()
		self.connecting_timeout_timer.stop()
		
		# create a new event loop for cleanup
		loop = asyncio.new_event_loop()
		asyncio.set_event_loop(loop)
		
		try:
			# run disconnect synchronously in the new loop
			loop.run_until_complete(self.disconnect_current())
		finally:
			# close the loop
			loop.close()
			# restore the original event loop
			asyncio.set_event_loop(None)
		
		event.accept()

	def on_stream_config_ui_changed(self):
		self.debounce_timer.start()
		self.update_plot_title()

	@asyncSlot()
	async def apply_stream_config(self):
		if self.current_myopod and self.current_myopod.is_connected and self.streaming:
			try:
				if self.current_myopod.is_subscribed:
					await self.current_myopod.stop_stream()
				
				stream_type = self.stream_type_dropdown.currentData()
				compression = self.compression_dropdown.currentData()
				avg_samples = self.avg_samples_spin.value()
				
				# Use INT16 compression for better performance
				if compression == CompressionType.NONE:
					compression = CompressionType.INT16
					self.compression_dropdown.setCurrentText(compression.name)
				
				await self.current_myopod.configure_stream(
					stream_source=stream_type,
					compression=compression,
					average_samples=avg_samples
				)
				
				await self.current_myopod.start_stream(self.emg_notification_handler)
				
				try:
					stream_conf = await self.current_myopod.read_stream_configuration()
					native_rate = stream_conf.native_sample_rate_hz
					conv = stream_conf.conversion_factor
					self.last_native_rate = native_rate
					self.last_conv_factor = conv
					self.update_effective_rate()
					self.log(f"Stream config updated: native_rate={native_rate}Hz, conv_factor={conv}, avg_samples={stream_conf.average_samples}, stream_type={stream_conf.active_stream_source.name}, compression={stream_conf.compression_type.name}")
				except Exception as e:
					self.log(f"[DEBUG] Could not read stream config after update: {e}")
			except Exception as e:
				self.log(f"[DEBUG] Failed to re-configure stream: {e}")

	def on_colour_btn_clicked(self):
		# get current colour from the button's style sheet
		current_colour = self.colour_btn.styleSheet().split(':')[1].split(';')[0].strip()
		colour = QtWidgets.QColorDialog.getColor(QtWidgets.QColor(current_colour))
		if colour.isValid():
			self.colour_btn.setStyleSheet(f"background-color: {colour.name()}; border: 1px solid #c0c0c0;")
			self.curve.setPen(colour.name())

	def format_stream_name(self, stream_type):
		# convert stream type name to title case and replace underscores with spaces
		return stream_type.name.replace('_', ' ').title()

	def update_plot_title(self):
		# update plot title based on selected stream type
		if self.stream_type_dropdown.count() > 0:
			stream_type = self.stream_type_dropdown.currentData()
			if stream_type:
				title = self.format_stream_name(stream_type)
				self.plot.setTitle(title)
			else:
				self.plot.setTitle("EMG Signal")
		else:
			self.plot.setTitle("EMG Signal")

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