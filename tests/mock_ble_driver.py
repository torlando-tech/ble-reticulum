"""
Mock BLE Driver for Unit Testing

This module provides a mock implementation of BLEDriverInterface that simulates
BLE behavior without requiring actual Bluetooth hardware. It's designed for
unit testing BLEInterface logic including:

- Fragmentation and reassembly
- Peer lifecycle management
- Connection blacklist logic
- MAC-based connection direction
- Error handling

Usage:
    # Create two mock drivers to simulate a pair of peers
    driver1 = MockBLEDriver()
    driver2 = MockBLEDriver()

    # Link them to enable bidirectional communication
    MockBLEDriver.link_drivers(driver1, driver2)

    # Simulate discovery
    driver1.simulate_device_discovered("AA:BB:CC:DD:EE:FF", "RNS-Test", -60)

    # Simulate connection
    driver1.connect("AA:BB:CC:DD:EE:FF")

    # Simulate data transfer
    driver1.send("AA:BB:CC:DD:EE:FF", b"test data")
    # -> Triggers driver2.on_data_received("11:22:33:44:55:66", b"test data")
"""

import sys
import os
# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from RNS.Interfaces.bluetooth_driver import BLEDriverInterface, BLEDevice, DriverState
from typing import List, Optional, Callable, Dict
import time


