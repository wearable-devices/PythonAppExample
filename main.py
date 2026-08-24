import asyncio
import sys
import time
from pathlib import Path
from threading import Thread

# Ensure SDK.Python root is on sys.path when run as `python views/test.py`.
_sdk_root = Path(__file__).resolve().parent.parent
if str(_sdk_root) not in sys.path:
    sys.path.insert(0, str(_sdk_root))

import platform
import tkinter as tk
from tkinter import ttk

from mudra_sdk import Mudra, MudraDevice
from mudra_sdk.cloud import MudraServerClient, SigninRequest, UserInfoRequest, AuthStorage
from mudra_sdk.models.callbacks import MudraDelegate
from mudra_sdk.models.enums import HandType, RecordingDataType, SampleType

from views.sdk_debug_view import SdkDebugView
from views.theme import (
    BG,
    BG_CARD,
    BG_SELECTED,
    BG_SURFACE,
    BORDER,
    ERROR,
    INFO,
    OUTPUT_BG,
    OUTPUT_FG,
    SUCCESS,
    TEXT,
    TEXT_MUTED,
    WARNING,
    apply_dark_theme,
)

INDICATOR_COL_WIDTH = 22
INDICATOR_COL_PAD = 4
LABEL_COL_CHARS = 18

loop = None
loop_thread = None
root = None
sdk_debug_view = None

devices_list = []
device_rows = {}
selected_device_holder = [None]
devices_container = None
device_info_labels = {}
user_info_labels = {}
hand_var = None
sample_var = None

SAMPLE_TYPE_LABELS = {
    SampleType.sample_16_bit: '16 bit',
    SampleType.sample_24_bit: '24 bit',
}
SAMPLE_TYPE_RADIO_VALUES = {
    SampleType.sample_16_bit: '16',
    SampleType.sample_24_bit: '24',
}
SAMPLE_TYPE_FROM_RADIO = {value: sample_type for sample_type, value in SAMPLE_TYPE_RADIO_VALUES.items()}

PERMISSION_LEVEL_LABELS = {
    0: 'Administrator',
    1: 'Basic',
    2: 'Advanced',
    3: 'Developer',
    6: 'Beta',
}

USER_TYPE_LABELS = {
    0: 'Email',
    1: 'Apple',
    2: 'Email + Apple',
    3: 'Google',
    4: 'Email + Google',
}

mudra = Mudra()
mudra_server_client = MudraServerClient()


def get_selected_device():
    """Return the device currently selected in the sidebar (clicked row)."""
    return selected_device_holder[0]


def get_device_for_features():
    """Return the connected device used for SDK feature toggles."""
    selected = selected_device_holder[0]
    if selected is not None:
        info = device_rows.get(selected.name)
        if info and info['state'] == 'connected':
            return selected
    for info in device_rows.values():
        if info['state'] == 'connected':
            return info['device']
    return None


def get_loop():
    return loop


def _post_ui(callback):
    if root is not None and root.winfo_exists():
        root.after(0, callback)


def ensure_event_loop_running():
    """Ensure the background asyncio event loop is running."""
    global loop, loop_thread
    if loop_thread is None or not loop_thread.is_alive():
        loop_thread = Thread(target=run_event_loop, daemon=True)
        loop_thread.start()
        time.sleep(0.1)


def run_event_loop():
    global loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_forever()


def update_devices_list(device):
    """Called from the BLE delegate when a new device is discovered."""
    print(device.name)
    if device.name in device_rows:
        return
    devices_list.append(device)
    if root and root.winfo_exists():
        root.after(0, lambda d=device: _add_device_row(d))


