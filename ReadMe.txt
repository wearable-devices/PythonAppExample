# Mudra Python SDK Example

This example application demonstrates device discovery, connection management,
SDK debug controls, recording, and user sign-in using the published Mudra Python
SDK. The SDK source and native libraries are intentionally not included here.

## Requirements

- Python 3.10 or newer
- Bluetooth-enabled computer
- Mudra device

Install the SDK dependency:

```powershell
python -m pip install -r requirements.txt
```

## Run

```powershell
python main.py
```

Use **Find Devices** to list Mudra devices already connected to the Bluetooth
adapter, or use **Start Scan** and **Stop Scan** to discover nearby devices.

## SDK

- https://pypi.org/project/mudra-sdk/