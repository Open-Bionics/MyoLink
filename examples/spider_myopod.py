import sys
import time
import asyncio
import functools
from collections import deque
from typing import Dict, List
import json
import os.path

import numpy as np
from PyQt6 import QtWidgets, QtCore, QtGui
from qasync import QEventLoop

from bleak import BleakClient, BleakError
from myolink.core import discover_devices
from myolink.discovery import DeviceType, Chirality
from myolink import MyoPod, EmgStreamSource, CompressionType
from myolink.myopod import StreamDataPacket

# Constants
MAX_DEVICES = 8  # Maximum number of supported devices
SAMPLE_RATE_HZ = 500  # Default sample rate
BUFFER_SIZE = 100  # Smaller buffer as we only need recent values
UPDATE_RATE_HZ = 30  # Spider graph update rate

class SpiderCanvas(QtWidgets.QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setMinimumSize(400, 400)
        self.points = {}  # address -> magnitude
        self.colors = {}  # address -> QColor
        self.device_names = {}  # address -> name
        self.device_angles = {}  # address -> angle in degrees
        self.max_magnitude = 1.0  # Auto-scales with data
        self.actions = []  # Initialize empty actions list
        
        # Drag state
        self.dragging_device = None
        self.setMouseTracking(True)  # Enable mouse tracking for hover effects

    def set_actions(self, actions):
        """Update the action definitions."""
        self.actions = actions
        self.update()

    def get_center_and_radius(self):
        """Helper to get canvas center and radius."""
        w, h = self.width(), self.height()
        center = QtCore.QPointF(w/2, h/2)
        radius = min(w, h) * 0.4
        return center, radius
        
    def get_device_at_pos(self, pos):
        """Returns device address if pos is near a device label."""
        center, radius = self.get_center_and_radius()
        label_radius = radius * 1.1  # Labels are drawn at 1.1 * radius
        
        # Convert QPoint to QPointF
        pos = QtCore.QPointF(pos.x(), pos.y())
        
        for addr, angle in self.device_angles.items():
            # Calculate label position
            label_x = center.x() + label_radius * np.cos(np.radians(angle))
            label_y = center.y() - label_radius * np.sin(np.radians(angle))
            label_pos = QtCore.QPointF(label_x, label_y)
            
            # Check if mouse is within 20 pixels of label position
            if (pos - label_pos).manhattanLength() < 20:
                return addr
        return None

    def angle_from_pos(self, pos):
        """Calculate angle in degrees from mouse position."""
        center, _ = self.get_center_and_radius()
        # Convert QPoint to float coordinates
        dx = float(pos.x()) - center.x()
        dy = center.y() - float(pos.y())  # Inverted Y axis
        angle = np.degrees(np.arctan2(dy, dx))
        return angle % 360  # Normalize to 0-360

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.dragging_device = self.get_device_at_pos(event.pos())
            if self.dragging_device:
                self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self.dragging_device:
            self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
            self.dragging_device = None

    def mouseMoveEvent(self, event):
        if self.dragging_device:
            # Update angle for dragged device
            new_angle = self.angle_from_pos(event.pos())
            self.device_angles[self.dragging_device] = new_angle
            # Update the connected device info using main_window reference
            self.main_window.update_device_angle(self.dragging_device, new_angle)
            self.update()
        else:
            # Show hand cursor when hovering over a device label
            if self.get_device_at_pos(event.pos()):
                self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    def calculate_vector_sum(self):
        """Calculate the vector sum of all EMG signals."""
        x_sum = 0
        y_sum = 0
        
        for addr in self.device_angles:
            if addr in self.points:
                magnitude = self.points[addr] / self.max_magnitude
                magnitude = min(magnitude, 1.0)  # Clip to max radius
                angle = np.radians(self.device_angles[addr])
                
                # Add vector components
                x_sum += magnitude * np.cos(angle)
                y_sum += magnitude * np.sin(angle)
        
        # Calculate resultant magnitude and angle
        magnitude = np.sqrt(x_sum**2 + y_sum**2)
        angle = np.degrees(np.arctan2(y_sum, x_sum))
        
        return magnitude, angle

    def calculate_active_actions(self, sum_magnitude, sum_angle):
        """Calculate which actions are currently active based on vector sum alignment."""
        active_actions = {}  # action_name -> activation_level
        
        for action in self.actions:
            start = action['start_angle']
            end = action['end_angle']
            
            # Handle wrap-around for regions that cross 0/360
            if end < start:
                # If sum_angle is in [start, 360] or [0, end]
                if sum_angle >= start or sum_angle <= end:
                    active_actions[action['name']] = sum_magnitude
            else:
                # Normal case: if sum_angle is in [start, end]
                if start <= sum_angle <= end:
                    active_actions[action['name']] = sum_magnitude
        
        return active_actions

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        # Calculate center and radius
        w, h = self.width(), self.height()
        center = QtCore.QPointF(w/2, h/2)
        radius = min(w, h) * 0.4  # Leave margin

        # Draw circular guides
        painter.setPen(QtGui.QPen(QtGui.QColor(200, 200, 200), 1))
        for i in range(1, 6):  # 5 concentric circles
            r = radius * i/5
            painter.drawEllipse(center, r, r)

        # Draw radial lines and labels for each device
        points = []  # Store points for connecting lines
        for addr in self.device_angles:
            angle = self.device_angles[addr]
            # Draw radial line
            end_x = center.x() + radius * np.cos(np.radians(angle))
            end_y = center.y() - radius * np.sin(np.radians(angle))
            painter.setPen(QtGui.QPen(QtGui.QColor(200, 200, 200), 1))
            painter.drawLine(center, QtCore.QPointF(end_x, end_y))
            
            # Draw device label
            label_x = center.x() + radius * 1.1 * np.cos(np.radians(angle))
            label_y = center.y() - radius * 1.1 * np.sin(np.radians(angle))
            device_name = self.device_names.get(addr, "Unknown Device")
            user_label = self.main_window.get_device_label(addr)
            display_text = user_label if user_label else device_name
            
            # Center the text
            text_rect = painter.fontMetrics().boundingRect(display_text)
            text_pos = QtCore.QPointF(
                label_x - text_rect.width()/2,
                label_y + text_rect.height()/4  # Adjust vertical position
            )
            painter.drawText(text_pos, display_text)

            # Draw data point if available
            if addr in self.points and addr in self.colors:
                magnitude = self.points[addr] / self.max_magnitude
                magnitude = min(magnitude, 1.0)  # Clip to max radius
                x = center.x() + radius * magnitude * np.cos(np.radians(angle))
                y = center.y() - radius * magnitude * np.sin(np.radians(angle))
                point = QtCore.QPointF(x, y)
                points.append(point)
                
                # Draw point with device color
                color = self.colors[addr]
                painter.setPen(QtGui.QPen(color, 2))  # Thinner pen for outline
                painter.setBrush(color)  # Same color for fill
                painter.drawEllipse(point, 10, 10)

        # Draw connecting lines if we have at least 2 points
        if len(points) > 1:
            # Close the shape by connecting back to first point
            points.append(points[0])
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 128), 4))
            for i in range(len(points)-1):
                painter.drawLine(points[i], points[i+1])

        # Draw vector sum and check for active actions
        if points:
            magnitude, angle = self.calculate_vector_sum()
            x = center.x() + radius * magnitude * np.cos(np.radians(angle))
            y = center.y() - radius * magnitude * np.sin(np.radians(angle))
            sum_point = QtCore.QPointF(x, y)
            
            # Draw line from center to sum point
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
            painter.drawLine(center, sum_point)
            
            # Draw white disc at sum point
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
            painter.setBrush(QtGui.QColor(255, 255, 255))
            painter.drawEllipse(sum_point, 12, 12)
            
            # Check for active actions
            active_actions = self.calculate_active_actions(magnitude, angle)
            
            # Draw active action indicators
            y_offset = 30
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 1))
            for name, activation in active_actions.items():
                text = f"Active: {name} ({activation:.2f})"
                painter.drawText(10, y_offset, text)
                y_offset += 20

        # Draw action regions first (behind everything else)
        for action in self.actions:
            # Create path for the angular segment
            path = QtGui.QPainterPath()
            path.moveTo(center)
            
            # Draw the pie segment (using same coordinate system as devices)
            rect = QtCore.QRectF(
                center.x() - radius,
                center.y() - radius,
                radius * 2,
                radius * 2
            )
            
            # Convert to Qt angles (clockwise from 3 o'clock, negative for counterclockwise)
            start_angle = -action['start_angle']  # Negative because Qt uses clockwise
            end_angle = -action['end_angle']
            span_angle = start_angle - end_angle
            
            path.arcTo(rect, start_angle, span_angle)
            path.lineTo(center)
            
            # Fill the region
            color = QtGui.QColor(action['color'])
            painter.fillPath(path, color)
            
            # Draw the action name (using original angles since we want counterclockwise)
            mid_angle = (action['start_angle'] + action['end_angle']) / 2
            if action['end_angle'] < action['start_angle']:
                mid_angle = (action['start_angle'] + action['end_angle'] + 360) / 2
            mid_angle = mid_angle % 360
            
            text_x = center.x() + radius * 0.7 * np.cos(np.radians(mid_angle))
            text_y = center.y() - radius * 0.7 * np.sin(np.radians(mid_angle))
            
            # Center the text
            text_rect = painter.fontMetrics().boundingRect(action['name'])
            text_pos = QtCore.QPointF(
                text_x - text_rect.width()/2,
                text_y + text_rect.height()/4
            )
            painter.drawText(text_pos, action['name'])