def _add_device_row(device):
    if devices_container is None or not root.winfo_exists():
        return
    if device.name in device_rows:
        return

    row = tk.Frame(devices_container, bg=BG_SURFACE, highlightthickness=1,
                   highlightbackground=BORDER)
    row.pack(fill=tk.X, pady=1)

    status_dot = tk.Label(row, text="●", font=('Segoe UI', 10), bg=BG_SURFACE, fg=TEXT_MUTED, width=2)
    status_dot.pack(side=tk.LEFT, padx=(2, 0))

    name_label = tk.Label(row, text=device.name, font=('Segoe UI', 9), bg=BG_SURFACE,
                          fg=TEXT, anchor='w')
    name_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 4))

    action_btn = ttk.Button(row, text="Connect", width=12,
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

    def _on_click(_event, d=device):
        _select_device(d)

    for w in (row, name_label, status_dot):
        w.bind('<Button-1>', _on_click)

    _refresh_idle_buttons()


def _select_device(device):
    selected_device_holder[0] = device
    for info in device_rows.values():
        is_selected = info['device'] is device
        bg = BG_SELECTED if is_selected else BG_SURFACE
        info['row'].config(bg=bg)
        info['name_label'].config(bg=bg)
        info['status_dot'].config(bg=bg)
    _post_ui(lambda: _refresh_device_ui(device))


def _refresh_idle_buttons():
    busy = any(info['state'] in ('connecting', 'connected', 'disconnecting')
               for info in device_rows.values())
    for info in device_rows.values():
        if info['state'] == 'idle':
            info['action_btn'].config(state='disabled' if busy else 'normal')


def _set_device_state(device, state):
    info = device_rows.get(device.name)
    if info is None or not root.winfo_exists():
        return
    info['state'] = state
    btn = info['action_btn']
    dot = info['status_dot']
    if state == 'idle':
        btn.config(text='Connect', state='normal')
        dot.config(fg=TEXT_MUTED)
    elif state == 'connecting':
        btn.config(text='...', state='disabled')
        dot.config(fg=WARNING)
    elif state == 'connected':
        btn.config(text='Disconnect', state='normal')
        dot.config(fg=SUCCESS)
    elif state == 'disconnecting':
        btn.config(text='...', state='disabled')
        dot.config(fg=WARNING)
    _refresh_idle_buttons()


def _schedule_ble_connected_sync(device):
    """Move the row to connected as soon as the BLE link is up."""
    async def _sync():
        for _ in range(300):
            info = device_rows.get(device.name)
            if info is None:
                return
            if info['state'] in ('idle', 'connected', 'disconnecting'):
                return
            if await mudra.is_connected(device):
                _post_state(device, 'connected')
                return
            await asyncio.sleep(0.1)

    ensure_event_loop_running()
    if loop is not None:
        asyncio.run_coroutine_threadsafe(_sync(), loop)


async def _toggle_connection_coro(device):
    info = device_rows.get(device.name)
    state = info['state'] if info else 'idle'
    try:
        should_disconnect = (
            await mudra.is_connected(device)
            or state in ('connected', 'disconnecting')
        )
        if should_disconnect:
            await device.disconnect()
            if not await mudra.is_connected(device) and state != 'idle':
                _post_state(device, 'idle')
        else:
            await device.connect()
    except Exception as exc:
        print(f"Connection toggle failed for {device.name}: {exc}")
        _post_state(device, 'idle')


def _toggle_connection(device):
    if device.name not in device_rows:
        return
    _select_device(device)
    ensure_event_loop_running()
    if loop is None:
        return
    asyncio.run_coroutine_threadsafe(_toggle_connection_coro(device), loop)


def _post_state(device, state):
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
        _select_device(device)
        _post_state(device, 'connected')
        _setup_device_monitoring(device)
        if get_selected_device() is device:
            _post_ui(lambda d=device: _refresh_device_ui(d))

    def on_mudra_device_connecting(self, device: MudraDevice):
        print(f"Device connecting: {device.name}")
        _post_state(device, 'connecting')
        _schedule_ble_connected_sync(device)

    def on_mudra_device_connection_failed(self, device: MudraDevice, error: str):
        print(f"Connection failed: {device.name}, Error: {error}")
        _post_state(device, 'idle')

    def on_bluetooth_state_changed(self, state: bool):
        print(f"Bluetooth state changed: {'On' if state else 'Off'}")


mudra.set_delegate(MyMudraDelegate())


def start_scan():
    ensure_event_loop_running()
    if loop is not None:
        asyncio.run_coroutine_threadsafe(mudra.scan(), loop)


async def _find_connected_devices_coro():
    devices = await mudra.get_connected_devices()
    print(f"Found {len(devices)} connected Mudra device(s).")


def find_devices():
    ensure_event_loop_running()
    if loop is not None:
        asyncio.run_coroutine_threadsafe(_find_connected_devices_coro(), loop)


def stop_scan():
    if loop is not None:
        asyncio.run_coroutine_threadsafe(mudra.stop_scan(), loop)


def _run_on_loop(coro):
    if loop is not None:
        asyncio.run_coroutine_threadsafe(coro, loop)


def _set_hand(hand_type: HandType):
    device = get_device_for_features()
    if device is None:
        print("No connected device available to set hand.")
        return
    print(f"Set hand to {hand_type.name} called for device: {device.name}")
    _set_device_info_label('hand_type', hand_type.name.capitalize())
    _run_on_loop(device.set_hand(hand_type))


def _on_hand_radio_selected():
    if hand_var is None:
        return
    value = hand_var.get()
    if value not in ('left', 'right'):
        return
    _set_hand(HandType.left if value == 'left' else HandType.right)


def _make_hand_radio_row(parent, small_font):
    row = tk.Frame(parent, bg=BG_CARD)
    row.pack(fill=tk.X, pady=2)

    tk.Frame(row, bg=BG_CARD, width=INDICATOR_COL_WIDTH, height=1).pack(
        side=tk.LEFT, fill=tk.Y, padx=(0, INDICATOR_COL_PAD),
    )

    tk.Label(row, text="Hand", font=small_font, bg=BG_CARD, fg=TEXT,
             width=LABEL_COL_CHARS, anchor='w').pack(side=tk.LEFT, padx=(0, 4))

    options = tk.Frame(row, bg=BG_CARD)
    options.pack(side=tk.LEFT, fill=tk.X, expand=True)

    for hand_type, label in ((HandType.left, "Left"), (HandType.right, "Right")):
        ttk.Radiobutton(
            options,
            text=label,
            variable=hand_var,
            value=hand_type.name,
            command=_on_hand_radio_selected,
        ).pack(side=tk.LEFT, padx=(0, 12))


def _set_sample_type(sample_type: SampleType):
    device = get_device_for_features()
    if device is None:
        print("No connected device available to set sample type.")
        return
    print(f"Set sample type to {sample_type.name} called for device: {device.name}")
    _set_device_info_label('sample_type', SAMPLE_TYPE_LABELS[sample_type])
    _run_on_loop(device.set_sample_type(sample_type))


def _on_sample_radio_selected():
    if sample_var is None:
        return
    value = sample_var.get()
    sample_type = SAMPLE_TYPE_FROM_RADIO.get(value)
    if sample_type is None:
        return
    _set_sample_type(sample_type)


def _make_sample_type_radio_row(parent, small_font):
    row = tk.Frame(parent, bg=BG_CARD)
    row.pack(fill=tk.X, pady=2)

    tk.Frame(row, bg=BG_CARD, width=INDICATOR_COL_WIDTH, height=1).pack(
        side=tk.LEFT, fill=tk.Y, padx=(0, INDICATOR_COL_PAD),
    )

    tk.Label(row, text="Sample Type", font=small_font, bg=BG_CARD, fg=TEXT,
             width=LABEL_COL_CHARS, anchor='w').pack(side=tk.LEFT, padx=(0, 4))

    options = tk.Frame(row, bg=BG_CARD)
    options.pack(side=tk.LEFT, fill=tk.X, expand=True)

    for sample_type, label in SAMPLE_TYPE_LABELS.items():
        ttk.Radiobutton(
            options,
            text=label,
            variable=sample_var,
            value=SAMPLE_TYPE_RADIO_VALUES[sample_type],
            command=_on_sample_radio_selected,
        ).pack(side=tk.LEFT, padx=(0, 12))


def _make_action_row(parent, label_text, buttons, small_font):
    row = tk.Frame(parent, bg=BG_CARD)
    row.pack(fill=tk.X, pady=2)

    tk.Frame(row, bg=BG_CARD, width=INDICATOR_COL_WIDTH, height=1).pack(
        side=tk.LEFT, fill=tk.Y, padx=(0, INDICATOR_COL_PAD),
    )

    tk.Label(row, text=label_text, font=small_font, bg=BG_CARD, fg=TEXT,
             width=LABEL_COL_CHARS, anchor='w').pack(side=tk.LEFT, padx=(0, 4))

    for i, (text, cmd, width) in enumerate(buttons):
        pad = (0, 2) if i < len(buttons) - 1 else (0, 5)
        ttk.Button(row, text=text, command=cmd, width=width).pack(side=tk.LEFT, padx=pad)
    return row


def _make_info_row(parent, label_text, key, small_font, labels=None):
    if labels is None:
        labels = device_info_labels
    row = tk.Frame(parent, bg=BG_CARD)
    row.pack(fill=tk.X, pady=2)

    tk.Frame(row, bg=BG_CARD, width=INDICATOR_COL_WIDTH, height=1).pack(
        side=tk.LEFT, fill=tk.Y, padx=(0, INDICATOR_COL_PAD),
    )

    tk.Label(row, text=label_text, font=small_font, bg=BG_CARD, fg=TEXT,
             width=LABEL_COL_CHARS, anchor='w').pack(side=tk.LEFT, padx=(0, 4))

    value_label = tk.Label(row, text="--", font=small_font, bg=OUTPUT_BG, fg=OUTPUT_FG,
                           anchor='w', padx=4)
    value_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
    labels[key] = value_label
    return value_label


def _set_info_label(labels, key, text):
    label = labels.get(key)
    if label is not None:
        label.config(text=text)


def _set_device_info_label(key, text):
    _set_info_label(device_info_labels, key, text)


def _set_user_info_label(key, text):
    _set_info_label(user_info_labels, key, text)


def _clear_user_info_labels():
    defaults = {
        'user_name': '--',
        'user_email': '--',
        'user_permission': '--',
        'user_account_type': '--',
    }
    for key, text in defaults.items():
        _set_user_info_label(key, text)


def _apply_user_info_response(response):
    if not root or not root.winfo_exists():
        return

    first = (response.get('firstName') or '').strip()
    last = (response.get('lastName') or '').strip()
    name = f"{first} {last}".strip() or '--'
    _set_user_info_label('user_name', name)

    email = (response.get('email') or AuthStorage().email or '--').strip() or '--'
    _set_user_info_label('user_email', email)

    permission = response.get('permissionLevel')
    _set_user_info_label(
        'user_permission',
        PERMISSION_LEVEL_LABELS.get(permission, str(permission) if permission is not None else '--'),
    )

    user_type = response.get('userType')
    _set_user_info_label(
        'user_account_type',
        USER_TYPE_LABELS.get(user_type, str(user_type) if user_type is not None else '--'),
    )


def _apply_user_info_from_storage():
    if not root or not root.winfo_exists():
        return
    storage = AuthStorage()
    email = storage.email.strip() if storage.email else '--'
    _set_user_info_label('user_email', email or '--')


def _fetch_user_info():
    """Fetch user profile from the API and update the settings tab."""
    if mudra_server_client.get_access_token() is None:
        _post_ui(_clear_user_info_labels)
        return

    def _work():
        try:
            response = mudra_server_client.get_user_info_api_call(UserInfoRequest().to_json())
            _post_ui(lambda r=response: _apply_user_info_response(r))
        except Exception as exc:
            print(f"Failed to fetch user info: {exc}")
            _post_ui(_apply_user_info_from_storage)

    Thread(target=_work, daemon=True).start()


def _refresh_hand_type(device):
    if not root or not root.winfo_exists():
        return
    hand = device.get_hand_type() if device is not None else None
    text = hand.name.capitalize() if hand is not None else "--"
    _set_device_info_label('hand_type', text)
    if hand_var is not None:
        hand_var.set(hand.name if hand is not None else '')


def _refresh_sample_type(device):
    if not root or not root.winfo_exists():
        return
    if device is None:
        _set_device_info_label('sample_type', '--')
        if sample_var is not None:
            sample_var.set('')
        return
    sample_type = device.get_sample_type()
    _set_device_info_label('sample_type', SAMPLE_TYPE_LABELS.get(sample_type, '--') if sample_type is not None else '--')
    if sample_var is not None:
        sample_var.set(SAMPLE_TYPE_RADIO_VALUES.get(sample_type, '') if sample_type is not None else '')


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
    if sdk_debug_view is not None:
        sdk_debug_view.refresh_status_indicators(device)
    if device is None:
        _clear_device_info_labels()
        _refresh_hand_type(None)
        _refresh_sample_type(None)
        return
    _refresh_hand_type(device)
    _refresh_sample_type(device)
    _refresh_charging(device)
    _refresh_battery_level(device)
    _refresh_static_device_info(device)


def _is_connected_device(device):
    info = device_rows.get(device.name)
    return info is not None and info['state'] == 'connected'


def _setup_device_monitoring(device):
    ensure_event_loop_running()
    if loop is None:
        return

    def on_firmware_status_changed(is_firmware_status_changed: bool):
        if _is_connected_device(device):
            def _update_ui(d=device):
                if sdk_debug_view is not None:
                    sdk_debug_view.refresh_status_indicators(d)
                _refresh_hand_type(d)
                _refresh_sample_type(d)
            _post_ui(_update_ui)

    def on_charging_state_changed(is_charging):
        if _is_connected_device(device):
            text = "Yes" if is_charging else "No"
            _post_ui(lambda t=text: _set_device_info_label('is_charging', t))

    def on_battery_level_changed(level):
        if _is_connected_device(device):
            _post_ui(lambda l=level: _set_device_info_label('battery_level', f"{l}%"))

    async def _register():
        await device.set_on_firmware_status_changed(on_firmware_status_changed)
        await device.set_on_charging_state_changed(on_charging_state_changed)
        await device.set_on_battery_level_changed(on_battery_level_changed)

    asyncio.run_coroutine_threadsafe(_register(), loop)


def _teardown_device_monitoring(device):
    ensure_event_loop_running()
    if loop is None:
        return

    async def _unregister():
        await device.set_on_firmware_status_changed(None)
        await device.set_on_charging_state_changed(None)
        await device.set_on_battery_level_changed(None)

    asyncio.run_coroutine_threadsafe(_unregister(), loop)


def start_recording_btn():
    device = get_selected_device()
    if device is None:
        print("No device selected to start recording.")
        return
    ensure_event_loop_running()
    recording_types = [
        RecordingDataType.sncTS, RecordingDataType.sncAppTS,
        RecordingDataType.snc1, RecordingDataType.snc2, RecordingDataType.snc3,
        RecordingDataType.accTS, RecordingDataType.accAppTS,
        RecordingDataType.acc1, RecordingDataType.acc2, RecordingDataType.acc3,
        RecordingDataType.pressure, RecordingDataType.endAppTS,
    ]
    device.enable_recording()
    _run_on_loop(device.start_recording(
        recording_description="SDK Python test recording",
        app_name="mudra link",
        recording_types=recording_types,
    ))


def stop_recording_btn():
    device = get_selected_device()
    if device is None:
        print("No device selected to stop recording.")
        return
    ensure_event_loop_running()

    async def stop_then_upload():
        await device.stop_recording()
        await device.upload_recording()

    _run_on_loop(stop_then_upload())


def get_json_recording_btn():
    device = get_selected_device()
    if device is None:
        print("No device selected to get recording.")
        return
    ensure_event_loop_running()
    json_str = device.get_json_recording()
    print("Recording JSON:")
    print(json_str)


def _build_settings_tab(parent, small_font):
    global hand_var, sample_var

    panels_grid = tk.Frame(parent, bg=BG)
    panels_grid.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
    panels_grid.columnconfigure(0, weight=1, uniform='app_cards')
    panels_grid.columnconfigure(1, weight=1, uniform='app_cards')

    col_left = tk.Frame(panels_grid, bg=BG)
    col_left.grid(row=0, column=0, sticky='nsew', padx=(0, 4))
    col_right = tk.Frame(panels_grid, bg=BG)
    col_right.grid(row=0, column=1, sticky='nsew', padx=(4, 0))

    user_info_frame = ttk.LabelFrame(col_left, text="User Info", padding=6)
    user_info_frame.pack(fill=tk.X, pady=(0, 6))
    _make_info_row(user_info_frame, "Name", "user_name", small_font, user_info_labels)
    _make_info_row(user_info_frame, "Email", "user_email", small_font, user_info_labels)
    _make_info_row(user_info_frame, "Permission", "user_permission", small_font, user_info_labels)
    _make_info_row(user_info_frame, "Account Type", "user_account_type", small_font, user_info_labels)

    device_info_frame = ttk.LabelFrame(col_left, text="Device Info", padding=6)
    device_info_frame.pack(fill=tk.X, pady=(0, 6))
    _make_info_row(device_info_frame, "Hand Type", "hand_type", small_font)
    _make_info_row(device_info_frame, "Sample Type", "sample_type", small_font)
    _make_info_row(device_info_frame, "Charging", "is_charging", small_font)
    _make_info_row(device_info_frame, "Battery Level", "battery_level", small_font)
    _make_info_row(device_info_frame, "Serial Number", "serial_number", small_font)
    _make_info_row(device_info_frame, "Firmware Version", "firmware_version", small_font)

    hand_frame = ttk.LabelFrame(col_right, text="Hand", padding=6)
    hand_frame.pack(fill=tk.X, pady=(0, 6))
    hand_var = tk.StringVar(value='')
    _make_hand_radio_row(hand_frame, small_font)

    sample_type_frame = ttk.LabelFrame(col_right, text="Sample Type", padding=6)
    sample_type_frame.pack(fill=tk.X, pady=(0, 6))
    sample_var = tk.StringVar(value='')
    _make_sample_type_radio_row(sample_type_frame, small_font)

    recording_frame = ttk.LabelFrame(col_right, text="Recording", padding=6)
    recording_frame.pack(fill=tk.X, pady=(0, 6))
    _make_action_row(
        recording_frame, "Recording",
        [
            ("Start", start_recording_btn, 8),
            ("Stop", stop_recording_btn, 8),
            ("Get JSON", get_json_recording_btn, 10),
        ],
        small_font,
    )


def on_sign_in():
    email = email_entry.get().strip()
    password = password_entry.get().strip()
    platform_val = 'Python'

    if not email:
        print("Error: Please enter your email address")
        sign_in_status_label.config(text="Please enter your email.", foreground=ERROR)
        return
    if not password:
        print("Error: Please enter your password")
        sign_in_status_label.config(text="Please enter your password.", foreground=ERROR)
        return

    sign_in_btn.config(state="disabled")
    sign_in_status_label.config(text="Signing in...", foreground=INFO)
    root.update()

    try:
        signin_request = SigninRequest(
            email=email,
            password=password,
            platform=platform_val,
            application="Python Test Application",
        )
        print(f"\n{'=' * 50}")
        print("Signing in...")
        print(f"Email: {email}")
        print(f"Platform: {platform_val}")
        print(f"{'=' * 50}\n")

        response = mudra_server_client.sign_in_api_call(signin_request.to_json())

        print("✓ Sign in successful!")
        print(f"\nResponse:")
        print(f"  Access Token: {response.get('accessToken', 'N/A')}")
        print(f"  Refresh Token: {response.get('refreshToken', 'N/A')}")
        if isinstance(response, dict):
            print(f"\nFull Response:")
            for key, value in response.items():
                print(f"  {key}: {value}")
        print(f"\n{'=' * 50}\n")

        sign_in_status_label.config(
            text="✓ Sign in successful! Check console for details.", foreground=SUCCESS,
        )
        password_entry.delete(0, tk.END)
        email_entry.delete(0, tk.END)
        _fetch_user_info()
    except Exception as e:
        error_message = str(e)
        print(f"\n{'=' * 50}")
        print("✗ Sign in failed!")
        print(f"Error: {error_message}")
        print(f"{'=' * 50}\n")
        sign_in_status_label.config(text=f"✗ Error: {error_message}", foreground=ERROR)
    finally:
        sign_in_btn.config(state="normal")
        root.update()


def _build_device_sidebar(body, small_font):
    global devices_container

    sidebar = ttk.LabelFrame(body, text="Discovered Devices", padding=6)
    sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))

    sidebar_width = 300

    scan_row = tk.Frame(sidebar, bg=BG)
    scan_row.pack(fill=tk.X, pady=(0, 6))
    ttk.Button(scan_row, text="Find Devices", command=find_devices).pack(
        side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3),
    )
    ttk.Button(scan_row, text="Start Scan", command=start_scan).pack(
        side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3),
    )
    ttk.Button(scan_row, text="Stop Scan", command=stop_scan).pack(
        side=tk.LEFT, fill=tk.X, expand=True,
    )

    dev_canvas = tk.Canvas(sidebar, highlightthickness=0, bg=BG_SURFACE, width=sidebar_width)
    dev_scroll = ttk.Scrollbar(sidebar, orient=tk.VERTICAL, command=dev_canvas.yview)
    dev_canvas.configure(yscrollcommand=dev_scroll.set)

    dev_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    dev_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    devices_container = tk.Frame(dev_canvas, bg=BG_SURFACE)
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


