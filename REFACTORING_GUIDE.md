# Refactoring BLEInterface to a Driver-Based Architecture

## 1. Goal

This guide outlines the process of refactoring the existing `RNS.Interfaces.BLEInterface` to decouple the high-level Reticulum protocol logic from the platform-specific Bluetooth implementation (`bleak`/`bluezero`).

The goal is to create a clean architectural boundary by introducing a `BLEDriverInterface`. The existing `BLEInterface` will be refactored to use this driver, and the Linux-specific `bleak` and `bluezero` code will be moved into a new concrete implementation of this driver, `BleakDriver`.

This will result in a more modular, maintainable, and testable system, and it will make it possible to share the high-level `BLEInterface` code between the pure Python implementation and the Android (Columba) implementation.

## 2. Prerequisites: The Driver Contract

First, create a new file, `RNS/Interfaces/bluetooth_driver.py`, and add the abstract interface definition we designed. This file defines the contract that all platform-specific drivers must follow.

```python
# RNS/Interfaces/bluetooth_driver.py

from abc import ABC, abstractmethod
from typing import List, Optional, Callable
from enum import Enum, auto
from dataclasses import dataclass

# --- Data Structures ---

@dataclass
class BLEDevice:
    """Represents a discovered BLE device."""
    address: str
    name: str
    rssi: int

class DriverState(Enum):
    """Represents the state of the BLE driver."""
    IDLE = auto()
    SCANNING = auto()
    ADVERTISING = auto()

# --- Driver Interface ---

class BLEDriverInterface(ABC):
    """
    Abstract interface for a platform-specific BLE driver.

    Driver implementations should maintain connection state tracking
    to prevent race conditions from concurrent connection attempts:

        self._connecting_peers: set = set()  # addresses with pending connections
        self._connecting_lock: threading.Lock = threading.Lock()

    The connect() method should check this set before initiating a connection,
    and always clean up the set in a finally block to ensure proper state
    management even on connection failures. This prevents "Operation already
    in progress" errors when discovery callbacks trigger multiple simultaneous
    connection attempts to the same peer.
    """

    # --- Callbacks ---
    on_device_discovered: Optional[Callable[[BLEDevice], None]] = None
    on_device_connected: Optional[Callable[[str, int], None]] = None  # address, mtu
    on_device_disconnected: Optional[Callable[[str], None]] = None # address
    on_data_received: Optional[Callable[[str, bytes], None]] = None # address, data

    # --- Lifecycle & Configuration ---

    @abstractmethod
    def start(self, service_uuid: str, rx_char_uuid: str, tx_char_uuid: str, identity_char_uuid: str):
        """
        Initializes the driver and its underlying BLE stack.
        """
        pass

    @abstractmethod
    def stop(self):
        """
        Stops all BLE activity and releases resources.
        """
        pass

    @abstractmethod
    def set_identity(self, identity_bytes: bytes):
        """
        Sets the value of the read-only Identity characteristic for the local GATT server.
        """
        pass

    # --- State & Properties ---

    @property
    @abstractmethod
    def state(self) -> DriverState:
        pass

    @property
    @abstractmethod
    def connected_peers(self) -> List[str]:
        pass

    # --- Core Actions ---

    @abstractmethod
    def start_scanning(self):
        pass

    @abstractmethod
    def stop_scanning(self):
        pass

    @abstractmethod
    def start_advertising(self, device_name: str):
        pass

    @abstractmethod
    def stop_advertising(self):
        pass

    @abstractmethod
    def connect(self, address: str):
        pass

    @abstractmethod
    def disconnect(self, address: str):
        pass

    @abstractmethod
    def send(self, address: str, data: bytes):
        pass
```

## 3. Step-by-Step Refactoring Guide

### Step 1: Create the `BleakDriver` Implementation

Create a new file, `RNS/Interfaces/bleak_driver.py`. This file will contain the new `BleakDriver` class that implements the `BLEDriverInterface` and encapsulates all `bleak` and `bluezero` code.

```python
# RNS/Interfaces/bleak_driver.py

from .bluetooth_driver import BLEDriverInterface, BLEDevice, DriverState
# Add other necessary imports like bleak, bluezero, asyncio, etc.

class BleakDriver(BLEDriverInterface):
    def __init__(self):
        # Initialize properties to hold clients, state, etc.
        self._state = DriverState.IDLE
        self._clients = {} # address -> BleakClient
        # ...and so on

    # Implement all the abstract methods from the interface here
    def start(self, service_uuid, rx_char_uuid, tx_char_uuid, identity_char_uuid):
        # Code to initialize bleak and bluezero will go here
        pass

    def start_scanning(self):
        # Code that uses bleak.BleakScanner will go here
        pass

    def send(self, address, data):
        # Code that uses bleak_client.write_gatt_char will go here
        pass

    # ... etc.
```

### Step 2: Move Platform-Specific Code to `BleakDriver`

Go through the existing `BLEInterface.py` method by method and move any code that directly calls `bleak` or `bluezero` into the corresponding method in your new `BleakDriver` class.

**Example: Moving the `send` logic**

**Before (`BLEInterface.py`):**
```python
# (Inside BLEPeerInterface class)
async def _send_fragment(self, fragment):
    # ...
    await self.client.write_gatt_char(self.parent.WRITE_CH_UUID, fragment)
    # ...
```

