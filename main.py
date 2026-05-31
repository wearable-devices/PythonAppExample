import asyncio
import sys
import os
from pathlib import Path
from mudra_sdk import Mudra, MudraDevice, FirmwareCallbacks
from mudra_sdk.cloud import MudraServerClient, SigninRequest
from mudra_sdk.models.callbacks import BleServiceDelegate, MudraDelegate
from mudra_sdk.models.enums import AirMouseButton, FirmwareTarget, GestureType, MudraCharacteristicUUID, HandType, PressureType, RecordingDataType, NavigationDirectionGesture

# Add parent directory to path so mudra_sdk can be imported
parent_dir = Path(__file__).parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

import tkinter as tk
from tkinter import ttk
from threading import Thread
import platform


# Global reference to the event loop and thread
loop = None
loop_thread = None

# Keep the actual MudraDevice objects so we can connect/disconnect to them
devices_list = []

# Dictionary to store status indicators (initialized in main)
status_indicators = {}

# Pressure On/Off button refs for mutual exclusion (Direct vs Pinch).
pressure_buttons = {}

# Tracks which features have been enabled from the UI. Callbacks check the
# corresponding flag before updating the UI so queued/stale BLE samples don't
# overwrite the reset value after the user clicks Off.
feature_active = {
    'snc': False,
    'imu_acc': False,
    'imu_gyro': False,
    'pinch_pressure': False,
    'direct_pressure': False,
    'navigation_axis': False,
    'navigation_direction': False,
    'gesture': False,
    'button_changed': False,
}

# UI widgets that callbacks/disable handlers reference. Initialised here so
# attempting to use them before main() builds the UI is safe.
snc_freq_label = None
rms_bars = []
rms_labels = []
imu_acc_label = None
imu_gyro_label = None
navigation_x_label = None
navigation_y_label = None
navigation_direction_label = None
gesture_label = None
air_touch_button_label = None
pinch_pressure_label = None
pinch_pressure_bar = None
direct_pressure_label = None
direct_pressure_bar = None

device_info_labels = {}

# Per-device row widgets, keyed by device.name.
# Each value is a dict: {device, row, name_label, status_dot, action_btn, state}.
device_rows = {}

# Holder for the currently selected device (clicked in the device list).
# A list so it can be mutated from nested functions without `global`.
selected_device_holder = [None]

# Scrollable container into which device rows are inserted (assigned in main).
devices_container = None

# Navigation smoothing (moving average)
navigation_history = {'x': [], 'y': []}
NAVIGATION_SMOOTHING_WINDOW = 5  # Number of samples to average

# Navigation Axis 2D pad: maps incoming values in [-NAV_AXIS_RANGE, +NAV_AXIS_RANGE]
# to a square canvas. The dot is more readable than the jumpy text-only output.
NAV_AXIS_RANGE = 200
NAV_PAD_SIZE = 90
NAV_DOT_RADIUS = 4
nav_axis_canvas = None
nav_axis_dot = None


def update_devices_list(device):
    """Called from the BLE delegate when a new device is discovered."""
    print(device.name)
    # Avoid duplicates if the same device is rediscovered on a second scan.
    if device.name in device_rows:
        return
    devices_list.append(device)
    if root and root.winfo_exists():
        root.after(0, lambda d=device: _add_device_row(d))


def _add_device_row(device):
    """Create a UI row for a discovered device inside `devices_container`."""
    if devices_container is None or not root.winfo_exists():
        return
    if device.name in device_rows:
        return

    row = tk.Frame(devices_container, bg='white', highlightthickness=1,
                   highlightbackground='#e0e0e0')
    row.pack(fill=tk.X, pady=1)

    status_dot = tk.Label(row, text="●", font=('Segoe UI', 10), bg='white', fg='gray', width=2)
    status_dot.pack(side=tk.LEFT, padx=(2, 0))

    name_label = tk.Label(row, text=device.name, font=('Segoe UI', 9), bg='white',
                          fg='black', anchor='w')
    name_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 4))

    action_btn = ttk.Button(row, text="Connect", width=11,
                            command=lambda d=device: _toggle_connection(d))
    action_btn.pack(side=tk.RIGHT, padx=(0, 2), pady=2)

    info = {
        'device': device,
        'row': row,
        'name_label': name_label,
        'status_dot': status_dot,
        'action_btn': action_btn,
        'state': 'idle',
    }
    device_rows[device.name] = info

    # Clicking anywhere on the row (except the button) selects the device for
    # feature operations.
    def _on_click(_event, d=device):
        _select_device(d)

    for w in (row, name_label, status_dot):
        w.bind('<Button-1>', _on_click)

    # If another device is already busy/connected, the new row should start disabled.
    _refresh_idle_buttons()


def _post_ui(callback):
    """Schedule a callback on the Tk main thread (safe from BLE/async thread)."""
    if root is not None and root.winfo_exists():
        root.after(0, callback)


def _select_device(device):
    """Mark `device` as the currently selected one for feature operations."""
    selected_device_holder[0] = device
    for name, info in device_rows.items():
        is_selected = info['device'] is device
        bg = '#cce5ff' if is_selected else 'white'
        info['row'].config(bg=bg)
        info['name_label'].config(bg=bg)
        info['status_dot'].config(bg=bg)
    _post_ui(lambda: _refresh_device_ui(device))


def _refresh_idle_buttons():
    """Disable Connect on every idle device whenever any other device is
    connecting / connected / disconnecting. Re-enable when nothing is busy.
    """
    busy = any(info['state'] in ('connecting', 'connected', 'disconnecting')
               for info in device_rows.values())
    for info in device_rows.values():
        if info['state'] == 'idle':
            info['action_btn'].config(state='disabled' if busy else 'normal')


def _set_device_state(device, state):
    """Update a row's visual state. `state` is one of:
    'idle', 'connecting', 'connected', 'disconnecting'."""
    info = device_rows.get(device.name)
    if info is None or not root.winfo_exists():
        return
    info['state'] = state
    btn = info['action_btn']
    dot = info['status_dot']
    if state == 'idle':
        btn.config(text='Connect', state='normal')
        dot.config(fg='gray')
    elif state == 'connecting':
        btn.config(text='...', state='disabled')
        dot.config(fg='#e0a800')
    elif state == 'connected':
        btn.config(text='Disconnect', state='normal')
        dot.config(fg='#28a745')
    elif state == 'disconnecting':
        btn.config(text='...', state='disabled')
        dot.config(fg='#e0a800')

    # After updating this row, lock/unlock all other idle rows accordingly.
    _refresh_idle_buttons()


def _toggle_connection(device):
    """Connect or disconnect based on this device's current state."""
    info = device_rows.get(device.name)
    state = info['state'] if info else 'idle'
    _select_device(device)
    ensure_event_loop_running()
    if loop is None:
        return
    if state == 'idle':
        asyncio.run_coroutine_threadsafe(device.connect(), loop)
    elif state == 'connected':
        asyncio.run_coroutine_threadsafe(device.disconnect(), loop)

mudra = Mudra()
mudra_server_client = MudraServerClient()