def main():
    global root, sdk_debug_view
    global email_entry, password_entry, sign_in_btn, sign_in_status_label

    root = tk.Tk()
    root.title("Mudra BLE Device Manager")
    root.geometry("1444x808")
    root.minsize(1311, 646)

    style = ttk.Style()
    if platform.system() == 'Darwin':
        small_font = ('Helvetica', 9)
    else:
        small_font = ('Segoe UI', 9)
    apply_dark_theme(root, style)

    main_container = tk.Frame(root, bg=BG)
    main_container.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    toolbar = tk.Frame(main_container, bg=BG)
    toolbar.pack(fill=tk.X, pady=(0, 6))

    signin_row = tk.Frame(toolbar, bg=BG)
    signin_row.pack(fill=tk.X, pady=(0, 4))

    tk.Label(signin_row, text="Email:", font=small_font, bg=BG, fg=TEXT).pack(side=tk.LEFT, padx=(0, 4))
    email_entry = ttk.Entry(signin_row, width=22)
    email_entry.pack(side=tk.LEFT, padx=(0, 10))

    tk.Label(signin_row, text="Password:", font=small_font, bg=BG, fg=TEXT).pack(side=tk.LEFT, padx=(0, 4))
    password_entry = ttk.Entry(signin_row, width=22, show="*")
    password_entry.pack(side=tk.LEFT, padx=(0, 10))

    sign_in_btn = ttk.Button(signin_row, text="Sign In", command=on_sign_in, width=10)
    sign_in_btn.pack(side=tk.LEFT, padx=(0, 8))
    sign_in_status_label = ttk.Label(signin_row, text="", foreground=INFO, background=BG)
    sign_in_status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

    root.bind("<Return>", lambda event: on_sign_in())

    ttk.Separator(main_container, orient='horizontal').pack(fill=tk.X, pady=(0, 6))

    body = tk.Frame(main_container, bg=BG)
    body.pack(fill=tk.BOTH, expand=True)

    _build_device_sidebar(body, small_font)

    main_area = tk.Frame(body, bg=BG)
    main_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    tab_bar = tk.Frame(main_area, bg=BG)
    tab_bar.pack(fill=tk.X, pady=(0, 10))
    tab_inner = tk.Frame(tab_bar, bg=BG)
    tab_inner.pack(anchor='center')

    content_area = tk.Frame(main_area, bg=BG)
    content_area.pack(fill=tk.BOTH, expand=True)

    settings_tab = tk.Frame(content_area, bg=BG)
    sdk_debug_tab = tk.Frame(content_area, bg=BG)
    tab_frames = {'settings': settings_tab, 'debug': sdk_debug_tab}
    tab_buttons = {}
    active_tab = {'key': None}

    for frame in tab_frames.values():
        frame.place(relx=0, rely=0, relwidth=1, relheight=1)

    def show_tab(key):
        if active_tab['key'] == key:
            return
        active_tab['key'] = key
        tab_frames[key].tkraise()
        for tab_key, button in tab_buttons.items():
            button.configure(
                style='AppTabSelected.TButton' if tab_key == key else 'AppTab.TButton'
            )

    for tab_key, label in (('settings', 'Settings'), ('debug', 'SDK Debug')):
        button = ttk.Button(
            tab_inner,
            text=label,
            style='AppTab.TButton',
            command=lambda key=tab_key: show_tab(key),
        )
        button.pack(side=tk.LEFT, padx=2)
        tab_buttons[tab_key] = button

    _build_settings_tab(settings_tab, small_font)

    sdk_debug_view = SdkDebugView(
        sdk_debug_tab,
        root=root,
        get_feature_device=get_device_for_features,
        get_loop=get_loop,
        ensure_event_loop_running=ensure_event_loop_running,
    )
    sdk_debug_view.build()

    show_tab('settings')

    root.mainloop()


if __name__ == "__main__":
    main()