class MockBLEDriver(BLEDriverInterface):
    """
    Mock BLE driver that simulates Bluetooth behavior for testing.
    """

    def __init__(self, local_address: str = "11:22:33:44:55:66"):
        """
        Initialize the mock driver.

        Args:
            local_address: Simulated MAC address for this driver
        """
        self.local_address = local_address
        self._state = DriverState.IDLE
        self._connected_peers: Dict[str, dict] = {}  # address -> {role, mtu, identity}
        self._identity: Optional[bytes] = None
        self._service_discovery_delay: float = 0.0  # No delay in mock
        self._power_mode: str = "balanced"

        # UUIDs (set via start())
        self._service_uuid: Optional[str] = None
        self._rx_char_uuid: Optional[str] = None
        self._tx_char_uuid: Optional[str] = None
        self._identity_char_uuid: Optional[str] = None

        # Callbacks (assigned by consumer)
        self.on_device_discovered: Optional[Callable[[BLEDevice], None]] = None
        self.on_device_connected: Optional[Callable[[str], None]] = None
        self.on_device_disconnected: Optional[Callable[[str], None]] = None
        self.on_data_received: Optional[Callable[[str, bytes], None]] = None
        self.on_mtu_negotiated: Optional[Callable[[str, int], None]] = None
        self.on_error: Optional[Callable[[str, str, Optional[Exception]], None]] = None

        # Linked driver for bidirectional communication testing
        self._linked_driver: Optional['MockBLEDriver'] = None

        # Simulated characteristics storage
        self._characteristics: Dict[str, bytes] = {}  # char_uuid -> value

        # Track sent data for assertions
        self.sent_data: List[tuple] = []  # [(address, data), ...]

    # --- Lifecycle & Configuration ---

    def start(self, service_uuid: str, rx_char_uuid: str, tx_char_uuid: str, identity_char_uuid: str):
        """Initialize the mock driver with UUIDs."""
        self._service_uuid = service_uuid
        self._rx_char_uuid = rx_char_uuid
        self._tx_char_uuid = tx_char_uuid
        self._identity_char_uuid = identity_char_uuid
        self._state = DriverState.IDLE

    def stop(self):
        """Stop all activity and disconnect all peers."""
        for address in list(self._connected_peers.keys()):
            self.disconnect(address)
        self._state = DriverState.IDLE

    def set_identity(self, identity_bytes: bytes):
        """Set the local identity value."""
        self._identity = identity_bytes
        self._characteristics[self._identity_char_uuid] = identity_bytes

    # --- State & Properties ---

    @property
    def state(self) -> DriverState:
        """Return current state."""
        return self._state

    @property
    def connected_peers(self) -> List[str]:
        """Return list of connected peer addresses."""
        return list(self._connected_peers.keys())

    # --- Core Actions ---

    def start_scanning(self):
        """Start scanning (simulated)."""
        self._state = DriverState.SCANNING

    def stop_scanning(self):
        """Stop scanning."""
        if self._state == DriverState.SCANNING:
            self._state = DriverState.IDLE

    def start_advertising(self, device_name: str, identity: bytes):
        """Start advertising (simulated)."""
        self._identity = identity
        self._characteristics[self._identity_char_uuid] = identity
        self._state = DriverState.ADVERTISING

    def stop_advertising(self):
        """Stop advertising."""
        if self._state == DriverState.ADVERTISING:
            self._state = DriverState.IDLE

    def connect(self, address: str):
        """
        Simulate connecting to a peer (central role).

        If a linked driver is set and its address matches, establishes
        a bidirectional connection.
        """
        if address in self._connected_peers:
            return  # Already connected

        # Simulate connection with default MTU
        self._connected_peers[address] = {
            "role": "central",
            "mtu": 185,  # Default MTU
            "identity": None
        }

        # Trigger callback
        if self.on_device_connected:
            self.on_device_connected(address)

        # Trigger MTU negotiation callback
        if self.on_mtu_negotiated:
            self.on_mtu_negotiated(address, 185)

        # If linked driver exists and address matches, establish reverse connection
        if self._linked_driver and self._linked_driver.local_address == address:
            self._linked_driver._accept_connection(self.local_address)

    def _accept_connection(self, address: str):
        """
        Internal: Accept incoming connection (peripheral role).
        Called by linked driver when it connects to us.
        """
        if address in self._connected_peers:
            return

        self._connected_peers[address] = {
            "role": "peripheral",
            "mtu": 185,
            "identity": None
        }

        if self.on_device_connected:
            self.on_device_connected(address)

        if self.on_mtu_negotiated:
            self.on_mtu_negotiated(address, 185)

    def disconnect(self, address: str):
        """Disconnect from a peer."""
        if address not in self._connected_peers:
            return

        # Remove peer
        role = self._connected_peers[address]["role"]
        del self._connected_peers[address]

        # Trigger callback
        if self.on_device_disconnected:
            self.on_device_disconnected(address)

        # If linked, trigger disconnect on other side
        if self._linked_driver and self._linked_driver.local_address == address:
            if role == "central":
                self._linked_driver._handle_disconnect(self.local_address)
            else:
                self._linked_driver._handle_disconnect(self.local_address)

    def _handle_disconnect(self, address: str):
        """Internal: Handle disconnection initiated by peer."""
        if address not in self._connected_peers:
            return

        del self._connected_peers[address]

        if self.on_device_disconnected:
            self.on_device_disconnected(address)

    def send(self, address: str, data: bytes):
        """
        Send data to a connected peer.

        Role-aware: automatically routes to linked driver's on_data_received.
        """
        if address not in self._connected_peers:
            raise ConnectionError(f"Not connected to {address}")

        # Track for assertions
        self.sent_data.append((address, data))

        # If linked driver exists, deliver data
        if self._linked_driver and self._linked_driver.local_address == address:
            if self._linked_driver.on_data_received:
                self._linked_driver.on_data_received(self.local_address, data)

    # --- GATT Characteristic Operations ---

    def read_characteristic(self, address: str, char_uuid: str) -> bytes:
        """
        Read a characteristic value from a peer.

        If linked driver exists, reads from its characteristics.
        """
        if address not in self._connected_peers:
            raise ConnectionError(f"Not connected to {address}")

        # If linked driver, read from its characteristics
        if self._linked_driver and self._linked_driver.local_address == address:
            if char_uuid in self._linked_driver._characteristics:
                return self._linked_driver._characteristics[char_uuid]
            else:
                raise KeyError(f"Characteristic {char_uuid} not found")
        else:
            # For testing without linked driver
            if char_uuid in self._characteristics:
                return self._characteristics[char_uuid]
            else:
                raise KeyError(f"Characteristic {char_uuid} not found")

    def write_characteristic(self, address: str, char_uuid: str, data: bytes):
        """
        Write a characteristic value to a peer.

        If linked driver exists, writes to its characteristics.
        """
        if address not in self._connected_peers:
            raise ConnectionError(f"Not connected to {address}")

        # If linked driver, write to its characteristics
        if self._linked_driver and self._linked_driver.local_address == address:
            self._linked_driver._characteristics[char_uuid] = data
        else:
            # For testing without linked driver
            self._characteristics[char_uuid] = data

    def start_notify(self, address: str, char_uuid: str, callback: Callable[[bytes], None]):
        """
        Subscribe to notifications from a characteristic.

        In the mock, this is a no-op since data delivery is automatic via send().
        """
        if address not in self._connected_peers:
            raise ConnectionError(f"Not connected to {address}")
        # In mock, notifications are handled automatically via send()
        pass

    # --- Configuration & Queries ---

    def get_local_address(self) -> str:
        """Return the simulated local MAC address."""
        return self.local_address

    def set_service_discovery_delay(self, seconds: float):
        """Set service discovery delay (no-op in mock)."""
        self._service_discovery_delay = seconds

    def set_power_mode(self, mode: str):
        """Set power mode (tracked but not enforced in mock)."""
        self._power_mode = mode

    # --- Test Helper Methods ---

    def simulate_device_discovered(self, address: str, name: str, rssi: int,
                                   service_uuids: Optional[List[str]] = None,
                                   manufacturer_data: Optional[Dict[int, bytes]] = None):
        """
        Simulate discovering a BLE device.

        Args:
            address: Device MAC address
            name: Device name
            rssi: Signal strength
            service_uuids: Optional list of advertised service UUIDs
            manufacturer_data: Optional manufacturer data
        """
        if self._state != DriverState.SCANNING:
            return

        device = BLEDevice(
            address=address,
            name=name,
            rssi=rssi,
            service_uuids=service_uuids or [],
            manufacturer_data=manufacturer_data or {}
        )

        if self.on_device_discovered:
            self.on_device_discovered(device)

    def simulate_mtu_change(self, address: str, new_mtu: int):
        """
        Simulate MTU renegotiation on an existing connection.

        Args:
            address: Peer address
            new_mtu: New MTU value
        """
        if address not in self._connected_peers:
            return

        self._connected_peers[address]["mtu"] = new_mtu

        if self.on_mtu_negotiated:
            self.on_mtu_negotiated(address, new_mtu)

    def simulate_error(self, severity: str, message: str, exception: Optional[Exception] = None):
        """
        Simulate a platform error.

        Args:
            severity: "warning" or "error"
            message: Error message
            exception: Optional exception object
        """
        if self.on_error:
            self.on_error(severity, message, exception)

    def get_peer_role(self, address: str) -> Optional[str]:
        """
        Get the connection role for a peer.

        Args:
            address: Peer address

        Returns:
            "central" or "peripheral", or None if not connected
        """
        if address in self._connected_peers:
            return self._connected_peers[address]["role"]
        return None

    @staticmethod
    def link_drivers(driver1: 'MockBLEDriver', driver2: 'MockBLEDriver'):
        """
        Link two mock drivers for bidirectional communication.

        This simulates a pair of BLE devices that can discover, connect,
        and exchange data with each other.

        Args:
            driver1: First driver
            driver2: Second driver
        """
        driver1._linked_driver = driver2
        driver2._linked_driver = driver1

    def reset(self):
        """Reset the mock driver to initial state (useful between tests)."""
        self.stop()
        self.sent_data.clear()
        self._characteristics.clear()
        self._identity = None