def on_sign_in():
    """Handle sign-in: validate inputs, call sign-in API, update status."""
    email = email_entry.get().strip()
    password = password_entry.get().strip()
    platform_val = platform_var.get().strip()

    if not email:
        print("Error: Please enter your email address")
        sign_in_status_label.config(text="Please enter your email.", foreground="red")
        return
    if not password:
        print("Error: Please enter your password")
        sign_in_status_label.config(text="Please enter your password.", foreground="red")
        return
    if not platform_val:
        print("Error: Please select a platform")
        sign_in_status_label.config(text="Please select a platform.", foreground="red")
        return

    sign_in_btn.config(state="disabled")
    sign_in_status_label.config(text="Signing in...", foreground="blue")
    root.update()

    try:
        signin_request = SigninRequest(
            email=email,
            password=password,
            platform=platform_val,
            application="Python Test Application"
        )
        print(f"\n{'='*50}")
        print("Signing in...")
        print(f"Email: {email}")
        print(f"Platform: {platform_val}")
        print(f"{'='*50}\n")

        response = mudra_server_client.sign_in_api_call(signin_request.to_json())

        print("✓ Sign in successful!")
        print(f"\nResponse:")
        print(f"  Access Token: {response.get('accessToken', 'N/A')}")
        print(f"  Refresh Token: {response.get('refreshToken', 'N/A')}")
        if isinstance(response, dict):
            print(f"\nFull Response:")
            for key, value in response.items():
                print(f"  {key}: {value}")
        print(f"\n{'='*50}\n")

        sign_in_status_label.config(text="✓ Sign in successful! Check console for details.", foreground="green")
        password_entry.delete(0, tk.END)
    except Exception as e:
        error_message = str(e)
        print(f"\n{'='*50}")
        print("✗ Sign in failed!")
        print(f"Error: {error_message}")
        print(f"{'='*50}\n")
        sign_in_status_label.config(text=f"✗ Error: {error_message}", foreground="red")
    finally:
        sign_in_btn.config(state="normal")
        root.update()


def _post_state(device, state):
    """Schedule a row state update on the Tk main thread (safe from BLE thread)."""
    if root is not None and root.winfo_exists():
        root.after(0, lambda d=device, s=state: _set_device_state(d, s))


class MyMudraDelegate(MudraDelegate):
    def on_device_discovered(self, device: MudraDevice):
        print(f"Discovered: {device.name}")
        update_devices_list(device)

    def on_mudra_device_disconnected(self, device: MudraDevice):
        print(f"Device disconnected: {device.name}")
        _post_state(device, 'idle')
        _teardown_device_monitoring(device)
        if get_selected_device() is device:
            _post_ui(lambda: _refresh_device_ui(None))

    def on_mudra_device_disconnecting(self, device: MudraDevice):
        print(f"Device disconnecting: {device.name}")
        _post_state(device, 'disconnecting')

    def on_mudra_device_connected(self, device: MudraDevice):
        print(f"Device connected: {device.name}")
        _post_state(device, 'connected')
        _setup_device_monitoring(device)
        if get_selected_device() is device:
            _post_ui(lambda d=device: _refresh_device_ui(d))

    def on_mudra_device_connecting(self, device: MudraDevice):
        print(f"Device connecting: {device.name}")
        _post_state(device, 'connecting')

    def on_mudra_device_connection_failed(self, device: MudraDevice, error: str):
        print(f"Connection failed: {device.name}, Error: {error}")
        _post_state(device, 'idle')

    def on_bluetooth_state_changed(self, state: bool):
        print(f"Bluetooth state changed: {'On' if state else 'Off'}")


mudra.set_delegate(MyMudraDelegate())

def run_event_loop():
    """Run the event loop in a separate thread."""
    global loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_forever()


def ensure_event_loop_running():
    """Ensure the background asyncio event loop is running."""
    global loop, loop_thread
    if loop_thread is None or not loop_thread.is_alive():
        loop_thread = Thread(target=run_event_loop, daemon=True)
        loop_thread.start()
        # Wait a bit for the loop to start
        import time
        time.sleep(0.1)


def start_scan():
    # Ensure event loop is running
    ensure_event_loop_running()

    # Schedule the scan on the event loop
    if loop is not None:
        asyncio.run_coroutine_threadsafe(mudra.scan(), loop)

def stop_scan():
    global loop
    # Stop scanning using the same event loop
    if loop is not None:
        asyncio.run_coroutine_threadsafe(mudra.stop_scan(), loop)


def get_selected_device():
    """Return the device currently selected in the sidebar (clicked row)."""
    return selected_device_holder[0]


def connect_selected_device():
    """Connect to the selected device."""
    global loop
    device = get_selected_device()
    if device is None:
        print("No device selected to connect.")
        return

    ensure_event_loop_running()

    if loop is not None:
        asyncio.run_coroutine_threadsafe(device.connect(), loop)


def disconnect_selected_device():
    """Disconnect from the selected device."""
    global loop
    device = get_selected_device()
    if device is None:
        print("No device selected to disconnect.")
        return

    ensure_event_loop_running()

    if loop is not None:
        asyncio.run_coroutine_threadsafe(device.disconnect(), loop)

def discover_selected_device():
    """Discover GATT services/characteristics on the selected device."""
    global loop
    device = get_selected_device()
    if device is None:
        print("No device selected to discover.")
        return

    ensure_event_loop_running()

    if loop is not None:
        future = asyncio.run_coroutine_threadsafe(
            mudra.ble_service.discover_services_and_characteristics(device),
            loop,
        )

        # Log completion or errors from the background task
        def _on_done(fut: asyncio.Future):
            try:
                fut.result()
                print("Discovery completed.")
            except Exception as e:
                print(f"Discovery failed with error: {e}")

        future.add_done_callback(_on_done)


def on_snc_ready(timestamp, data_list, frequency, frequency_std, rms_list):
    print(f"SNC frequency: {frequency}")

    def _update_rms_bars():
        if not root.winfo_exists() or not feature_active['snc']:
            return
        values = (list(rms_list) + [0.0, 0.0, 0.0])[:3]
        for i, (bar, label, value) in enumerate(zip(rms_bars, rms_labels, values), start=1):
            value = max(0.0, min(1.0, float(value)))
            bar.config(value=value * 100.0)
            label.config(text=f"RMS {i}: {value:.2f}")
        if snc_freq_label is not None:
            snc_freq_label.config(text=f"{frequency:.2f} Hz")

    if root.winfo_exists():
        root.after(0, _update_rms_bars)


def on_imu_acc_ready(timestamp, data_list, frequency, frequency_std, rms_list):
    print(f"IMU Acc frequency: {frequency}")

    def _update_imu_acc_label():
        if not root.winfo_exists() or not feature_active['imu_acc']:
            return
        if imu_acc_label is not None:
            imu_acc_label.config(text=f"{frequency:.2f} Hz")

    if root.winfo_exists():
        root.after(0, _update_imu_acc_label)


def on_imu_gyro_ready(timestamp, data_list, frequency, frequency_std, rms_list):
    print(f"IMU Gyro frequency: {frequency}")

    def _update_imu_gyro_label():
        if not root.winfo_exists() or not feature_active['imu_gyro']:
            return
        if imu_gyro_label is not None:
            imu_gyro_label.config(text=f"{frequency:.2f} Hz")

    if root.winfo_exists():
        root.after(0, _update_imu_gyro_label)

def _nav_value_to_pixel(value: float) -> float:
    """Map a value in [-NAV_AXIS_RANGE, +NAV_AXIS_RANGE] to a canvas pixel coord."""
    clamped = max(-NAV_AXIS_RANGE, min(NAV_AXIS_RANGE, value))
    mid = NAV_PAD_SIZE / 2.0
    usable = mid - NAV_DOT_RADIUS - 1
    return mid + (clamped / NAV_AXIS_RANGE) * usable


def _set_nav_axis_dot(x: float, y: float):
    """Position the pad dot for axis values x, y (canvas Y is inverted)."""
    if nav_axis_canvas is None or nav_axis_dot is None:
        return
    px = _nav_value_to_pixel(x)
    py = _nav_value_to_pixel(-y)
    r = NAV_DOT_RADIUS
    nav_axis_canvas.coords(nav_axis_dot, px - r, py - r, px + r, py + r)


