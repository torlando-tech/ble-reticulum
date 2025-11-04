
import pytest
import asyncio
import os
import sys

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from src.RNS.Interfaces.BLEInterface import BLEInterface

class MockReticulum:
    def __init__(self):
        self.transport_enabled = False
        self.is_connected_to_shared_instance = False

    def register_interface(self, interface):
        pass

class MockOwner:
    def __init__(self):
        self.reticulum = MockReticulum()

@pytest.mark.asyncio
async def test_two_device_communication():
    """
    Tests a basic two-device communication scenario where one device acts as a
    peripheral and the other as a central.
    """
    # Create mock owner and configuration for the peripheral device
    peripheral_owner = MockOwner()
    peripheral_config = {
        'name': 'PeripheralInterface',
        'enable_central': False,
        'enable_peripheral': True,
        'device_name': 'TestPeripheral',
    }

    # Create mock owner and configuration for the central device
    central_owner = MockOwner()
    central_config = {
        'name': 'CentralInterface',
        'enable_central': True,
        'enable_peripheral': False,
    }

    # Create the peripheral and central interfaces
    peripheral_interface = BLEInterface(peripheral_owner, peripheral_config)
    central_interface = BLEInterface(central_owner, central_config)

    # Allow some time for the interfaces to start and for discovery to happen
    await asyncio.sleep(10)

    # Check that the central has discovered and connected to the peripheral
    assert len(central_interface.peers) > 0, "Central did not connect to any peers"

    # TODO: Add assertions to verify data exchange

    # Clean up
    await peripheral_interface.stop()
    await central_interface.stop()