class SpiderMyoPod(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spider MyoPod")
        self.setMinimumSize(800, 600)
        
        self.central_widget = QtWidgets.QWidget()
        self.setCentralWidget(self.central_widget)
        
        layout = QtWidgets.QVBoxLayout(self.central_widget)
        
        # Spider canvas
        self.spider_canvas = SpiderCanvas(self)
        layout.addWidget(self.spider_canvas)
        
        # Connect buttons
        btn_layout = QtWidgets.QHBoxLayout()
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.disconnect_btn)
        layout.addLayout(btn_layout)
        
        # Device list layout
        device_list_layout = QtWidgets.QVBoxLayout()
        
        # Add label edit field
        label_layout = QtWidgets.QHBoxLayout()
        self.label_edit = QtWidgets.QLineEdit()
        self.label_edit.setPlaceholderText("Enter device label")
        self.label_edit.setEnabled(False)
        self.set_label_btn = QtWidgets.QPushButton("Set Label")
        self.set_label_btn.setEnabled(False)
        label_layout.addWidget(self.label_edit)
        label_layout.addWidget(self.set_label_btn)
        device_list_layout.addLayout(label_layout)
        
        # Device list widget
        self.device_list_widget = QtWidgets.QListWidget()
        device_list_layout.addWidget(self.device_list_widget)
        
        layout.addLayout(device_list_layout)
        
        # Log area
        self.log_area = QtWidgets.QTextEdit()
        layout.addWidget(self.log_area)
        
        self.settings_file = "spider_myopod_settings.json"
        self.discovered_devices = {}
        self.connected_devices = {}
        self.connecting_devices = set()

        # Device colors
        self.device_colors = [
            QtGui.QColor(255, 0, 0),    # Red
            QtGui.QColor(0, 255, 0),    # Green
            QtGui.QColor(0, 0, 255),    # Blue
            QtGui.QColor(255, 255, 0),  # Yellow
            QtGui.QColor(255, 0, 255),  # Magenta
            QtGui.QColor(0, 255, 255),  # Cyan
            QtGui.QColor(255, 128, 0),  # Orange
            QtGui.QColor(128, 0, 255),  # Purple
        ]

        # Load saved device settings
        self.load_settings()

        # Start background scan timer
        self.scan_timer = QtCore.QTimer()
        self.scan_timer.timeout.connect(lambda: asyncio.create_task(self.background_scan()))
        self.scan_timer.start(1000)  # Scan every second

        # Add auto-connect timer
        self.connect_timer = QtCore.QTimer()
        self.connect_timer.timeout.connect(lambda: asyncio.create_task(self.try_auto_connect()))
        self.connect_timer.start(2000)  # Try auto-connect every 2 seconds

        # Start update timer for spider graph
        self.update_timer = QtCore.QTimer()
        self.update_timer.timeout.connect(self.update_spider)
        self.update_timer.start(1000 // UPDATE_RATE_HZ)

        # Connect buttons
        self.connect_btn.clicked.connect(self.on_connect_button)
        self.disconnect_btn.clicked.connect(self.on_disconnect_button)
        self.set_label_btn.clicked.connect(self.on_set_label)
        self.device_list_widget.itemSelectionChanged.connect(self.on_selection_changed)

    def load_settings(self):
        """Load saved device settings and actions from file."""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    settings = json.load(f)
                    self.saved_devices = settings.get('devices', [])
                    self.actions = settings.get('actions', [])
                    self.spider_canvas.set_actions(self.actions)
                    self.log("Loaded saved settings")
            else:
                # Create default settings file if it doesn't exist
                settings = {
                    'devices': [],
                    'actions': []
                }
                with open(self.settings_file, 'w') as f:
                    json.dump(settings, f, indent=2)
                self.saved_devices = []
                self.actions = []
                self.spider_canvas.set_actions(self.actions)
                self.log("Created new settings file")
        except Exception as e:
            self.log(f"Error loading settings: {e}")
            self.saved_devices = []
            self.actions = []
            self.spider_canvas.set_actions(self.actions)

    def save_settings(self):
        """Save current device settings to file."""
        try:
            # First load existing settings to preserve disconnected device info and actions
            existing_settings = {'devices': [], 'actions': self.default_actions}
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    existing_settings = json.load(f)

            # Update settings only for currently connected devices
            current_devices = {}
            for device in existing_settings['devices']:
                current_devices[device['address']] = device

            # Update or add connected devices
            for addr, info in self.connected_devices.items():
                current_devices[addr] = {
                    'address': addr,
                    'name': info['name'],
                    'angle': info.get('angle', 0),
                    'label': info.get('label', '')
                }

            settings = {
                'devices': list(current_devices.values()),
                'actions': existing_settings.get('actions', self.default_actions)  # Preserve existing actions
            }
            
            with open(self.settings_file, 'w') as f:
                json.dump(settings, f, indent=2)
            self.log("Saved device settings")
        except Exception as e:
            self.log(f"Error saving settings: {e}")

    async def try_auto_connect(self):
        """Attempt to auto-connect to saved devices."""
        if not self.saved_devices:
            return

        for device_info in self.saved_devices:
            address = device_info['address']
            if (address in self.discovered_devices and 
                address not in self.connected_devices and
                address not in self.connecting_devices):  # Check if not already connecting
                self.log(f"Auto-connecting to {device_info['name']}")
                await self.connect_device(address)

    async def background_scan(self):
        """Background scan for discovering new devices."""
        self.log("Starting background scan...")
        try:
            devices = await discover_devices(device_type=DeviceType.OB2_SENSOR)
            current_time = time.time()
            for address, (device, parsed_ad, rssi) in devices.items():
                if address not in self.discovered_devices:
                    self.discovered_devices[address] = (device, device.name, rssi, current_time)
                    self.update_device_list()
        except Exception as e:
            self.log(f"Error during background scan: {e}")

    def on_connect_button(self):
        """Handles the connect button click."""
        selected_items = self.device_list_widget.selectedItems()
        if not selected_items:
            self.log("No device selected for connection.")
            return
            
        address = selected_items[0].data(QtCore.Qt.ItemDataRole.UserRole)
        if address in self.connected_devices:
            self.log(f"Disconnecting from {address}")
            asyncio.create_task(self.disconnect_device(address))
        else:
            self.log(f"Connecting to {address}")
            asyncio.create_task(self.connect_device(address))

    def on_disconnect_button(self):
        """Handles the disconnect button click."""
        selected_items = self.device_list_widget.selectedItems()
        if not selected_items:
            self.log("No device selected for disconnection.")
            return
            
        address = selected_items[0].data(QtCore.Qt.ItemDataRole.UserRole)
        if address in self.connected_devices:
            self.log(f"Disconnecting from {address}")
            asyncio.create_task(self.disconnect_device(address))

    async def disconnect_device(self, address):
        """Disconnects from a device."""
        if address not in self.connected_devices:
            return
            
        self.log(f"Disconnecting from {address}")
        device_info = self.connected_devices.pop(address)
        
        try:
            # Stop the stream
            await device_info['myopod'].stop_stream()
            # Disconnect the client
            await device_info['client'].disconnect()
        except Exception as e:
            self.log(f"Error during disconnect: {e}")
        
        # Remove from spider canvas
        if address in self.spider_canvas.points:
            del self.spider_canvas.points[address]
        if address in self.spider_canvas.colors:
            del self.spider_canvas.colors[address]
        if address in self.spider_canvas.device_angles:
            del self.spider_canvas.device_angles[address]
        
        self.spider_canvas.update()
        self.update_device_list()

        # Save settings after disconnection
        self.save_settings()

    async def connect_device(self, address):
        """Connects to a device."""
        if address not in self.discovered_devices:
            self.log(f"Device {address} not found in discovered devices")
            return
            
        if address in self.connecting_devices:
            self.log(f"Already attempting to connect to {address}")
            return
            
        device, name, rssi, _ = self.discovered_devices[address]
        
        # Mark device as being connected to
        self.connecting_devices.add(address)
        
        try:
            client = BleakClient(device)
            await client.connect()
            self.log(f"Connected to {name}")
            
            myopod = MyoPod(client)
            data_deque = deque(maxlen=BUFFER_SIZE)
            notification_queue = asyncio.Queue()
            
            # Find saved settings for this device
            saved_angle = 0
            saved_label = ''
            for saved_device in self.saved_devices:
                if saved_device['address'] == address:
                    saved_angle = saved_device.get('angle', 0)
                    saved_label = saved_device.get('label', '')
                    break
            
            # Store connection info
            self.connected_devices[address] = {
                'client': client,
                'myopod': myopod,
                'data_deque': data_deque,
                'notification_queue': notification_queue,
                'name': name,
                'angle': saved_angle,
                'label': saved_label
            }
            
            # Assign color with wraparound
            color_index = (len(self.connected_devices) - 1) % len(self.device_colors)
            self.spider_canvas.colors[address] = self.device_colors[color_index]
            
            # Update spider canvas with device info
            self.spider_canvas.device_names[address] = name
            self.spider_canvas.device_angles[address] = saved_angle
            
            # Configure and start stream
            await myopod.configure_stream(
                stream_source=EmgStreamSource.PROCESSED_EMG,
                compression=CompressionType.INT16,
                average_samples=1
            )
            
            # Start stream with notification handler
            bound_handler = functools.partial(self.notification_handler, address)
            await myopod.start_stream(bound_handler)
            
            self.update_device_list()
            self.spider_canvas.update()
            
        except Exception as e:
            self.log(f"Failed to connect to {name}: {e}")
            if address in self.connected_devices:
                del self.connected_devices[address]
            if address in self.spider_canvas.device_names:
                del self.spider_canvas.device_names[address]
            if address in self.spider_canvas.device_angles:
                del self.spider_canvas.device_angles[address]
            self.update_device_list()
        finally:
            # Always remove from connecting set when done
            self.connecting_devices.remove(address)

    def notification_handler(self, address, packet: StreamDataPacket):
        """Handles incoming EMG data packets."""
        try:
            if address in self.connected_devices:
                device_info = self.connected_devices[address]
                if packet and packet.data_points:
                    device_info['data_deque'].extend(packet.data_points)
        except Exception as e:
            self.log(f"Error in notification handler: {e}")

    def update_spider(self):
        """Updates the spider graph with latest EMG values."""
        for address, device_info in self.connected_devices.items():
            data_deque = device_info.get('data_deque')
            if data_deque and len(data_deque) > 0:
                # Calculate RMS of recent samples
                recent_data = np.array(list(data_deque))
                rms = np.sqrt(np.mean(np.square(recent_data)))
                
                # Update max magnitude if necessary
                if rms > self.spider_canvas.max_magnitude:
                    self.spider_canvas.max_magnitude = rms * 1.2  # Add 20% headroom
                
                # Update point
                self.spider_canvas.points[address] = rms
        
        # Trigger repaint
        self.spider_canvas.update()

    def update_device_list(self):
        self.device_list_widget.clear()
        current_time = time.time()
        
        # First remove old devices
        addresses_to_remove = []
        for address, (_, _, _, last_seen) in self.discovered_devices.items():
            if current_time - last_seen > 10:
                addresses_to_remove.append(address)
        
        for address in addresses_to_remove:
            del self.discovered_devices[address]
        
        # Update the list
        for address, (device, name, rssi, last_seen) in self.discovered_devices.items():
            status = "Connected" if address in self.connected_devices else "Available"
            item = QtWidgets.QListWidgetItem(f"{name} ({status}) RSSI: {rssi}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, address)
            self.device_list_widget.addItem(item)
            
        # Update button states
        self.on_selection_changed()

    def on_selection_changed(self):
        """Handle device selection change."""
        selected_items = self.device_list_widget.selectedItems()
        if selected_items:
            address = selected_items[0].data(QtCore.Qt.ItemDataRole.UserRole)
            is_connected = address in self.connected_devices
            self.label_edit.setEnabled(is_connected)
            self.set_label_btn.setEnabled(is_connected)
            if is_connected:
                self.label_edit.setText(self.connected_devices[address].get('label', ''))
        else:
            self.label_edit.setEnabled(False)
            self.set_label_btn.setEnabled(False)
            self.label_edit.clear()

    def on_set_label(self):
        """Handle setting device label."""
        selected_items = self.device_list_widget.selectedItems()
        if not selected_items:
            return
            
        address = selected_items[0].data(QtCore.Qt.ItemDataRole.UserRole)
        if address in self.connected_devices:
            self.connected_devices[address]['label'] = self.label_edit.text()
            self.spider_canvas.update()
            self.save_settings()

    def log(self, message):
        self.log_area.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}")

    def update_device_angle(self, address, angle):
        """Update stored angle for a device."""
        if address in self.connected_devices:
            self.connected_devices[address]['angle'] = angle

    def get_device_label(self, address):
        """Get the user-defined label for a device."""
        if address in self.connected_devices:
            return self.connected_devices[address].get('label', '')
        return ''

    def closeEvent(self, event):
        """Handle application closing."""
        self.log("Saving settings before exit...")
        self.save_settings()
        
        # Disconnect all devices
        for address in list(self.connected_devices.keys()):
            asyncio.create_task(self.disconnect_device(address))
        
        # Accept the close event
        event.accept()

async def main():
    app = QtWidgets.QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    window = SpiderMyoPod()
    window.show()
    
    # Run the event loop
    try:
        loop.run_forever()
    finally:
        # Cleanup when the loop stops
        loop.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully
        print("\nShutting down...") 