def _set_nav_axis_text(x: float, y: float):
    if navigation_x_label is not None:
        navigation_x_label.config(text=f"X = {int(round(x))}")
    if navigation_y_label is not None:
        navigation_y_label.config(text=f"Y = {int(round(y))}")


def _reset_nav_axis_view():
    if root and root.winfo_exists():
        _set_nav_axis_dot(0, 0)
        _set_nav_axis_text(0, 0)


def on_navigation_axis_ready(delta_x, delta_y):
    print(f"Navigation delta: {delta_x}, {delta_y}")

    global navigation_history
    navigation_history['x'].append(float(delta_x))
    navigation_history['y'].append(float(delta_y))

    if len(navigation_history['x']) > NAVIGATION_SMOOTHING_WINDOW:
        navigation_history['x'].pop(0)
        navigation_history['y'].pop(0)

    smoothed_x = sum(navigation_history['x']) / len(navigation_history['x'])
    smoothed_y = sum(navigation_history['y']) / len(navigation_history['y'])

    def _update_navigation_view():
        if not root.winfo_exists() or not feature_active['navigation_axis']:
            return
        _set_nav_axis_dot(smoothed_x, smoothed_y)
        _set_nav_axis_text(smoothed_x, smoothed_y)

    if root.winfo_exists():
        root.after(0, _update_navigation_view)

def on_navigation_direction_ready(direction: NavigationDirectionGesture):
    direction_str = direction.description if hasattr(direction, 'description') else str(direction)
    print(f"Navigation direction: {direction_str}")

    def _update_navigation_direction_label():
        if not root.winfo_exists() or not feature_active['navigation_direction']:
            return
        if navigation_direction_label is not None:
            navigation_direction_label.config(text=direction_str)

    if root.winfo_exists():
        root.after(0, _update_navigation_direction_label)


def on_pinch_pressure_ready(pressure_data: float):
    def _update():
        if not root.winfo_exists() or not feature_active['pinch_pressure']:
            return
        value = max(0.0, min(1.0, float(pressure_data)))
        if pinch_pressure_bar is not None:
            pinch_pressure_bar.config(value=value)
        if pinch_pressure_label is not None:
            pinch_pressure_label.config(text=f"{value:.2f}")

    if root.winfo_exists():
        root.after(0, _update)


def on_direct_pressure_ready(pressure_data: float):
    def _update():
        if not root.winfo_exists() or not feature_active['direct_pressure']:
            return
        value = max(0.0, min(1.0, float(pressure_data)))
        if direct_pressure_bar is not None:
            direct_pressure_bar.config(value=value)
        if direct_pressure_label is not None:
            direct_pressure_label.config(text=f"{value:.2f}")

    if root.winfo_exists():
        root.after(0, _update)


def on_gesture_ready(gesture_type):
    print(f"Gesture received: {gesture_type}")

    def _update_gesture_label():
        if not root.winfo_exists() or not feature_active['gesture']:
            return
        if gesture_label is not None:
            gesture_label.config(text=str(gesture_type))

    if root.winfo_exists():
        root.after(0, _update_gesture_label)


def on_airmouse_button_changed_ready(air_touch_button):
    print(f"Air Touch Button changed: {air_touch_button}")

    def _update_air_touch_button_label():
        if not root.winfo_exists() or not feature_active['button_changed']:
            return
        if air_touch_button_label is not None:
            air_touch_button_label.config(text=str(air_touch_button))

    if root.winfo_exists():
        root.after(0, _update_air_touch_button_label)

def enable_snc_feature():
    """Placeholder to enable a feature on the selected device."""
    device = get_selected_device()
    if device is None:
        print("No device selected to enable feature.")
        return
    print(f"Enable feature called for device: {device.name}")
    feature_active['snc'] = True
    asyncio.run_coroutine_threadsafe(device.set_on_snc_ready(on_snc_ready), loop)

def _reset_snc_output():
    """Reset SNC frequency label and the 3 RMS bars/labels to their defaults."""
    if root is None or not root.winfo_exists():
        return
    if snc_freq_label is not None:
        snc_freq_label.config(text="-- Hz")
    for i, (bar, lbl) in enumerate(zip(rms_bars, rms_labels), start=1):
        bar.config(value=0)
        lbl.config(text=f"RMS {i}: 0.00")


def disable_snc_feature():
    """Placeholder to disable a feature on the selected device."""
    device = get_selected_device()
    if device is None:
        print("No device selected to disable feature.")
        return
    print(f"Disable feature called for device: {device.name}")
    feature_active['snc'] = False
    asyncio.run_coroutine_threadsafe(device.set_on_snc_ready(None), loop)
    _reset_snc_output()


def enable_imu_acc_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to enable IMU Acc feature.")
        return
    print(f"Enable IMU Acc feature called for device: {device.name}")
    feature_active['imu_acc'] = True
    asyncio.run_coroutine_threadsafe(device.set_on_imu_acc_ready(on_imu_acc_ready), loop)

def disable_imu_acc_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to disable IMU Acc feature.")
        return
    print(f"Disable IMU Acc feature called for device: {device.name}")
    feature_active['imu_acc'] = False
    asyncio.run_coroutine_threadsafe(device.set_on_imu_acc_ready(None), loop)
    if root and root.winfo_exists() and imu_acc_label is not None:
        imu_acc_label.config(text="-- Hz")

def enable_imu_gyro_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to enable IMU Gyro feature.")
        return
    print(f"Enable IMU Gyro feature called for device: {device.name}")
    feature_active['imu_gyro'] = True
    asyncio.run_coroutine_threadsafe(device.set_on_imu_gyro_ready(on_imu_gyro_ready), loop)

def disable_imu_gyro_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to disable IMU Gyro feature.")
        return
    print(f"Disable IMU Gyro feature called for device: {device.name}")
    feature_active['imu_gyro'] = False
    asyncio.run_coroutine_threadsafe(device.set_on_imu_gyro_ready(None), loop)
    if root and root.winfo_exists() and imu_gyro_label is not None:
        imu_gyro_label.config(text="-- Hz")


def _set_pressure_buttons_state(prefix, state):
    """Set both On/Off buttons of the given pressure ('direct' or 'pinch')."""
    for suffix in ('on', 'off'):
        btn = pressure_buttons.get(f'{prefix}_{suffix}')
        if btn is not None:
            btn.config(state=state)


def enable_pinch_pressure_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to enable Pinch Pressure feature.")
        return
    print(f"Enable Pinch Pressure feature called for device: {device.name}")
    feature_active['pinch_pressure'] = True
    asyncio.run_coroutine_threadsafe(device.set_on_pressure_ready(on_pinch_pressure_ready, PressureType.pinch), loop)
    _set_pressure_buttons_state('direct', 'disabled')

def disable_pinch_pressure_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to disable Pinch Pressure feature.")
        return
    print(f"Disable Pinch Pressure feature called for device: {device.name}")
    feature_active['pinch_pressure'] = False
    asyncio.run_coroutine_threadsafe(device.set_on_pressure_ready(None), loop)
    _set_pressure_buttons_state('direct', 'normal')
    if root and root.winfo_exists():
        if pinch_pressure_label is not None:
            pinch_pressure_label.config(text="0.00")
        if pinch_pressure_bar is not None:
            pinch_pressure_bar.config(value=0)

