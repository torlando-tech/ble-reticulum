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
    """Test that BLEInterface.py has GATT server integration code."""
    interface_path = os.path.join(os.path.dirname(__file__), '../src/RNS/Interfaces/BLEInterface.py')
    with open(interface_path, 'r') as f:
        code = f.read()

    # Check for GATT server imports (uses try/except fallback pattern)
    assert 'from RNS.Interfaces.BLEGATTServer import BLEGATTServer' in code
    assert 'HAS_GATT_SERVER' in code

    # Check for peripheral mode configuration
    assert 'enable_peripheral' in code

    # Check for callback methods
    assert 'def handle_peripheral_data(' in code
    assert 'def handle_central_connected(' in code
    assert 'def handle_central_disconnected(' in code
    assert 'def _create_peripheral_peer(' in code
    assert 'def _start_server(' in code

    # Check for detach stops server
    assert 'self.gatt_server.stop()' in code


def test_peer_interface_has_routing():
    """Test that BLEPeerInterface has routing methods."""
    interface_path = os.path.join(os.path.dirname(__file__), '../src/RNS/Interfaces/BLEInterface.py')
    with open(interface_path, 'r') as f:
        code = f.read()

    # Check for connection flag
    assert 'is_peripheral_connection' in code

    # Check for routing methods
    assert 'def _send_via_peripheral(' in code
    assert 'def _send_via_central(' in code

    # Check that process_outgoing routes based on connection type
    assert 'if self.is_peripheral_connection:' in code


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


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