**After (`bleak_driver.py`):**
```python
# (Inside BleakDriver class)
async def send(self, address: str, data: bytes):
    if address in self._clients:
        client = self._clients[address]
        try:
            # The driver now handles the actual write operation
            await client.write_gatt_char(self.rx_char_uuid, data)
        except Exception as e:
            # Handle exceptions and possibly trigger disconnect
            pass
```

### Step 3: Refactor `BLEInterface` to Use the Driver

Modify `BLEInterface.py` to remove all direct dependencies on `bleak` and `bluezero`. Instead, it will be initialized with a driver instance and will use it to perform all BLE operations.

**Example: Refactoring `__init__` and `_send_fragment`**

**Before (`BLEInterface.py`):**
```python
import bleak
from bluezero import peripheral

class BLEInterface(Interface):
    def __init__(self, owner, name, ...):
        # ... bleak and bluezero objects initialized here
        pass

    # ... methods with direct bleak/bluezero calls
```

**After (`BLEInterface.py`):**
```python
# No more bleak or bluezero imports!
from .bluetooth_driver import BLEDriverInterface, BLEDevice

class BLEInterface(Interface):
    def __init__(self, owner, name, ..., driver: BLEDriverInterface):
        super().__init__()
        self.driver = driver # Dependency Injection

        # Assign callbacks so the driver can report events back to us
        self.driver.on_device_discovered = self._device_discovered_callback
        self.driver.on_data_received = self._data_received_callback
        # ... etc.

    # This method no longer needs to be async if the driver's send is blocking
    # or if we want to fire-and-forget
    def _send_fragment(self, fragment, peer_address):
        # High-level logic just tells the driver to send
        self.driver.send(peer_address, fragment)

    # --- Callback Implementations ---
    def _device_discovered_callback(self, device: BLEDevice):
        # Logic to handle a discovered device
        pass

    def _data_received_callback(self, address: str, data: bytes):
        # This is where you feed the raw data (a fragment) into the reassembler
        pass
```

## 4. Thorough Testing Plan

A multi-layered testing strategy is crucial for a refactor of this scale.

### Tier 1: Unit Testing (Mock Driver)

The biggest advantage of this new architecture is testability. You can now test your entire `BLEInterface` and fragmentation logic without any Bluetooth hardware.

1.  **Create a `MockBLEDriver`:**
    *   Create a `tests/mock_ble_driver.py` file.
    *   The `MockBLEDriver` class will implement `BLEDriverInterface`.
    *   Its methods will not use Bluetooth. Instead, they will simulate it. For example, its `send()` method could store the data in a list and immediately trigger the `on_data_received` callback on a paired "virtual" peer's mock driver.
2.  **Write `BLEInterface` Unit Tests:**
    *   Write `pytest` tests that initialize `BLEInterface` with the `MockBLEDriver`.
    *   **Test Case 1: Fragmentation.** Call `BLEInterface.process_outgoing()` with a large packet. Assert that the `mock_driver.send()` method was called multiple times with correctly fragmented data (correct headers, sequence numbers, etc.).
    *   **Test Case 2: Reassembly.** Have the `mock_driver` call the `on_data_received` callback with a sequence of fragments. Assert that `BLEInterface` correctly reassembles them and passes the complete packet to `RNS.Transport.inbound`.
    *   **Test Case 3: Peer Lifecycle.** Simulate device discovery, connection, and disconnection events from the mock driver and assert that `BLEInterface` creates and destroys its internal peer representations correctly.

### Tier 2: Integration Testing (Driver Level)

This tier tests your actual `BleakDriver` implementation against real hardware.

1.  **Create Test Scripts:** Write simple Python scripts that use *only* the `BleakDriver`.
2.  **Setup:** You will need two machines with Bluetooth, or one machine and your Columba app on an Android device.
3.  **Test Cases:**
    *   **Scanning Test:** Run a script that starts the driver and prints discovered devices. Verify that it finds your other test device.
    *   **Connection Test:** Write a script to connect to the test device. Verify that the `on_device_connected` callback fires and that `driver.connected_peers` is updated.
    *   **Data I/O Test:** After connecting, use `driver.send()` to send a simple "hello world" byte string. On the other device, verify that the bytes are received correctly. Test this in both directions.
    *   **Connection Race Condition Test:** Simulate rapid discovery callbacks for the same peer (e.g., by triggering `on_device_discovered` multiple times in quick succession). Verify that:
        - Only one connection attempt is made (check `driver._connecting_peers` contains only one entry)
        - No "Operation already in progress" errors appear in logs
        - The `_connecting_peers` set is properly cleaned up after connection (success or failure)
        - Subsequent connection attempts are properly rate-limited (5-second minimum interval)

### Tier 3: End-to-End Testing (Full Stack)

This is the final validation, testing the entire refactored application.

1.  **Run Full Application:** Start the full Reticulum application on two Linux machines using the refactored code.
2.  **Test Cases:**
    *   **Announce Exchange:** Verify that the two nodes discover each other and exchange announces. Check the logs for successful path discovery.
    *   **LXMF Message Transfer:** Use a tool like `lxmf-send` or a simple script to send a message from one node to the other. Verify it is received.
    *   **Cross-Compatibility Test:** Test interoperability between a refactored pure Python node and your Columba Android application.

By following this guide and testing plan, you can confidently execute the refactor, resulting in a more robust, maintainable, and future-proof architecture for your project.