def enable_direct_pressure_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to enable Direct Pressure feature.")
        return
    print(f"Enable Direct Pressure feature called for device: {device.name}")
    feature_active['direct_pressure'] = True
    asyncio.run_coroutine_threadsafe(device.set_on_pressure_ready(on_direct_pressure_ready, PressureType.direct), loop)
    _set_pressure_buttons_state('pinch', 'disabled')

def disable_direct_pressure_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to disable Direct Pressure feature.")
        return
    print(f"Disable Direct Pressure feature called for device: {device.name}")
    feature_active['direct_pressure'] = False
    asyncio.run_coroutine_threadsafe(device.set_on_pressure_ready(None), loop)
    _set_pressure_buttons_state('pinch', 'normal')
    if root and root.winfo_exists():
        if direct_pressure_label is not None:
            direct_pressure_label.config(text="0.00")
        if direct_pressure_bar is not None:
            direct_pressure_bar.config(value=0)

def enable_navigation_axis_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to enable Navigation Axis feature.")
        return
    print(f"Enable Navigation feature called for device: {device.name}")
    feature_active['navigation_axis'] = True
    asyncio.run_coroutine_threadsafe(device.set_on_navigation_axis_ready(on_navigation_axis_ready), loop)

def disable_navigation_axis_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to disable Navigation Axis feature.")
        return
    print(f"Disable Navigation feature called for device: {device.name}")
    feature_active['navigation_axis'] = False
    asyncio.run_coroutine_threadsafe(device.set_on_navigation_axis_ready(None), loop)
    navigation_history['x'].clear()
    navigation_history['y'].clear()
    _reset_nav_axis_view()

def enable_navigation_direction_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to enable Navigation Direction feature.")
        return
    print(f"Enable Navigation Direction feature called for device: {device.name}")
    feature_active['navigation_direction'] = True
    asyncio.run_coroutine_threadsafe(device.set_on_navigation_direction_ready(on_navigation_direction_ready), loop)

def disable_navigation_direction_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to disable Navigation Direction feature.")
        return
    print(f"Disable Navigation Direction feature called for device: {device.name}")
    feature_active['navigation_direction'] = False
    asyncio.run_coroutine_threadsafe(device.set_on_navigation_direction_ready(None), loop)
    if root and root.winfo_exists() and navigation_direction_label is not None:
        navigation_direction_label.config(text="--")

def enable_gesture_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to enable Gesture feature.")
        return
    print(f"Enable Gesture feature called for device: {device.name}")
    feature_active['gesture'] = True
    asyncio.run_coroutine_threadsafe(device.set_on_gesture_ready(on_gesture_ready), loop)

def disable_gesture_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to disable Gesture feature.")
        return
    print(f"Disable Gesture feature called for device: {device.name}")
    feature_active['gesture'] = False
    asyncio.run_coroutine_threadsafe(device.set_on_gesture_ready(None), loop)
    if root and root.winfo_exists() and gesture_label is not None:
        gesture_label.config(text="--")

def enable_air_mouse_button_changed_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to enable Air Touch Button Changed feature.")
        return
    print(f"Enable Air Touch Button Changed feature called for device: {device.name}")
    feature_active['button_changed'] = True
    asyncio.run_coroutine_threadsafe(device.set_on_button_changed(on_airmouse_button_changed_ready), loop)

def disable_air_mouse_button_changed_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to disable Air Touch Button Changed feature.")
        return
    print(f"Disable Air Touch Button Changed feature called for device: {device.name}")
    feature_active['button_changed'] = False
    asyncio.run_coroutine_threadsafe(device.set_on_button_changed(None), loop)
    if root and root.winfo_exists() and air_touch_button_label is not None:
        air_touch_button_label.config(text="--")

def enable_embedded_airtouch_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to enable Embedded AirTouch feature.")
        return
    print(f"Enable Embedded AirTouch feature called for device: {device.name}")
    asyncio.run_coroutine_threadsafe(device.set_air_touch_active(True), loop)

def disable_embedded_airtouch_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to disable Embedded AirTouch feature.")
        return
    print(f"Disable Embedded AirTouch feature called for device: {device.name}")
    asyncio.run_coroutine_threadsafe(device.set_air_touch_active(False), loop)

def enable_navigation_to_app():
    device = get_selected_device()
    if device is None:
        print("No device selected to enable Navigation To App.")
        return
    print(f"Enable Navigation To App called for device: {device.name}")
    asyncio.run_coroutine_threadsafe(device.set_firmware_target(FirmwareTarget.navigation_to_app, True), loop)

def disable_navigation_to_app():
    device = get_selected_device()
    if device is None:
        print("No device selected to disable Navigation To App.")
        return
    print(f"Disable Navigation To App called for device: {device.name}")
    asyncio.run_coroutine_threadsafe(device.set_firmware_target(FirmwareTarget.navigation_to_app, False), loop)

def enable_gesture_to_hid():
    device = get_selected_device()
    if device is None:
        print("No device selected to enable Gesture To HID.")
        return
    print(f"Enable Gesture To HID called for device: {device.name}")
    asyncio.run_coroutine_threadsafe(device.set_firmware_target(FirmwareTarget.gesture_to_hid, True), loop)

def disable_gesture_to_hid():
    device = get_selected_device()
    if device is None:
        print("No device selected to disable Gesture To HID.")
        return
    print(f"Disable Gesture To HID called for device: {device.name}")
    asyncio.run_coroutine_threadsafe(device.set_firmware_target(FirmwareTarget.gesture_to_hid, False), loop)

def enable_navigation_to_hid():
    device = get_selected_device()
    if device is None:
        print("No device selected to enable Navigation To HID.")
        return
    print(f"Enable Navigation To HID called for device: {device.name}")
    asyncio.run_coroutine_threadsafe(device.set_firmware_target(FirmwareTarget.navigation_to_hid, True), loop)

def disable_navigation_to_hid():
    device = get_selected_device()
    if device is None:
        print("No device selected to disable Navigation To HID.")
        return
    print(f"Disable Navigation To HID called for device: {device.name}")
    asyncio.run_coroutine_threadsafe(device.set_firmware_target(FirmwareTarget.navigation_to_hid, False), loop)

def set_hand_left():
    device = get_selected_device()
    if device is None:
        print("No device selected to set hand to left.")
        return
    print(f"Set hand to left called for device: {device.name}")
    asyncio.run_coroutine_threadsafe(device.set_hand(HandType.left), loop)

def set_hand_right():
    device = get_selected_device()
    if device is None:
        print("No device selected to set hand to right.")
        return
    print(f"Set hand to right called for device: {device.name}")
    asyncio.run_coroutine_threadsafe(device.set_hand(HandType.right), loop)


def start_recording_btn():
    """Start recording on the selected device."""
    device = get_selected_device()
    if device is None:
        print("No device selected to start recording.")
        return
    ensure_event_loop_running()
    # Record SNC, pressure, IMU data, and timestamps for 60 seconds
    recording_types = [
        RecordingDataType.sncTS, RecordingDataType.sncAppTS,
        RecordingDataType.snc1, RecordingDataType.snc2, RecordingDataType.snc3,
        RecordingDataType.accTS, RecordingDataType.accAppTS,
        RecordingDataType.acc1, RecordingDataType.acc2, RecordingDataType.acc3,
        RecordingDataType.pressure, RecordingDataType.endAppTS,
    ]
    device.enable_recording()
    asyncio.run_coroutine_threadsafe(device.start_recording("{}", recording_types, 60), loop)


def stop_recording_btn():
    """Stop recording on the selected device."""
    device = get_selected_device()
    if device is None:
        print("No device selected to stop recording.")
        return
    ensure_event_loop_running()

    asyncio.run_coroutine_threadsafe(device.stop_recording(), loop)


