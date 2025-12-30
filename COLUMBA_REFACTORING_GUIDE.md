
# Refactoring Columba's BLE Layer to a Driver-Based Architecture

## 1. Goal

This guide outlines the process of refactoring the existing BLE implementation in the Columba Android project to align with the new driver-based architecture of the `ble-reticulum` project.

The goal is to:
- Reuse the battle-tested `BLEInterface.py` from `ble-reticulum` as the main Reticulum logic for BLE in Columba.
- Create a new Android-specific BLE driver in Python (`AndroidBLEDriver.py`) that implements the `BLEDriverInterface`.
- Bridge this new Python driver to a dedicated Kotlin class (`KotlinBLEBridge.kt`) that handles all native Android BLE operations.
- Isolate the Kotlin BLE logic from the rest of the Columba UI application, with the `KotlinBLEBridge` acting as the sole entry point for the Python layer.

This will result in a more modular, maintainable, and testable system, and will allow Columba to easily stay up-to-date with the latest improvements in the `ble-reticulum` project.

## 2. Current State Analysis

Based on the file structure of the Columba project, the current BLE implementation is a monolithic Kotlin implementation with a Python bridge.

- **Kotlin:** The core BLE logic is in the `com.lxmf.messenger.reticulum.ble` package, with classes like `BleConnectionManager`, `BleGattClient`, `BleGattServer`, `BleScanner`, and `BleAdvertiser`.
- **Python:** The `rn_ble_interface.py` script acts as the Chaquopy bridge, importing and using the Kotlin classes to create a Reticulum interface.

This architecture is tightly coupled, making it difficult to update and maintain. The new driver-based architecture will address these issues.

## 3. Proposed Architecture

The new architecture will consist of three main components:

1.  **`BLEInterface.py`:** The high-level, platform-agnostic Reticulum interface logic from the `ble-reticulum` project.
2.  **`AndroidBLEDriver.py`:** A new Python class that implements the `BLEDriverInterface` and acts as a bridge to the Kotlin layer.
3.  **`KotlinBLEBridge.kt`:** A new, isolated Kotlin class that exposes a clean API for the `AndroidBLEDriver.py` to interact with the native Android BLE stack.

This architecture will allow us to reuse the `BLEInterface.py` and only implement the platform-specific BLE operations in the `AndroidBLEDriver.py` and `KotlinBLEBridge.kt`.

## 4. Step-by-Step Refactoring Guide

### Step 1: Create the `KotlinBLEBridge.kt`

Create a new Kotlin class, `KotlinBLEBridge.kt`, in the `com.lxmf.messenger.reticulum.ble.service` package. This class will be the single entry point for all BLE operations from the Python layer. It should be a singleton and should not have any dependencies on the Columba UI.

The `KotlinBLEBridge.kt` class should expose methods that correspond to the `BLEDriverInterface` in Python. For example:

```kotlin
class KotlinBLEBridge(private val context: Context) {

    fun start(serviceUuid: String, rxCharUuid: String, txCharUuid: String, identityCharUuid: String) {
        // Initialize the BLE stack
    }

    fun stop() {
        // Stop all BLE activity
    }

    fun setIdentity(identityBytes: ByteArray) {
        // Set the identity for the GATT server
    }

    fun startScanning() {
        // Start scanning for devices
    }

    fun stopScanning() {
        // Stop scanning
    }

    fun startAdvertising(deviceName: String) {
        // Start advertising
    }

    fun stopAdvertising() {
        // Stop advertising
    }

    fun connect(address: String) {
        // Connect to a device
    }

    fun disconnect(address: String) {
        // Disconnect from a device
    }

    fun send(address: String, data: ByteArray) {
        // Send data to a device
    }

    // ... other methods as needed
}
```

This class will also be responsible for invoking the callbacks on the Python driver. You can use a listener interface to achieve this.

### Step 2: Create the `AndroidBLEDriver.py`

Create a new Python file, `AndroidBLEDriver.py`, in the `columba/app/src/main/python` directory. This class will implement the `BLEDriverInterface` and will use Chaquopy to call the methods of the `KotlinBLEBridge`.

```python
from com.chaquo.python import Python
from RNS.Interfaces.bluetooth_driver import BLEDriverInterface, BLEDevice, DriverState

class AndroidBLEDriver(BLEDriverInterface):
    def __init__(self):
        self.kotlin_ble_bridge = Python.getPlatform().getApplication().getKotlinBLEBridge()
        # Set up callbacks from Kotlin to Python
        # ...

    def start(self, service_uuid, rx_char_uuid, tx_char_uuid, identity_char_uuid):
        self.kotlin_ble_bridge.start(service_uuid, rx_char_uuid, tx_char_uuid, identity_char_uuid)

    def stop(self):
        self.kotlin_ble_bridge.stop()

    # ... implement all other methods of the BLEDriverInterface

```

### Step 3: Refactor `rn_ble_interface.py`

Modify the existing `rn_ble_interface.py` to use the new `BLEInterface` and `AndroidBLEDriver`.

```python
# rn_ble_interface.py

from RNS.Interfaces.BLEInterface import BLEInterface
from AndroidBLEDriver import AndroidBLEDriver

# ... other imports

class RNBLEInterface(BLEInterface):
    def __init__(self, owner, config):
        driver = AndroidBLEDriver()
        super().__init__(owner, config, driver=driver)

# ... rest of the file
```

### Step 4: Replace the old BLE implementation

Once the new driver-based architecture is in place, you can start removing the old BLE implementation in the Columba project. This includes classes like `BleConnectionManager`, `BleGattClient`, `BleGattServer`, etc. The new `KotlinBLEBridge` should encapsulate all the necessary BLE logic.

## 5. Testing

Thorough testing is crucial for this refactoring.

- **Unit Tests:** Write unit tests for the `KotlinBLEBridge` to ensure that it correctly interacts with the Android BLE stack.
- **Integration Tests:** Write integration tests that verify the communication between the `AndroidBLEDriver.py` and the `KotlinBLEBridge.kt`.
- **End-to-End Tests:** Run the full Columba application and test the BLE functionality to ensure that everything works as expected.

By following this guide, you can refactor the Columba BLE layer to a more modern, modular, and maintainable architecture, while at the same time reusing the battle-tested `BLEInterface.py` from the `ble-reticulum` project.
