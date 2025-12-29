import asyncio
import tkinter as tk
from tkinter import ttk
from threading import Thread
from mudra_sdk import Mudra, MudraDevice
from mudra_sdk.models.callbacks import MudraDelegate

discovered_devices = []
connected_device = None
loop = None
loop_thread = None

class MyMudraDelegate(MudraDelegate):
    def on_device_discovered(self, device: MudraDevice):
        print(f"✓ Discovered: {device.name} ({device.address})")
        discovered_devices.append(device)
        if root is not None:
            root.after(0, refresh_listbox)

    def on_mudra_device_connected(self, device: MudraDevice):
        global connected_device
        connected_device = device
        print(f"✓ Connected to: {device.name}")

    def on_mudra_device_disconnected(self, device: MudraDevice):
        print(f"✓ Disconnected from: {device.name}")

    def on_mudra_device_connecting(self, device: MudraDevice):
        print(f"→ Connecting to: {device.name}...")

    def on_mudra_device_disconnecting(self, device: MudraDevice):
        print(f"→ Disconnecting from: {device.name}...")

    def on_mudra_device_connection_failed(self, device: MudraDevice, error: str):
        print(f"✗ Connection failed: {device.name}, Error: {error}")

    def on_bluetooth_state_changed(self, state: bool):
        print(f"Bluetooth: {'On' if state else 'Off'}")

def on_pressure_ready(pressure_data: int):
    def _update_pressure_bar():
        if root.winfo_exists():
            value = max(0.0, min(100.0, float(pressure_data)))
            pressure_bar.config(value=value)
            pressure_label.config(text=f"Pressure: {value:.2f}")
    
    if root.winfo_exists():
        root.after(0, _update_pressure_bar)

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
        import time
        time.sleep(0.1)

def refresh_listbox():
    listbox.delete(0, tk.END)
    for dev in discovered_devices:
        listbox.insert(tk.END, dev.name)

def get_selected_device():
    """Return the currently selected MudraDevice, or None."""
    selection = listbox.curselection()
    if not selection:
        return None
    index = selection[0]
    if 0 <= index < len(discovered_devices):
        return discovered_devices[index]
    return None

def start_scan():
    if mudra is None:
        return
    ensure_event_loop_running()
    if loop is not None:
        asyncio.run_coroutine_threadsafe(mudra.scan(), loop)

def stop_scan():
    if mudra is None or loop is None:
        return
    asyncio.run_coroutine_threadsafe(mudra.stop_scan(), loop)

def connect_selected_device():
    device = get_selected_device()
    if device is None:
        print("No device selected to connect.")
        return
    ensure_event_loop_running()
    if loop is not None:
        asyncio.run_coroutine_threadsafe(device.connect(), loop)

def disconnect_selected_device():
    device = get_selected_device()
    if device is None:
        print("No device selected to disconnect.")
        return
    ensure_event_loop_running()
    if loop is not None:
        asyncio.run_coroutine_threadsafe(device.disconnec1t(), loop)

def enable_pressure_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to enable Pressure feature.")
        return
    ensure_event_loop_running()
    if loop is not None:
        asyncio.run_coroutine_threadsafe(device.set_on_pressure_ready(on_pressure_ready), loop)

def disable_pressure_feature():
    device = get_selected_device()
    if device is None:
        print("No device selected to disable Pressure feature.")
        return
    ensure_event_loop_running()
    if loop is not None:
        asyncio.run_coroutine_threadsafe(device.set_on_pressure_ready(None), loop)

# Global UI references
mudra = None
root = None
listbox = None
pressure_bar = None
pressure_label = None

def main():
    """Main function to set up and run the application."""
    global mudra, root, listbox, pressure_bar, pressure_label
    
    # Initialize Mudra
    mudra = Mudra()
    mudra.set_delegate(MyMudraDelegate())
    
    # Set up Tkinter window
    root = tk.Tk()
    root.title("Mudra BLE Devices")
    
    frame = tk.Frame(root)
    frame.pack(padx=10, pady=10)
    
    listbox = tk.Listbox(frame, width=40, height=10)
    listbox.pack(pady=(0, 10))
    
    # Pressure UI
    pressure_frame = tk.Frame(frame)
    pressure_frame.pack(fill=tk.X, pady=(0, 10))
    
    pressure_label = tk.Label(pressure_frame, text="Pressure: 0")
    pressure_label.pack(side=tk.LEFT)
    
    pressure_bar = ttk.Progressbar(
        pressure_frame,
        orient=tk.HORIZONTAL,
        length=200,
        mode='determinate',
        maximum=100
    )
    pressure_bar.pack(side=tk.LEFT, padx=(10, 0), fill=tk.X, expand=True)
    
    # Buttons
    button_frame = tk.Frame(frame)
    button_frame.pack()
    
    start_btn = tk.Button(button_frame, text="Start Scanning", command=start_scan)
    start_btn.pack(side=tk.LEFT, padx=5)
    
    stop_btn = tk.Button(button_frame, text="Stop Scanning", command=stop_scan)
    stop_btn.pack(side=tk.LEFT, padx=5)
    
    connect_btn = tk.Button(button_frame, text="Connect", command=connect_selected_device)
    connect_btn.pack(side=tk.LEFT, padx=5)
    
    disconnect_btn = tk.Button(button_frame, text="Disconnect", command=disconnect_selected_device)
    disconnect_btn.pack(side=tk.LEFT, padx=5)
    
    enable_pressure_btn = tk.Button(button_frame, text="Enable Pressure", command=enable_pressure_feature)
    enable_pressure_btn.pack(side=tk.LEFT, padx=5)
    
    disable_pressure_btn = tk.Button(button_frame, text="Disable Pressure", command=disable_pressure_feature)
    disable_pressure_btn.pack(side=tk.LEFT, padx=5)
    
    root.mainloop()

if __name__ == "__main__":
    main()