def get_json_recording_btn():
    """Get JSON recording from the selected device and display it."""
    device = get_selected_device()
    if device is None:
        print("No device selected to get recording.")
        return
    ensure_event_loop_running()

    json_str = device.get_json_recording()
    print("Recording JSON:")
    print(json_str)

def _update_indicator(indicator_or_list, color):
    """Helper function to update indicator(s) - handles both single indicator and list of indicators."""
    if not root.winfo_exists():
        return
    if isinstance(indicator_or_list, list):
        for indicator in indicator_or_list:
            if indicator:
                indicator.config(bg='white', fg=color)
    else:
        if indicator_or_list:
            indicator_or_list.config(bg='white', fg=color)

def _set_device_info_label(key, text):
    label = device_info_labels.get(key)
    if label is not None:
        label.config(text=text)


def _refresh_hand_type(device):
    if not root or not root.winfo_exists():
        return
    hand = device.get_hand_type() if device is not None else None
    text = hand.name.capitalize() if hand is not None else "--"
    _set_device_info_label('hand_type', text)


def _refresh_charging(device):
    if not root or not root.winfo_exists():
        return
    if device is None:
        _set_device_info_label('is_charging', "--")
        return
    try:
        charging = device.get_is_charging()
    except AttributeError:
        charging = None
    text = "Yes" if charging else ("No" if charging is False else "--")
    _set_device_info_label('is_charging', text)


def _refresh_battery_level(device):
    if not root or not root.winfo_exists():
        return
    if device is None:
        _set_device_info_label('battery_level', "--")
        return
    try:
        level = device.get_battery_level()
    except AttributeError:
        level = None
    _set_device_info_label('battery_level', f"{level}%" if level is not None else "--")


def _refresh_static_device_info(device):
    """Serial number and firmware version are available once the device has connected."""
    if not root or not root.winfo_exists():
        return
    if device is None:
        _set_device_info_label('serial_number', "--")
        _set_device_info_label('firmware_version', "--")
        return
    serial = device.get_serial_number()
    _set_device_info_label('serial_number', str(serial) if serial is not None else "--")
    version = device.get_firmware_version()
    _set_device_info_label('firmware_version', version if version else "--")


def _clear_device_info_labels():
    for key in device_info_labels:
        _set_device_info_label(key, "--")


def _refresh_device_ui(device):
    """Refresh indicators and device-info labels for the selected device."""
    _refresh_status_indicators(device)
    if device is None:
        _clear_device_info_labels()
        return
    _refresh_hand_type(device)
    _refresh_charging(device)
    _refresh_battery_level(device)
    _refresh_static_device_info(device)


def _setup_device_monitoring(device):
    """Register firmware / charging / battery callbacks on the connected device."""
    ensure_event_loop_running()
    if loop is None:
        return

    def on_firmware_status_changed(is_firmware_status_changed: bool):
        if get_selected_device() is device:
            def _update_ui(d=device):
                _refresh_status_indicators(d)
                _refresh_hand_type(d)
            _post_ui(_update_ui)

    def on_charging_state_changed(is_charging):
        if get_selected_device() is device:
            text = "Yes" if is_charging else "No"
            _post_ui(lambda t=text: _set_device_info_label('is_charging', t))

    def on_battery_level_changed(level):
        if get_selected_device() is device:
            _post_ui(lambda l=level: _set_device_info_label('battery_level', f"{l}%"))

    async def _register():
        await device.set_on_firmware_status_changed(on_firmware_status_changed)
        await device.set_on_charging_state_changed(on_charging_state_changed)
        await device.set_on_battery_level_changed(on_battery_level_changed)

    asyncio.run_coroutine_threadsafe(_register(), loop)


def _teardown_device_monitoring(device):
    """Clear device monitoring callbacks on disconnect."""
    ensure_event_loop_running()
    if loop is None:
        return

    async def _unregister():
        await device.set_on_firmware_status_changed(None)
        await device.set_on_charging_state_changed(None)
        await device.set_on_battery_level_changed(None)

    asyncio.run_coroutine_threadsafe(_unregister(), loop)


def _refresh_status_indicators(device):
    """Update status indicators from the given device's firmware_status."""
    if device is None or not hasattr(device, 'firmware_status'):
        for indicator in status_indicators.values():
            _update_indicator(indicator, 'gray')
        _set_pressure_buttons_state('direct', 'normal')
        _set_pressure_buttons_state('pinch', 'normal')
        return

    fs = device.firmware_status
    
    # Update Data Features indicators
    if 'snc' in status_indicators:
        color = 'green' if fs.is_snc_enabled else 'red'
        _update_indicator(status_indicators['snc'], color)
    
    if 'imu_acc' in status_indicators:
        color = 'green' if fs.is_acc_enabled else 'red'
        _update_indicator(status_indicators['imu_acc'], color)
    
    if 'imu_gyro' in status_indicators:
        color = 'green' if fs.is_gyro_enabled else 'red'
        _update_indicator(status_indicators['imu_gyro'], color)
    
    if 'pinch_pressure' in status_indicators:
        color = 'green' if fs.is_pinch_pressure_enabled else 'red'
        _update_indicator(status_indicators['pinch_pressure'], color)

    if 'direct_pressure' in status_indicators:
        color = 'green' if fs.is_pressure_enabled else 'red'
        _update_indicator(status_indicators['direct_pressure'], color)
    
    if 'navigation' in status_indicators:
        color = 'green' if fs.is_navigation_enabled else 'red'
        _update_indicator(status_indicators['navigation'], color)
    
    if 'gesture' in status_indicators:
        color = 'green' if fs.is_gesture_enabled else 'red'
        _update_indicator(status_indicators['gesture'], color)
    
    if 'air_touch' in status_indicators:
        color = 'green' if fs.is_air_touch_enabled else 'red'
        _update_indicator(status_indicators['air_touch'], color)
    
    # Update Firmware Targets indicators
    if 'nav_to_app' in status_indicators:
        color = 'green' if fs.is_sends_navigation_to_app_enabled else 'red'
        _update_indicator(status_indicators['nav_to_app'], color)
    
    if 'gesture_to_hid' in status_indicators:
        color = 'green' if fs.is_sends_gesture_to_hid_enabled else 'red'
        _update_indicator(status_indicators['gesture_to_hid'], color)
    
    if 'nav_to_hid' in status_indicators:
        color = 'green' if fs.is_sends_navigation_to_hid_enabled else 'red'
        _update_indicator(status_indicators['nav_to_hid'], color)
    
    # Update Embedded Features indicators
    if 'embedded_airtouch' in status_indicators:
        color = 'green' if fs.is_air_touch_enabled else 'red'
        _update_indicator(status_indicators['embedded_airtouch'], color)

    # Mutual exclusion: Direct and Pinch pressure cannot both be active.
    # While one is enabled, disable BOTH On and Off of the other.
    if pressure_buttons:
        direct_active = bool(fs.is_pressure_enabled)
        pinch_active = bool(fs.is_pinch_pressure_enabled)
        _set_pressure_buttons_state('pinch', 'disabled' if direct_active else 'normal')
        _set_pressure_buttons_state('direct', 'disabled' if pinch_active else 'normal')


def _register_indicator(feature_key, indicator):
    if feature_key in status_indicators:
        existing = status_indicators[feature_key]
        if isinstance(existing, list):
            existing.append(indicator)
        else:
            status_indicators[feature_key] = [existing, indicator]
    else:
        status_indicators[feature_key] = indicator


