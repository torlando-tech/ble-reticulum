"""
Integration tests for BLEInterface with GATT server.

Tests the structure and code changes for peripheral mode integration.
"""

import pytest
import os


def test_config_options():
    """Test that configuration option for peripheral mode is documented."""
    # Read config example file
    config_path = os.path.join(os.path.dirname(__file__), '../examples/config_example.toml')
    with open(config_path, 'r') as f:
        config_content = f.read()

    # Check that enable_peripheral is documented
    assert 'enable_peripheral' in config_content
    assert 'peripheral mode' in config_content.lower()
    assert 'GATT server' in config_content


def test_interface_has_gatt_integration():
    """Test that BLEInterface.py uses driver abstraction for peripheral mode."""
    interface_path = os.path.join(os.path.dirname(__file__), '../src/RNS/Interfaces/BLEInterface.py')
    with open(interface_path, 'r') as f:
        code = f.read()

    # Check for driver-based architecture
    assert 'from RNS.Interfaces.bluetooth_driver import BLEDriverInterface' in code or 'bluetooth_driver' in code

    # Check for peripheral mode configuration
    assert 'enable_peripheral' in code

    # Check for callback methods (driver calls these)
    assert 'def _data_received_callback(' in code
    assert 'def _device_connected_callback(' in code
    assert 'def _device_disconnected_callback(' in code

    # Check for peripheral mode callbacks
    assert 'def handle_peripheral_data(' in code
    assert 'def handle_central_connected(' in code

    # Check that driver is used for peripheral operations
    assert 'self.driver' in code


def test_peer_interface_has_routing():
    """Test that BLEPeerInterface uses driver for sending."""
    interface_path = os.path.join(os.path.dirname(__file__), '../src/RNS/Interfaces/BLEInterface.py')
    with open(interface_path, 'r') as f:
        code = f.read()

    # Check that BLEPeerInterface class exists
    assert 'class BLEPeerInterface' in code

    # Check for process_outgoing method
    assert 'def process_outgoing(' in code

    # Check that driver.send() is used (driver handles role-aware routing)
    assert 'self.parent_interface.driver.send(' in code or 'driver.send(' in code


def test_gatt_server_file_exists():
    """Test that BLEGATTServer module exists."""
    server_path = os.path.join(os.path.dirname(__file__), '../src/RNS/Interfaces/BLEGATTServer.py')
    assert os.path.exists(server_path)

    with open(server_path, 'r') as f:
        code = f.read()

    # Check for key classes and methods
    assert 'class BLEGATTServer' in code
    assert 'async def start(' in code
    assert 'async def stop(' in code
    assert 'async def send_notification(' in code


def test_driver_abstraction_exists():
    """Test that driver abstraction layer is properly implemented."""
    # Check driver interface exists
    driver_interface_path = os.path.join(os.path.dirname(__file__), '../src/RNS/Interfaces/bluetooth_driver.py')
    assert os.path.exists(driver_interface_path)

    with open(driver_interface_path, 'r') as f:
        code = f.read()

    # Check for abstract interface
    assert 'class BLEDriverInterface' in code
    assert 'ABC' in code or 'abstractmethod' in code

    # Check Linux driver implementation exists
    linux_driver_path = os.path.join(os.path.dirname(__file__), '../src/RNS/Interfaces/linux_bluetooth_driver.py')
    assert os.path.exists(linux_driver_path)

    with open(linux_driver_path, 'r') as f:
        driver_code = f.read()

    # Check for driver implementation
    assert 'class LinuxBluetoothDriver' in driver_code
    assert 'BLEDriverInterface' in driver_code

    # Check for key driver methods
    assert 'def start_advertising(' in driver_code
    assert 'def stop_advertising(' in driver_code
    assert 'def start_scanning(' in driver_code
    assert 'def connect(' in driver_code
    assert 'def send(' in driver_code


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
