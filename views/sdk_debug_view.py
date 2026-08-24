"""SDK debug page — Python counterpart of Mudra Link's SDKDebugView."""

import asyncio
import platform

import tkinter as tk
from tkinter import ttk

from mudra_sdk.models.enums import (
    FirmwareTarget,
    NavigationDirectionGesture,
    PressureType,
)

from views.toggle_switch import ToggleSwitch
from views.theme import (
    BG,
    BG_CARD,
    ERROR,
    NAV_CANVAS_BG,
    NAV_DOT,
    NAV_GRID,
    OUTPUT_BG,
    OUTPUT_FG,
    RAIL,
    SUCCESS,
    TEXT,
)

INDICATOR_COL_WIDTH = 22
INDICATOR_COL_PAD = 4
LABEL_COL_CHARS = 18

NAVIGATION_SMOOTHING_WINDOW = 5
NAV_AXIS_RANGE = 200
NAV_PAD_SIZE = 82
NAV_DOT_RADIUS = 4
SECTION_PAD = 6
SECTION_GAP = 6
ROW_PADY = 1


class SdkDebugView:
    """SDK debug feature toggles and live outputs (signals, gesture, pressure, navigation, embedded)."""

    def __init__(self, parent, *, root, get_feature_device, get_loop, ensure_event_loop_running):
        self.parent = parent
        self.root = root
        self.get_feature_device = get_feature_device
        self.get_loop = get_loop
        self.ensure_event_loop_running = ensure_event_loop_running

        self.status_indicators = {}
        self.feature_toggles = {}
        self.pressure_buttons = {}
        self.feature_active = {
            'snc': False,
            'imu_acc': False,
            'imu_gyro': False,
            'pinch_pressure': False,
            'direct_pressure': False,
            'navigation_axis': False,
            'navigation_direction': False,
            'gesture': False,
            'button_changed': False,
            'embedded_airtouch': False,
            'nav_to_app': False,
            'gesture_to_hid': False,
            'nav_to_hid': False,
        }
        self.navigation_history = {'x': [], 'y': []}

        self.snc_freq_label = None
        self.rms_bars = []
        self.rms_labels = []
        self.imu_acc_label = None
        self.imu_gyro_label = None
        self.navigation_x_label = None
        self.navigation_y_label = None
        self.navigation_direction_label = None
        self.gesture_label = None
        self.air_touch_button_label = None
        self.pinch_pressure_label = None
        self.pinch_pressure_bar = None
        self.direct_pressure_label = None
        self.direct_pressure_bar = None
        self.nav_axis_canvas = None
        self.nav_axis_dot = None

        if platform.system() == 'Darwin':
            self.default_font = ('Helvetica', 10)
            self.small_font = ('Helvetica', 9)
        else:
            self.default_font = ('Segoe UI', 10)
            self.small_font = ('Segoe UI', 9)

    def build(self):
        """Build the SDK debug feature grid inside `self.parent`."""
        self.status_indicators.clear()
        self.feature_toggles.clear()

        features_grid = tk.Frame(self.parent, bg=BG)
        features_grid.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        features_grid.columnconfigure(0, weight=1, uniform='cards')
        features_grid.columnconfigure(1, weight=1, uniform='cards')

        col_left = tk.Frame(features_grid, bg=BG)
        col_left.grid(row=0, column=0, sticky='nsew', padx=(0, 4))
        col_right = tk.Frame(features_grid, bg=BG)
        col_right.grid(row=0, column=1, sticky='nsew', padx=(4, 0))

        self.rms_bars = []
        self.rms_labels = []

        self._build_navigation_section(col_left)
        self._build_gesture_section(col_left)
        self._build_pressure_section(col_right)
        self._build_signals_section(col_right)
        self._build_embedded_section(col_right)

    # ------------------------------------------------------------------ UI builders

    def _build_signals_section(self, col_left):
        signals_frame = ttk.LabelFrame(col_left, text="Signals", padding=SECTION_PAD)
        signals_frame.pack(fill=tk.X, pady=(0, SECTION_GAP))

        _, self.snc_freq_label = self._make_feature_row(
            signals_frame, "SNC", "snc", self.enable_snc_feature, self.disable_snc_feature,
            create_output=lambda row: tk.Label(
                row, text="-- Hz", font=self.small_font, bg=OUTPUT_BG, fg=OUTPUT_FG, anchor='w', padx=4,
            ),
        )

        snc_output = tk.Frame(signals_frame, bg=BG_CARD)
        snc_output.pack(fill=tk.X, padx=(18, 0), pady=(2, 4))
        for i in range(3):
            rms_row = tk.Frame(snc_output, bg=BG_CARD)
            rms_row.pack(fill=tk.X, pady=1)
            lbl = tk.Label(rms_row, text=f"RMS {i + 1}: 0.00", font=self.small_font, bg=BG_CARD, fg=TEXT, width=10, anchor='w')
            lbl.pack(side=tk.LEFT)
            bar = ttk.Progressbar(rms_row, orient=tk.HORIZONTAL, mode='determinate', maximum=100, value=0)
            bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
            self.rms_labels.append(lbl)
            self.rms_bars.append(bar)

        _, self.imu_acc_label = self._make_feature_row(
            signals_frame, "Acc", "imu_acc", self.enable_imu_acc_feature, self.disable_imu_acc_feature,
            create_output=lambda row: tk.Label(
                row, text="-- Hz", font=self.small_font, bg=OUTPUT_BG, fg=OUTPUT_FG, anchor='w', padx=4,
            ),
        )
        _, self.imu_gyro_label = self._make_feature_row(
            signals_frame, "Gyro", "imu_gyro", self.enable_imu_gyro_feature, self.disable_imu_gyro_feature,
            create_output=lambda row: tk.Label(
                row, text="-- Hz", font=self.small_font, bg=OUTPUT_BG, fg=OUTPUT_FG, anchor='w', padx=4,
            ),
        )

    def _build_gesture_section(self, col_left):
        gesture_frame = ttk.LabelFrame(col_left, text="Gesture", padding=SECTION_PAD)
        gesture_frame.pack(fill=tk.X, pady=(0, SECTION_GAP))

        self._make_feature_row(
            gesture_frame, "Gesture to HID", "gesture_to_hid",
            self.enable_gesture_to_hid, self.disable_gesture_to_hid,
        )

        gesture_label_holder = {}
        button_label_holder = {}

        def _build_gesture_rows(rows_col):
            _, gesture_label_holder['w'] = self._make_feature_row(
                rows_col, "Discrete", "gesture", self.enable_gesture_feature, self.disable_gesture_feature,
                toggle_key='gesture',
                create_output=lambda row: tk.Label(
                    row, text="--", font=self.small_font, bg=OUTPUT_BG, fg=OUTPUT_FG, anchor='w', padx=4, width=18,
                ),
                with_indicator=False,
            )
            _, button_label_holder['w'] = self._make_feature_row(
                rows_col, "Continuous", "gesture",
                self.enable_air_mouse_button_changed_feature, self.disable_air_mouse_button_changed_feature,
                toggle_key='button_changed',
                create_output=lambda row: tk.Label(
                    row, text="--", font=self.small_font, bg=OUTPUT_BG, fg=OUTPUT_FG, anchor='w', padx=4, width=18,
                ),
                with_indicator=False,
            )

        self._make_grouped_rows(
            gesture_frame, "gesture", _build_gesture_rows, rows_top_pad=SECTION_PAD,
        )
        self.gesture_label = gesture_label_holder['w']
        self.air_touch_button_label = button_label_holder['w']

    def _build_embedded_section(self, col_left):
        embedded_frame = ttk.LabelFrame(col_left, text="Embedded Features", padding=SECTION_PAD)
        embedded_frame.pack(fill=tk.X, pady=(0, SECTION_GAP))
        self._make_feature_row(
            embedded_frame, "AirTouch", "embedded_airtouch",
            self.enable_embedded_airtouch_feature, self.disable_embedded_airtouch_feature,
        )

    def _build_pressure_section(self, col_right):
        pressure_frame = ttk.LabelFrame(col_right, text="Pressure", padding=SECTION_PAD)
        pressure_frame.pack(fill=tk.X, pady=(0, SECTION_GAP))

        self.direct_pressure_label, self.direct_pressure_bar, direct_toggle = self._make_pressure_row(
            pressure_frame, "Direct", "direct_pressure",
            self.enable_direct_pressure_feature, self.disable_direct_pressure_feature,
        )
        self.pinch_pressure_label, self.pinch_pressure_bar, pinch_toggle = self._make_pressure_row(
            pressure_frame, "Pinch", "pinch_pressure",
            self.enable_pinch_pressure_feature, self.disable_pinch_pressure_feature,
        )

        self.pressure_buttons['direct'] = direct_toggle
        self.pressure_buttons['pinch'] = pinch_toggle

    def _build_navigation_section(self, col_right):
        navigation_frame = ttk.LabelFrame(col_right, text="Navigation", padding=SECTION_PAD)
        navigation_frame.pack(fill=tk.X, pady=(0, SECTION_GAP))

        self._make_dual_feature_row(
            navigation_frame,
            (
                "Navigation to HID", "nav_to_hid",
                self.enable_navigation_to_hid, self.disable_navigation_to_hid,
            ),
            (
                "Navigation to App", "nav_to_app",
                self.enable_navigation_to_app, self.disable_navigation_to_app,
            ),
        )

        nav_axis_holder = {}
        nav_dir_holder = {}

        def _create_nav_axis_output(row):
            out = tk.Frame(row, bg=BG_CARD)

            canvas = tk.Canvas(
                out, width=NAV_PAD_SIZE, height=NAV_PAD_SIZE,
                bg=NAV_CANVAS_BG, highlightthickness=1, highlightbackground=NAV_GRID,
            )
            canvas.pack(side=tk.LEFT, padx=(0, 6))
            mid = NAV_PAD_SIZE // 2
            canvas.create_line(mid, 2, mid, NAV_PAD_SIZE - 2, fill=NAV_GRID)
            canvas.create_line(2, mid, NAV_PAD_SIZE - 2, mid, fill=NAV_GRID)
            r = NAV_DOT_RADIUS
            dot = canvas.create_oval(mid - r, mid - r, mid + r, mid + r, fill=NAV_DOT, outline='')
            self.nav_axis_canvas = canvas
            self.nav_axis_dot = dot

            labels_col = tk.Frame(out, bg=BG_CARD)
            labels_col.pack(side=tk.LEFT, fill=tk.Y)

            x_label = tk.Label(
                labels_col, text="X = 0", font=self.small_font, bg=OUTPUT_BG, fg=OUTPUT_FG,
                anchor='w', padx=6, width=10,
            )
            x_label.pack(side=tk.TOP, anchor='w', pady=(0, 2))

            y_label = tk.Label(
                labels_col, text="Y = 0", font=self.small_font, bg=OUTPUT_BG, fg=OUTPUT_FG,
                anchor='w', padx=6, width=10,
            )
            y_label.pack(side=tk.TOP, anchor='w')

            nav_axis_holder['x_label'] = x_label
            nav_axis_holder['y_label'] = y_label
            return out

        def _build_navigation_rows(rows_col):
            self._make_feature_row(
                rows_col, "Axis", "navigation",
                self.enable_navigation_axis_feature, self.disable_navigation_axis_feature,
                toggle_key='navigation_axis',
                create_output=_create_nav_axis_output,
                with_indicator=False,
            )
            _, nav_dir_holder['w'] = self._make_feature_row(
                rows_col, "Direction", "navigation",
                self.enable_navigation_direction_feature, self.disable_navigation_direction_feature,
                toggle_key='navigation_direction',
                create_output=lambda row: tk.Label(
                    row, text="--", font=self.small_font,
                    bg=OUTPUT_BG, fg=OUTPUT_FG, anchor='w', padx=4, width=18,
                ),
                with_indicator=False,
            )

        self._make_grouped_rows(
            navigation_frame, "navigation", _build_navigation_rows, rows_top_pad=SECTION_PAD,
        )
        self.navigation_x_label = nav_axis_holder['x_label']
        self.navigation_y_label = nav_axis_holder['y_label']
        self.navigation_direction_label = nav_dir_holder['w']

    # ------------------------------------------------------------------ Row helpers

    def _register_indicator(self, feature_key, indicator):
        if feature_key in self.status_indicators:
            existing = self.status_indicators[feature_key]
            if isinstance(existing, list):
                existing.append(indicator)
            else:
                self.status_indicators[feature_key] = [existing, indicator]
        else:
            self.status_indicators[feature_key] = indicator

    def _add_indicator_slot(self, row, feature_key):
        slot = tk.Frame(row, bg=BG_CARD, width=INDICATOR_COL_WIDTH, height=1)
        slot.pack(side=tk.LEFT, fill=tk.Y, padx=(0, INDICATOR_COL_PAD))
        slot.pack_propagate(False)

        indicator = tk.Label(slot, text="●", font=(self.default_font[0], 12), bg=BG_CARD, fg=ERROR)
        indicator.place(relx=0.5, rely=0.5, anchor='center')
        self._register_indicator(feature_key, indicator)
        return slot, indicator

    def _make_feature_row(self, parent, feature_name, feature_key, enable_cmd, disable_cmd,
                          output_widget=None, create_output=None, with_indicator=True, toggle_key=None):
        row = tk.Frame(parent, bg=BG_CARD)
        row.pack(fill=tk.X, pady=ROW_PADY)

        if with_indicator:
            self._add_indicator_slot(row, feature_key)

        tk.Label(row, text=feature_name, font=self.small_font, bg=BG_CARD, fg=TEXT,
                 width=LABEL_COL_CHARS, anchor='w').pack(side=tk.LEFT, padx=(0, 4))

        def _on_toggle(enabled):
            if enabled:
                enable_cmd()
            else:
                disable_cmd()

        toggle = ToggleSwitch(row, _on_toggle, bg=BG_CARD)
        toggle.pack(side=tk.LEFT, padx=(0, 5))
        self.feature_toggles[toggle_key or feature_key] = toggle

        if create_output is not None:
            output_widget = create_output(row)
        if output_widget is not None:
            output_widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
        return row, output_widget

    def _make_dual_feature_row(self, parent, left, right):
        """Place two label + toggle feature controls on one row."""
        row = tk.Frame(parent, bg=BG_CARD)
        row.pack(fill=tk.X, pady=ROW_PADY)

        def _add_half(feature_name, feature_key, enable_cmd, disable_cmd):
            half = tk.Frame(row, bg=BG_CARD)
            half.pack(side=tk.LEFT, fill=tk.X, expand=True)

            self._add_indicator_slot(half, feature_key)

            tk.Label(
                half, text=feature_name, font=self.small_font, bg=BG_CARD, fg=TEXT,
                width=LABEL_COL_CHARS, anchor='w',
            ).pack(side=tk.LEFT, padx=(0, 4))

            def _on_toggle(enabled):
                if enabled:
                    enable_cmd()
                else:
                    disable_cmd()

            toggle = ToggleSwitch(half, _on_toggle, bg=BG_CARD)
            toggle.pack(side=tk.LEFT, padx=(0, 5))
            self.feature_toggles[feature_key] = toggle

        _add_half(*left)
        _add_half(*right)
        return row

    def _make_grouped_rows(self, parent, group_key, build_rows, *, rows_top_pad=0):
        container = tk.Frame(parent, bg=BG_CARD)
        container.pack(fill=tk.X, pady=ROW_PADY)

        indicator_col = tk.Frame(container, bg=BG_CARD, width=INDICATOR_COL_WIDTH, height=1)
        indicator_col.pack(side=tk.LEFT, fill=tk.Y, padx=(0, INDICATOR_COL_PAD))
        indicator_col.pack_propagate(False)

        indicator = tk.Label(indicator_col, text="●", font=(self.default_font[0], 12), bg=BG_CARD, fg=ERROR)
        indicator.place(relx=0.5, rely=0.5, anchor='center')
        self._register_indicator(group_key, indicator)

        rows_col = tk.Frame(container, bg=BG_CARD)
        rows_col.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=(rows_top_pad, 0))

        rail = tk.Frame(rows_col, bg=RAIL, width=2)
        rail.place(relx=0, rely=0.1, relheight=0.8, anchor='nw')

        build_rows(rows_col)
        return container

    def _make_pressure_row(self, parent, feature_name, feature_key, enable_cmd, disable_cmd):
        row = tk.Frame(parent, bg=BG_CARD)
        row.pack(fill=tk.X, pady=ROW_PADY)

        self._add_indicator_slot(row, feature_key)

        tk.Label(row, text=feature_name, font=self.small_font, bg=BG_CARD, fg=TEXT,
                 width=LABEL_COL_CHARS, anchor='w').pack(side=tk.LEFT, padx=(0, 4))

        def _on_toggle(enabled):
            if enabled:
                enable_cmd()
            else:
                disable_cmd()

        toggle = ToggleSwitch(row, _on_toggle, bg=BG_CARD)
        toggle.pack(side=tk.LEFT, padx=(0, 5))
        self.feature_toggles[feature_key] = toggle

        out = tk.Frame(row, bg=BG_CARD)
        out.pack(side=tk.LEFT, fill=tk.X, expand=True)
        val_lbl = tk.Label(out, text="0.00", font=self.small_font, bg=OUTPUT_BG, fg=OUTPUT_FG, width=5, anchor='e')
        val_lbl.pack(side=tk.LEFT, padx=(0, 4))
        bar = ttk.Progressbar(out, orient=tk.HORIZONTAL, mode='determinate', maximum=1.0, value=0)
        bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        return val_lbl, bar, toggle

    # ------------------------------------------------------------------ Status indicators

    def refresh_status_indicators(self, device):
        """Update firmware status dots for SDK debug features."""
        self._refresh_status_indicators(device)

    def _update_indicator(self, indicator_or_list, color):
        if not self.root.winfo_exists():
            return
        if isinstance(indicator_or_list, list):
            for indicator in indicator_or_list:
                if indicator:
                    indicator.config(bg=BG_CARD, fg=color)
        elif indicator_or_list:
            indicator_or_list.config(bg=BG_CARD, fg=color)

    def _set_pressure_buttons_state(self, prefix, state):
        toggle = self.pressure_buttons.get(prefix)
        if toggle is not None:
            toggle.set_enabled(state == 'normal')
            if state != 'normal':
                toggle.set(False)

    def _sync_pressure_toggle_availability(self, direct_active, pinch_active):
        """Grey out mutually exclusive pressure toggles without changing on/off state."""
        pinch_toggle = self.pressure_buttons.get('pinch')
        direct_toggle = self.pressure_buttons.get('direct')
        if pinch_toggle is not None:
            pinch_toggle.set_enabled(not direct_active)
        if direct_toggle is not None:
            direct_toggle.set_enabled(not pinch_active)

    def _indicator_color(self, enabled):
        return SUCCESS if enabled else ERROR

    def _set_indicator_state(self, feature_key, enabled):
        if feature_key not in self.status_indicators:
            return
        self._update_indicator(self.status_indicators[feature_key], self._indicator_color(enabled))

    def _firmware_states(self, fs):
        navigation_enabled = fs.is_navigation_enabled
        gesture_enabled = fs.is_gesture_enabled
        return {
            'snc': fs.is_snc_enabled,
            'imu_acc': fs.is_acc_enabled,
            'imu_gyro': fs.is_gyro_enabled,
            'pinch_pressure': fs.is_pinch_pressure_enabled,
            'direct_pressure': fs.is_pressure_enabled,
            'navigation': navigation_enabled,
            'navigation_axis': navigation_enabled,
            'navigation_direction': navigation_enabled,
            'gesture': gesture_enabled,
            'button_changed': gesture_enabled,
            'embedded_airtouch': fs.is_air_touch_enabled,
            'nav_to_app': fs.is_sends_navigation_to_app_enabled,
            'gesture_to_hid': fs.is_sends_gesture_to_hid_enabled,
            'nav_to_hid': fs.is_sends_navigation_to_hid_enabled,
        }

    def _sync_ui_from_firmware(self, device):
        """Sync indicator dots from device firmware status (toggles stay manual)."""
        if device is None or not hasattr(device, 'firmware_status'):
            for key in self.feature_active:
                self.feature_active[key] = False
            for feature_key in self.status_indicators:
                self._set_indicator_state(feature_key, False)
            self._sync_pressure_toggle_availability(False, False)
            return

        states = self._firmware_states(device.firmware_status)

        for key, enabled in states.items():
            if key in self.feature_active:
                self.feature_active[key] = enabled

        for feature_key in self.status_indicators:
            if feature_key in states:
                self._set_indicator_state(feature_key, states[feature_key])

        direct_active = states['direct_pressure']
        pinch_active = states['pinch_pressure']
        self._sync_pressure_toggle_availability(direct_active, pinch_active)

    def _refresh_status_indicators(self, device):
        self._sync_ui_from_firmware(device)

    # ------------------------------------------------------------------ Navigation helpers

    def _nav_value_to_pixel(self, value: float) -> float:
        clamped = max(-NAV_AXIS_RANGE, min(NAV_AXIS_RANGE, value))
        mid = NAV_PAD_SIZE / 2.0
        usable = mid - NAV_DOT_RADIUS - 1
        return mid + (clamped / NAV_AXIS_RANGE) * usable

    def _set_nav_axis_dot(self, x: float, y: float):
        if self.nav_axis_canvas is None or self.nav_axis_dot is None:
            return
        px = self._nav_value_to_pixel(x)
        py = self._nav_value_to_pixel(-y)
        r = NAV_DOT_RADIUS
        self.nav_axis_canvas.coords(self.nav_axis_dot, px - r, py - r, px + r, py + r)

    def _set_nav_axis_text(self, x: float, y: float):
        if self.navigation_x_label is not None:
            self.navigation_x_label.config(text=f"X = {int(round(x))}")
        if self.navigation_y_label is not None:
            self.navigation_y_label.config(text=f"Y = {int(round(y))}")

    def _reset_nav_axis_view(self):
        if self.root and self.root.winfo_exists():
            self._set_nav_axis_dot(0, 0)
            self._set_nav_axis_text(0, 0)

    # ------------------------------------------------------------------ BLE data callbacks

    def on_snc_ready(self, timestamp, data_list, frequency, frequency_std, rms_list):
        print(f"SNC frequency: {frequency}")

        def _update_rms_bars():
            if not self.root.winfo_exists() or not self.feature_active['snc']:
                return
            values = (list(rms_list) + [0.0, 0.0, 0.0])[:3]
            for i, (bar, label, value) in enumerate(zip(self.rms_bars, self.rms_labels, values), start=1):
                value = max(0.0, min(1.0, float(value)))
                bar.config(value=value * 100.0)
                label.config(text=f"RMS {i}: {value:.2f}")
            if self.snc_freq_label is not None:
                self.snc_freq_label.config(text=f"{frequency:.2f} Hz")

        if self.root.winfo_exists():
            self.root.after(0, _update_rms_bars)

    def on_imu_acc_ready(self, timestamp, data_list, frequency, frequency_std, rms_list):
        print(f"IMU Acc frequency: {frequency}")

        def _update():
            if not self.root.winfo_exists() or not self.feature_active['imu_acc']:
                return
            if self.imu_acc_label is not None:
                self.imu_acc_label.config(text=f"{frequency:.2f} Hz")

        if self.root.winfo_exists():
            self.root.after(0, _update)

    def on_imu_gyro_ready(self, timestamp, data_list, frequency, frequency_std, rms_list):
        print(f"IMU Gyro frequency: {frequency}")

        def _update():
            if not self.root.winfo_exists() or not self.feature_active['imu_gyro']:
                return
            if self.imu_gyro_label is not None:
                self.imu_gyro_label.config(text=f"{frequency:.2f} Hz")

        if self.root.winfo_exists():
            self.root.after(0, _update)

    def on_navigation_axis_ready(self, delta_x, delta_y):
        print(f"Navigation delta: {delta_x}, {delta_y}")

        self.navigation_history['x'].append(float(delta_x))
        self.navigation_history['y'].append(float(delta_y))

        if len(self.navigation_history['x']) > NAVIGATION_SMOOTHING_WINDOW:
            self.navigation_history['x'].pop(0)
            self.navigation_history['y'].pop(0)

        smoothed_x = sum(self.navigation_history['x']) / len(self.navigation_history['x'])
        smoothed_y = sum(self.navigation_history['y']) / len(self.navigation_history['y'])

        def _update():
            if not self.root.winfo_exists() or not self.feature_active['navigation_axis']:
                return
            self._set_nav_axis_dot(smoothed_x, smoothed_y)
            self._set_nav_axis_text(smoothed_x, smoothed_y)

        if self.root.winfo_exists():
            self.root.after(0, _update)

    def on_navigation_direction_ready(self, direction: NavigationDirectionGesture):
        direction_str = direction.description if hasattr(direction, 'description') else str(direction)
        print(f"Navigation direction: {direction_str}")

        def _update():
            if not self.root.winfo_exists() or not self.feature_active['navigation_direction']:
                return
            if self.navigation_direction_label is not None:
                self.navigation_direction_label.config(text=direction_str)

        if self.root.winfo_exists():
            self.root.after(0, _update)

    def on_pinch_pressure_ready(self, pressure_data: float):
        def _update():
            if not self.root.winfo_exists() or not self.feature_active['pinch_pressure']:
                return
            value = max(0.0, min(1.0, float(pressure_data)))
            if self.pinch_pressure_bar is not None:
                self.pinch_pressure_bar.config(value=value)
            if self.pinch_pressure_label is not None:
                self.pinch_pressure_label.config(text=f"{value:.2f}")

        if self.root.winfo_exists():
            self.root.after(0, _update)

    def on_direct_pressure_ready(self, pressure_data: float):
        def _update():
            if not self.root.winfo_exists() or not self.feature_active['direct_pressure']:
                return
            value = max(0.0, min(1.0, float(pressure_data)))
            if self.direct_pressure_bar is not None:
                self.direct_pressure_bar.config(value=value)
            if self.direct_pressure_label is not None:
                self.direct_pressure_label.config(text=f"{value:.2f}")

        if self.root.winfo_exists():
            self.root.after(0, _update)

    def on_gesture_ready(self, gesture_type):
        print(f"Gesture received: {gesture_type}")

        def _update():
            if not self.root.winfo_exists() or not self.feature_active['gesture']:
                return
            if self.gesture_label is not None:
                self.gesture_label.config(text=str(gesture_type))

        if self.root.winfo_exists():
            self.root.after(0, _update)

    def on_airmouse_button_changed_ready(self, air_touch_button):
        print(f"Air Touch Button changed: {air_touch_button}")

        def _update():
            if not self.root.winfo_exists() or not self.feature_active['button_changed']:
                return
            if self.air_touch_button_label is not None:
                self.air_touch_button_label.config(text=str(air_touch_button))

        if self.root.winfo_exists():
            self.root.after(0, _update)

    # ------------------------------------------------------------------ Feature enable / disable

    def _run_on_loop(self, coro, *, refresh_indicators=True):
        self.ensure_event_loop_running()
        loop = self.get_loop()
        if loop is None:
            print("Event loop is not available.")
            return

        def _done(future):
            exc = future.exception()
            if exc is not None:
                print(f"Feature command failed: {exc}")
            if refresh_indicators and self.root.winfo_exists():
                device = self.get_feature_device()
                if device is not None:
                    def _sync(d=device):
                        if self.root.winfo_exists():
                            self._sync_ui_from_firmware(d)
                    self.root.after(0, _sync)
                    self.root.after(300, _sync)
                    self.root.after(800, _sync)

        future = asyncio.run_coroutine_threadsafe(coro, loop)
        future.add_done_callback(_done)

    def _mark_features(self, **states):
        """Optimistic local update for toggled features before firmware confirms."""
        self.feature_active.update(states)
        group_navigation = (
            self.feature_active.get('navigation_axis')
            or self.feature_active.get('navigation_direction')
        )
        group_gesture = (
            self.feature_active.get('gesture')
            or self.feature_active.get('button_changed')
        )
        for key, enabled in states.items():
            if key in self.status_indicators:
                self._set_indicator_state(key, enabled)
            elif key in ('navigation_axis', 'navigation_direction'):
                self._set_indicator_state('navigation', group_navigation)
            elif key in ('gesture', 'button_changed'):
                self._set_indicator_state('gesture', group_gesture)
            if key in self.feature_toggles:
                self.feature_toggles[key].set(enabled)

    def enable_snc_feature(self):
        device = self.get_feature_device()
        if device is None:
            print("No connected device available to enable feature.")
            return
        print(f"Enable feature called for device: {device.name}")
        self._mark_features(snc=True)
        self._run_on_loop(device.set_on_snc_ready(self.on_snc_ready))

    def _reset_snc_output(self):
        if self.root is None or not self.root.winfo_exists():
            return
        if self.snc_freq_label is not None:
            self.snc_freq_label.config(text="-- Hz")
        for i, (bar, lbl) in enumerate(zip(self.rms_bars, self.rms_labels), start=1):
            bar.config(value=0)
            lbl.config(text=f"RMS {i}: 0.00")

    def disable_snc_feature(self):
        self._mark_features(snc=False)
        device = self.get_feature_device()
        if device is None:
            self._reset_snc_output()
            return
        print(f"Disable feature called for device: {device.name}")
        self._run_on_loop(device.set_on_snc_ready(None))
        self._reset_snc_output()

    def enable_imu_acc_feature(self):
        device = self.get_feature_device()
        if device is None:
            print("No connected device available to enable IMU Acc feature.")
            return
        print(f"Enable IMU Acc feature called for device: {device.name}")
        self._mark_features(imu_acc=True)
        self._run_on_loop(device.set_on_imu_acc_ready(self.on_imu_acc_ready))

    def disable_imu_acc_feature(self):
        self._mark_features(imu_acc=False)
        device = self.get_feature_device()
        if device is None:
            if self.root and self.root.winfo_exists() and self.imu_acc_label is not None:
                self.imu_acc_label.config(text="-- Hz")
            return
        print(f"Disable IMU Acc feature called for device: {device.name}")
        self._run_on_loop(device.set_on_imu_acc_ready(None))
        if self.root and self.root.winfo_exists() and self.imu_acc_label is not None:
            self.imu_acc_label.config(text="-- Hz")

    def enable_imu_gyro_feature(self):
        device = self.get_feature_device()
        if device is None:
            print("No connected device available to enable IMU Gyro feature.")
            return
        print(f"Enable IMU Gyro feature called for device: {device.name}")
        self._mark_features(imu_gyro=True)
        self._run_on_loop(device.set_on_imu_gyro_ready(self.on_imu_gyro_ready))

    def disable_imu_gyro_feature(self):
        self._mark_features(imu_gyro=False)
        device = self.get_feature_device()
        if device is None:
            if self.root and self.root.winfo_exists() and self.imu_gyro_label is not None:
                self.imu_gyro_label.config(text="-- Hz")
            return
        print(f"Disable IMU Gyro feature called for device: {device.name}")
        self._run_on_loop(device.set_on_imu_gyro_ready(None))
        if self.root and self.root.winfo_exists() and self.imu_gyro_label is not None:
            self.imu_gyro_label.config(text="-- Hz")

    def enable_pinch_pressure_feature(self):
        device = self.get_feature_device()
        if device is None:
            print("No connected device available to enable Pinch Pressure feature.")
            return
        print(f"Enable Pinch Pressure feature called for device: {device.name}")
        self._mark_features(pinch_pressure=True, direct_pressure=False)
        self._run_on_loop(
            device.set_on_pressure_ready(self.on_pinch_pressure_ready, PressureType.pinch),
        )
        self._set_pressure_buttons_state('direct', 'disabled')

    def disable_pinch_pressure_feature(self):
        self._mark_features(pinch_pressure=False)
        device = self.get_feature_device()
        if device is None:
            self._set_pressure_buttons_state('direct', 'normal')
            return
        print(f"Disable Pinch Pressure feature called for device: {device.name}")
        self._run_on_loop(device.set_on_pressure_ready(None))
        self._set_pressure_buttons_state('direct', 'normal')
        if self.root and self.root.winfo_exists():
            if self.pinch_pressure_label is not None:
                self.pinch_pressure_label.config(text="0.00")
            if self.pinch_pressure_bar is not None:
                self.pinch_pressure_bar.config(value=0)

    def enable_direct_pressure_feature(self):
        device = self.get_feature_device()
        if device is None:
            print("No connected device available to enable Direct Pressure feature.")
            return
        print(f"Enable Direct Pressure feature called for device: {device.name}")
        self._mark_features(direct_pressure=True, pinch_pressure=False)
        self._run_on_loop(
            device.set_on_pressure_ready(self.on_direct_pressure_ready, PressureType.direct),
        )
        self._set_pressure_buttons_state('pinch', 'disabled')

    def disable_direct_pressure_feature(self):
        self._mark_features(direct_pressure=False)
        device = self.get_feature_device()
        if device is None:
            self._set_pressure_buttons_state('pinch', 'normal')
            return
        print(f"Disable Direct Pressure feature called for device: {device.name}")
        self._run_on_loop(device.set_on_pressure_ready(None))
        self._set_pressure_buttons_state('pinch', 'normal')
        if self.root and self.root.winfo_exists():
            if self.direct_pressure_label is not None:
                self.direct_pressure_label.config(text="0.00")
            if self.direct_pressure_bar is not None:
                self.direct_pressure_bar.config(value=0)

    def enable_navigation_axis_feature(self):
        device = self.get_feature_device()
        if device is None:
            print("No connected device available to enable Navigation Axis feature.")
            return
        print(f"Enable Navigation feature called for device: {device.name}")
        self._mark_features(navigation_axis=True)
        self._run_on_loop(device.set_on_navigation_axis_ready(self.on_navigation_axis_ready))

    def disable_navigation_axis_feature(self):
        self._mark_features(navigation_axis=False)
        device = self.get_feature_device()
        if device is None:
            self.navigation_history['x'].clear()
            self.navigation_history['y'].clear()
            self._reset_nav_axis_view()
            return
        print(f"Disable Navigation feature called for device: {device.name}")
        self._run_on_loop(device.set_on_navigation_axis_ready(None))
        self.navigation_history['x'].clear()
        self.navigation_history['y'].clear()
        self._reset_nav_axis_view()

    def enable_navigation_direction_feature(self):
        device = self.get_feature_device()
        if device is None:
            print("No connected device available to enable Navigation Direction feature.")
            return
        print(f"Enable Navigation Direction feature called for device: {device.name}")
        self._mark_features(navigation_direction=True)
        self._run_on_loop(device.set_on_navigation_direction_ready(self.on_navigation_direction_ready))

    def disable_navigation_direction_feature(self):
        self._mark_features(navigation_direction=False)
        device = self.get_feature_device()
        if device is None:
            if self.root and self.root.winfo_exists() and self.navigation_direction_label is not None:
                self.navigation_direction_label.config(text="--")
            return
        print(f"Disable Navigation Direction feature called for device: {device.name}")
        self._run_on_loop(device.set_on_navigation_direction_ready(None))
        if self.root and self.root.winfo_exists() and self.navigation_direction_label is not None:
            self.navigation_direction_label.config(text="--")

    def enable_gesture_feature(self):
        device = self.get_feature_device()
        if device is None:
            print("No connected device available to enable Gesture feature.")
            return
        print(f"Enable Gesture feature called for device: {device.name}")
        self._mark_features(gesture=True)
        self._run_on_loop(device.set_on_gesture_ready(self.on_gesture_ready))

    def disable_gesture_feature(self):
        self._mark_features(gesture=False)
        device = self.get_feature_device()
        if device is None:
            if self.root and self.root.winfo_exists() and self.gesture_label is not None:
                self.gesture_label.config(text="--")
            return
        print(f"Disable Gesture feature called for device: {device.name}")
        self._run_on_loop(device.set_on_gesture_ready(None))
        if self.root and self.root.winfo_exists() and self.gesture_label is not None:
            self.gesture_label.config(text="--")

    def enable_air_mouse_button_changed_feature(self):
        device = self.get_feature_device()
        if device is None:
            print("No connected device available to enable Air Touch Button Changed feature.")
            return
        print(f"Enable Air Touch Button Changed feature called for device: {device.name}")
        self._mark_features(button_changed=True)
        self._run_on_loop(device.set_on_button_changed(self.on_airmouse_button_changed_ready))

    def disable_air_mouse_button_changed_feature(self):
        self._mark_features(button_changed=False)
        device = self.get_feature_device()
        if device is None:
            if self.root and self.root.winfo_exists() and self.air_touch_button_label is not None:
                self.air_touch_button_label.config(text="--")
            return
        print(f"Disable Air Touch Button Changed feature called for device: {device.name}")
        self._run_on_loop(device.set_on_button_changed(None))
        if self.root and self.root.winfo_exists() and self.air_touch_button_label is not None:
            self.air_touch_button_label.config(text="--")

    def enable_embedded_airtouch_feature(self):
        device = self.get_feature_device()
        if device is None:
            print("No connected device available to enable Embedded AirTouch feature.")
            return
        print(f"Enable Embedded AirTouch feature called for device: {device.name}")
        self._mark_features(embedded_airtouch=True)
        self._run_on_loop(device.set_air_touch_active(True))

    def disable_embedded_airtouch_feature(self):
        self._mark_features(embedded_airtouch=False)
        device = self.get_feature_device()
        if device is None:
            return
        print(f"Disable Embedded AirTouch feature called for device: {device.name}")
        self._run_on_loop(device.set_air_touch_active(False))

    def enable_navigation_to_app(self):
        device = self.get_feature_device()
        if device is None:
            print("No connected device available to enable Navigation To App.")
            return
        print(f"Enable Navigation To App called for device: {device.name}")
        self._mark_features(nav_to_app=True)
        self._run_on_loop(device.set_firmware_target(FirmwareTarget.navigation_to_app, True))

    def disable_navigation_to_app(self):
        self._mark_features(nav_to_app=False)
        device = self.get_feature_device()
        if device is None:
            return
        print(f"Disable Navigation To App called for device: {device.name}")
        self._run_on_loop(device.set_firmware_target(FirmwareTarget.navigation_to_app, False))

    def enable_gesture_to_hid(self):
        device = self.get_feature_device()
        if device is None:
            print("No connected device available to enable Gesture To HID.")
            return
        print(f"Enable Gesture To HID called for device: {device.name}")
        self._mark_features(gesture_to_hid=True)
        self._run_on_loop(device.set_firmware_target(FirmwareTarget.gesture_to_hid, True))

    def disable_gesture_to_hid(self):
        self._mark_features(gesture_to_hid=False)
        device = self.get_feature_device()
        if device is None:
            return
        print(f"Disable Gesture To HID called for device: {device.name}")
        self._run_on_loop(device.set_firmware_target(FirmwareTarget.gesture_to_hid, False))

    def enable_navigation_to_hid(self):
        device = self.get_feature_device()
        if device is None:
            print("No connected device available to enable Navigation To HID.")
            return
        print(f"Enable Navigation To HID called for device: {device.name}")
        self._mark_features(nav_to_hid=True)
        self._run_on_loop(device.set_firmware_target(FirmwareTarget.navigation_to_hid, True))

    def disable_navigation_to_hid(self):
        self._mark_features(nav_to_hid=False)
        device = self.get_feature_device()
        if device is None:
            return
        print(f"Disable Navigation To HID called for device: {device.name}")
        self._run_on_loop(device.set_firmware_target(FirmwareTarget.navigation_to_hid, False))