# Column sizes shared across single rows and grouped wrappers so On/Off
# buttons line up vertically across every section.
INDICATOR_COL_WIDTH = 22   # leftmost indicator column (px)
INDICATOR_COL_PAD = 4      # gap between indicator column and label (px)
LABEL_COL_CHARS = 18       # feature name column width in characters


def _add_indicator_slot(row, feature_key, default_font):
    """Add a fixed-width indicator slot with a centered indicator dot."""
    slot = tk.Frame(row, bg='white', width=INDICATOR_COL_WIDTH, height=1)
    slot.pack(side=tk.LEFT, fill=tk.Y, padx=(0, INDICATOR_COL_PAD))
    slot.pack_propagate(False)

    indicator = tk.Label(slot, text="●", font=(default_font[0], 12), bg='white', fg='gray')
    indicator.place(relx=0.5, rely=0.5, anchor='center')
    _register_indicator(feature_key, indicator)
    return slot, indicator


def _make_feature_row(parent, feature_name, feature_key, enable_cmd, disable_cmd,
                      output_widget=None, create_output=None, small_font=None, default_font=None,
                      with_indicator=True):
    """Create a feature row: [indicator slot][label][On][Off][output].

    When `with_indicator` is False, the row omits the indicator slot entirely
    — used by `_make_grouped_rows` whose wrapper already occupies that column
    so On/Off buttons stay vertically aligned with un-grouped rows.
    """
    row = tk.Frame(parent, bg='white')
    row.pack(fill=tk.X, pady=2)

    if with_indicator:
        _add_indicator_slot(row, feature_key, default_font)

    tk.Label(row, text=feature_name, font=small_font, bg='white', fg='black',
             width=LABEL_COL_CHARS, anchor='w').pack(side=tk.LEFT, padx=(0, 4))

    ttk.Button(row, text="On", command=enable_cmd, width=6).pack(side=tk.LEFT, padx=(0, 2))
    ttk.Button(row, text="Off", command=disable_cmd, width=6).pack(side=tk.LEFT, padx=(0, 5))

    if create_output is not None:
        output_widget = create_output(row)
    if output_widget is not None:
        output_widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
    return row, output_widget


def _make_grouped_rows(parent, group_key, build_rows, small_font, default_font):
    """Build a group of rows that share a single indicator on the left.

    The wrapper occupies the same indicator-column width as a single row, so
    sub-rows (built with `with_indicator=False`) line up perfectly with
    sibling un-grouped rows. The rail is drawn with `.place()` so it doesn't
    consume horizontal space.
    """
    container = tk.Frame(parent, bg='white')
    container.pack(fill=tk.X, pady=2)

    indicator_col = tk.Frame(container, bg='white', width=INDICATOR_COL_WIDTH, height=1)
    indicator_col.pack(side=tk.LEFT, fill=tk.Y, padx=(0, INDICATOR_COL_PAD))
    indicator_col.pack_propagate(False)

    indicator = tk.Label(indicator_col, text="●", font=(default_font[0], 12), bg='white', fg='gray')
    indicator.place(relx=0.5, rely=0.5, anchor='center')
    _register_indicator(group_key, indicator)

    rows_col = tk.Frame(container, bg='white')
    rows_col.pack(side=tk.LEFT, fill=tk.X, expand=True)

    # Decorative rail rendered with place() — does not consume layout space.
    rail = tk.Frame(rows_col, bg='#cfd8e0', width=2)
    rail.place(relx=0, rely=0.1, relheight=0.8, anchor='nw')

    # Build the inner rows WITHOUT their own indicator slot — the wrapper
    # already occupies that column on their behalf.
    build_rows(rows_col)
    return container


def _make_action_row(parent, label_text, buttons, small_font):
    """Row with no indicator/status — just a label and a list of buttons.

    `buttons` is a list of (text, command, width) tuples.
    Columns line up with feature rows because we reserve the same indicator
    spacer width and label width.
    """
    row = tk.Frame(parent, bg='white')
    row.pack(fill=tk.X, pady=2)

    # Empty spacer in place of the indicator slot, to keep column alignment.
    tk.Frame(row, bg='white', width=INDICATOR_COL_WIDTH, height=1).pack(
        side=tk.LEFT, fill=tk.Y, padx=(0, INDICATOR_COL_PAD)
    )

    tk.Label(row, text=label_text, font=small_font, bg='white', fg='black',
             width=LABEL_COL_CHARS, anchor='w').pack(side=tk.LEFT, padx=(0, 4))

    for i, (text, cmd, width) in enumerate(buttons):
        pad = (0, 2) if i < len(buttons) - 1 else (0, 5)
        ttk.Button(row, text=text, command=cmd, width=width).pack(side=tk.LEFT, padx=pad)
    return row


def _make_info_row(parent, label_text, key, small_font):
    """Read-only info row: [spacer][label][value]. Aligns with feature rows."""
    row = tk.Frame(parent, bg='white')
    row.pack(fill=tk.X, pady=2)

    tk.Frame(row, bg='white', width=INDICATOR_COL_WIDTH, height=1).pack(
        side=tk.LEFT, fill=tk.Y, padx=(0, INDICATOR_COL_PAD)
    )

    tk.Label(row, text=label_text, font=small_font, bg='white', fg='black',
             width=LABEL_COL_CHARS, anchor='w').pack(side=tk.LEFT, padx=(0, 4))

    value_label = tk.Label(row, text="--", font=small_font, bg='#e8f4fc', fg='#333',
                           anchor='w', padx=4)
    value_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
    device_info_labels[key] = value_label
    return value_label


def _make_pressure_row(parent, feature_name, feature_key, enable_cmd, disable_cmd, small_font, default_font):
    """Feature row with inline value label + progress bar (column-aligned)."""
    row = tk.Frame(parent, bg='white')
    row.pack(fill=tk.X, pady=2)

    _add_indicator_slot(row, feature_key, default_font)

    tk.Label(row, text=feature_name, font=small_font, bg='white', fg='black',
             width=LABEL_COL_CHARS, anchor='w').pack(side=tk.LEFT, padx=(0, 4))

    on_btn = ttk.Button(row, text="On", command=enable_cmd, width=6)
    on_btn.pack(side=tk.LEFT, padx=(0, 2))
    off_btn = ttk.Button(row, text="Off", command=disable_cmd, width=6)
    off_btn.pack(side=tk.LEFT, padx=(0, 5))

    out = tk.Frame(row, bg='white')
    out.pack(side=tk.LEFT, fill=tk.X, expand=True)
    val_lbl = tk.Label(out, text="0.00", font=small_font, bg='#e8f4fc', width=5, anchor='e')
    val_lbl.pack(side=tk.LEFT, padx=(0, 4))
    bar = ttk.Progressbar(out, orient=tk.HORIZONTAL, mode='determinate', maximum=1.0, value=0)
    bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
    return val_lbl, bar, on_btn, off_btn


def main():
    """Main function to set up and run the Tkinter GUI application."""
    global root, rms_bars, rms_labels, status_indicators
    global imu_acc_label, imu_gyro_label, snc_freq_label
    global navigation_x_label, navigation_y_label, navigation_direction_label
    global nav_axis_canvas, nav_axis_dot
    global gesture_label, air_touch_button_label
    global pinch_pressure_label, pinch_pressure_bar, direct_pressure_label, direct_pressure_bar
    global email_entry, password_entry, platform_var, sign_in_btn, sign_in_status_label

    status_indicators.clear()

    root = tk.Tk()
    root.title("Mudra BLE Device Manager")
    root.geometry("1520x850")
    root.minsize(1380, 680)
    
    # Configure style
    style = ttk.Style()
    # Use platform-appropriate theme
    if platform.system() == 'Darwin':  # macOS
        style.theme_use('aqua')
    else:
        style.theme_use('clam')
    
    # Platform-specific font
    if platform.system() == 'Darwin':  # macOS
        default_font = ('Helvetica', 10)
        small_font = ('Helvetica', 9)
    else:  # Windows/Linux
        default_font = ('Segoe UI', 10)
        small_font = ('Segoe UI', 9)
    
    main_container = tk.Frame(root, bg='#f0f0f0')
    main_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    # ========== TOP TOOLBAR (compact, no LabelFrame chrome) ==========
    toolbar = tk.Frame(main_container, bg='#f0f0f0')
    toolbar.pack(fill=tk.X, pady=(0, 6))

    # Row 1: Sign-in
    signin_row = tk.Frame(toolbar, bg='#f0f0f0')
    signin_row.pack(fill=tk.X, pady=(0, 4))

    tk.Label(signin_row, text="Email:", font=small_font, bg='#f0f0f0').pack(side=tk.LEFT, padx=(0, 4))
    email_entry = ttk.Entry(signin_row, width=22)
    email_entry.pack(side=tk.LEFT, padx=(0, 10))

    tk.Label(signin_row, text="Password:", font=small_font, bg='#f0f0f0').pack(side=tk.LEFT, padx=(0, 4))
    password_entry = ttk.Entry(signin_row, width=12, show="*")
    password_entry.pack(side=tk.LEFT, padx=(0, 10))

    tk.Label(signin_row, text="Platform:", font=small_font, bg='#f0f0f0').pack(side=tk.LEFT, padx=(0, 4))
    platform_var = tk.StringVar(value="Python")
    platform_combo = ttk.Combobox(signin_row, textvariable=platform_var, width=10, state="readonly")
    platform_combo["values"] = ("Python", "Windows", "macOS", "Linux")
    platform_combo.pack(side=tk.LEFT, padx=(0, 6))

    sign_in_btn = ttk.Button(signin_row, text="Sign In", command=on_sign_in, width=10)
    sign_in_btn.pack(side=tk.LEFT, padx=(0, 8))
    sign_in_status_label = ttk.Label(signin_row, text="", foreground="blue", background='#f0f0f0')
    sign_in_status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

    root.bind("<Return>", lambda event: on_sign_in())

    ttk.Separator(main_container, orient='horizontal').pack(fill=tk.X, pady=(0, 6))

    # ========== BODY: sidebar (devices) + main area (features) ==========
    body = tk.Frame(main_container, bg='#f0f0f0')
    body.pack(fill=tk.BOTH, expand=True)

    # Sidebar: scan → device rows (each with inline Connect/Disconnect)
    sidebar = ttk.LabelFrame(body, text="Discovered Devices", padding=6)
    sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))

    # Fixed sidebar width — keep it consistent regardless of device-name length.
    sidebar_width = 300

    scan_row = tk.Frame(sidebar, bg='#f0f0f0')
    scan_row.pack(fill=tk.X, pady=(0, 6))
    ttk.Button(scan_row, text="Start Scan", command=start_scan).pack(
        side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3)
    )
    ttk.Button(scan_row, text="Stop Scan", command=stop_scan).pack(
        side=tk.LEFT, fill=tk.X, expand=True
    )

    # Scrollable container for discovered-device rows.
    dev_canvas = tk.Canvas(sidebar, highlightthickness=0, bg='white', width=sidebar_width)
    dev_scroll = ttk.Scrollbar(sidebar, orient=tk.VERTICAL, command=dev_canvas.yview)
    dev_canvas.configure(yscrollcommand=dev_scroll.set)

    dev_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    dev_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    global devices_container
    devices_container = tk.Frame(dev_canvas, bg='white')
    dev_window = dev_canvas.create_window((0, 0), window=devices_container, anchor='nw')

    def _on_dev_configure(event=None):
        dev_canvas.configure(scrollregion=dev_canvas.bbox('all'))
        if dev_canvas.winfo_width() > 1:
            dev_canvas.itemconfig(dev_window, width=dev_canvas.winfo_width())

    def _on_dev_canvas_configure(event):
        if event.width > 1:
            dev_canvas.itemconfig(dev_window, width=event.width)

    devices_container.bind('<Configure>', _on_dev_configure)
    dev_canvas.bind('<Configure>', _on_dev_canvas_configure)

    def _on_dev_wheel(event):
        if platform.system() == 'Darwin':
            dev_canvas.yview_scroll(int(-1 * event.delta), 'units')
        else:
            dev_canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

    dev_canvas.bind('<MouseWheel>', _on_dev_wheel)
    devices_container.bind('<MouseWheel>', _on_dev_wheel)

    # Main area to the right of the sidebar
    main_area = tk.Frame(body, bg='#f0f0f0')
    main_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # ========== FEATURE GRID (2 columns) ==========
    features_grid = tk.Frame(main_area, bg='#f0f0f0')
    features_grid.pack(fill=tk.X)
    features_grid.columnconfigure(0, weight=1, uniform='cards')
    features_grid.columnconfigure(1, weight=1, uniform='cards')

    col_left = tk.Frame(features_grid, bg='#f0f0f0')
    col_left.grid(row=0, column=0, sticky='nsew', padx=(0, 4))
    col_right = tk.Frame(features_grid, bg='#f0f0f0')
    col_right.grid(row=0, column=1, sticky='nsew', padx=(4, 0))

    rms_bars = []
    rms_labels = []

    # --- Signals ---
    signals_frame = ttk.LabelFrame(col_left, text="Signals", padding=8)
    signals_frame.pack(fill=tk.X, pady=(0, 8))

    _, snc_freq_label = _make_feature_row(
        signals_frame, "SNC", "snc", enable_snc_feature, disable_snc_feature,
        create_output=lambda row: tk.Label(row, text="-- Hz", font=small_font, bg='#e8f4fc', fg='#333', anchor='w', padx=4),
        small_font=small_font, default_font=default_font,
    )

    snc_output = tk.Frame(signals_frame, bg='white')
    snc_output.pack(fill=tk.X, padx=(18, 0), pady=(2, 6))
    for i in range(3):
        rms_row = tk.Frame(snc_output, bg='white')
        rms_row.pack(fill=tk.X, pady=1)
        lbl = tk.Label(rms_row, text=f"RMS {i + 1}: 0.00", font=small_font, bg='white', width=10, anchor='w')
        lbl.pack(side=tk.LEFT)
        bar = ttk.Progressbar(rms_row, orient=tk.HORIZONTAL, mode='determinate', maximum=100, value=0)
        bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        rms_labels.append(lbl)
        rms_bars.append(bar)

    _, imu_acc_label = _make_feature_row(
        signals_frame, "Acc", "imu_acc", enable_imu_acc_feature, disable_imu_acc_feature,
        create_output=lambda row: tk.Label(row, text="-- Hz", font=small_font, bg='#e8f4fc', fg='#333', anchor='w', padx=4),
        small_font=small_font, default_font=default_font,
    )
    _, imu_gyro_label = _make_feature_row(
        signals_frame, "Gyro", "imu_gyro", enable_imu_gyro_feature, disable_imu_gyro_feature,
        create_output=lambda row: tk.Label(row, text="-- Hz", font=small_font, bg='#e8f4fc', fg='#333', anchor='w', padx=4),
        small_font=small_font, default_font=default_font,
    )

    # --- Gesture ---
    gesture_frame = ttk.LabelFrame(col_left, text="Gesture", padding=8)
    gesture_frame.pack(fill=tk.X, pady=(0, 8))

    gesture_label_holder = {}
    button_label_holder = {}

    def _build_gesture_rows(rows_col):
        _, gesture_label_holder['w'] = _make_feature_row(
            rows_col, "Discrete", "gesture", enable_gesture_feature, disable_gesture_feature,
            create_output=lambda row: tk.Label(row, text="--", font=small_font, bg='#e8f4fc', fg='#333', anchor='w', padx=4, width=18),
            small_font=small_font, default_font=default_font, with_indicator=False,
        )
        _, button_label_holder['w'] = _make_feature_row(
            rows_col, "Continuous", "gesture",
            enable_air_mouse_button_changed_feature, disable_air_mouse_button_changed_feature,
            create_output=lambda row: tk.Label(row, text="--", font=small_font, bg='#e8f4fc', fg='#333', anchor='w', padx=4, width=18),
            small_font=small_font, default_font=default_font, with_indicator=False,
        )

    _make_grouped_rows(gesture_frame, "gesture", _build_gesture_rows, small_font, default_font)
    gesture_label = gesture_label_holder['w']
    air_touch_button_label = button_label_holder['w']
    _make_feature_row(
        gesture_frame, "Gesture to HID", "gesture_to_hid",
        enable_gesture_to_hid, disable_gesture_to_hid,
        small_font=small_font, default_font=default_font,
    )

    # --- Pressure ---
    pressure_frame = ttk.LabelFrame(col_right, text="Pressure", padding=8)
    pressure_frame.pack(fill=tk.X, pady=(0, 8))

    direct_pressure_label, direct_pressure_bar, direct_on_btn, direct_off_btn = _make_pressure_row(
        pressure_frame, "Direct", "direct_pressure",
        enable_direct_pressure_feature, disable_direct_pressure_feature,
        small_font, default_font,
    )
    pinch_pressure_label, pinch_pressure_bar, pinch_on_btn, pinch_off_btn = _make_pressure_row(
        pressure_frame, "Pinch", "pinch_pressure",
        enable_pinch_pressure_feature, disable_pinch_pressure_feature,
        small_font, default_font,
    )

    pressure_buttons['direct_on'] = direct_on_btn
    pressure_buttons['direct_off'] = direct_off_btn
    pressure_buttons['pinch_on'] = pinch_on_btn
    pressure_buttons['pinch_off'] = pinch_off_btn

    # --- Navigation ---
    navigation_frame = ttk.LabelFrame(col_right, text="Navigation", padding=8)
    navigation_frame.pack(fill=tk.X, pady=(0, 8))

    nav_axis_holder = {}
    nav_dir_holder = {}

    def _create_nav_axis_output(row):
        global nav_axis_canvas, nav_axis_dot
        out = tk.Frame(row, bg='white')

        canvas = tk.Canvas(
            out, width=NAV_PAD_SIZE, height=NAV_PAD_SIZE,
            bg='#e8f4fc', highlightthickness=1, highlightbackground='#b8d4e0',
        )
        canvas.pack(side=tk.LEFT, padx=(0, 6))
        mid = NAV_PAD_SIZE // 2
        canvas.create_line(mid, 2, mid, NAV_PAD_SIZE - 2, fill='#c5d8e3')
        canvas.create_line(2, mid, NAV_PAD_SIZE - 2, mid, fill='#c5d8e3')
        r = NAV_DOT_RADIUS
        dot = canvas.create_oval(mid - r, mid - r, mid + r, mid + r,
                                 fill='#1e88e5', outline='')
        nav_axis_canvas = canvas
        nav_axis_dot = dot

        labels_col = tk.Frame(out, bg='white')
        labels_col.pack(side=tk.LEFT, fill=tk.Y)

        x_label = tk.Label(
            labels_col, text="X = 0", font=small_font, bg='#e8f4fc', fg='#333',
            anchor='w', padx=6, width=10,
        )
        x_label.pack(side=tk.TOP, anchor='w', pady=(0, 2))

        y_label = tk.Label(
            labels_col, text="Y = 0", font=small_font, bg='#e8f4fc', fg='#333',
            anchor='w', padx=6, width=10,
        )
        y_label.pack(side=tk.TOP, anchor='w')

        nav_axis_holder['x_label'] = x_label
        nav_axis_holder['y_label'] = y_label
        return out

    def _build_navigation_rows(rows_col):
        _make_feature_row(
            rows_col, "Axis", "navigation",
            enable_navigation_axis_feature, disable_navigation_axis_feature,
            create_output=_create_nav_axis_output,
            small_font=small_font, default_font=default_font, with_indicator=False,
        )
        _, nav_dir_holder['w'] = _make_feature_row(
            rows_col, "Direction", "navigation",
            enable_navigation_direction_feature, disable_navigation_direction_feature,
            create_output=lambda row: tk.Label(row, text="--", font=small_font,
                                               bg='#e8f4fc', fg='#333', anchor='w', padx=4, width=18),
            small_font=small_font, default_font=default_font, with_indicator=False,
        )

    _make_grouped_rows(navigation_frame, "navigation", _build_navigation_rows, small_font, default_font)
    navigation_x_label = nav_axis_holder['x_label']
    navigation_y_label = nav_axis_holder['y_label']
    navigation_direction_label = nav_dir_holder['w']
    _make_feature_row(
        navigation_frame, "Navigation to HID", "nav_to_hid",
        enable_navigation_to_hid, disable_navigation_to_hid,
        small_font=small_font, default_font=default_font,
    )
    _make_feature_row(
        navigation_frame, "Navigation to App", "nav_to_app",
        enable_navigation_to_app, disable_navigation_to_app,
        small_font=small_font, default_font=default_font,
    )

    # --- Hand (action-only section) ---
    hand_frame = ttk.LabelFrame(col_right, text="Hand", padding=8)
    hand_frame.pack(fill=tk.X, pady=(0, 8))
    _make_action_row(
        hand_frame, "Set Hand",
        [("Left", set_hand_left, 8), ("Right", set_hand_right, 8)],
        small_font,
    )

    # --- Recording (action-only section) ---
    recording_frame = ttk.LabelFrame(col_right, text="Recording", padding=8)
    recording_frame.pack(fill=tk.X, pady=(0, 8))
    _make_action_row(
        recording_frame, "Recording",
        [
            ("Start", start_recording_btn, 8),
            ("Stop", stop_recording_btn, 8),
            ("Get JSON", get_json_recording_btn, 10),
        ],
        small_font,
    )

    # --- Embedded (left column, same width as Signals / Gesture) ---
    embedded_frame = ttk.LabelFrame(col_left, text="Embedded Features", padding=8)
    embedded_frame.pack(fill=tk.X, pady=(0, 8))
    _make_feature_row(
        embedded_frame, "AirTouch", "embedded_airtouch",
        enable_embedded_airtouch_feature, disable_embedded_airtouch_feature,
        small_font=small_font, default_font=default_font,
    )

    # --- Device Info (updated via device callbacks on connect) ---
    device_info_frame = ttk.LabelFrame(col_left, text="Device Info", padding=8)
    device_info_frame.pack(fill=tk.X, pady=(0, 8))
    _make_info_row(device_info_frame, "Hand Type", "hand_type", small_font)
    _make_info_row(device_info_frame, "Charging", "is_charging", small_font)
    _make_info_row(device_info_frame, "Battery Level", "battery_level", small_font)
    _make_info_row(device_info_frame, "Serial Number", "serial_number", small_font)
    _make_info_row(device_info_frame, "Firmware Version", "firmware_version", small_font)

    root.mainloop()


if __name__ == "__main__":
    main()
