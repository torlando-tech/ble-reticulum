"""
pytest configuration for BLE interface tests.

This file is automatically loaded by pytest before test collection begins.
It sets up the Python path to allow imports from src/ and Reticulum.
"""

import sys
import os

# Calculate paths relative to this file's location
tests_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(tests_dir)
src_dir = os.path.join(project_root, 'src')

# Add src/ to path for BLE interface modules
# This allows tests to import from src/RNS/Interfaces/
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# Note: Some test files (test_gatt_integration.py, test_ble_integration.py) have
# import issues due to Python's namespace package limitations. They can't be run
# with 'pytest tests/' but work individually. This is expected until the BLE
# interface is fully integrated into the Reticulum repository.

import pytest
import asyncio
import time
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from types import ModuleType


# ============================================================================
# Mock RNS module functions for testing
# ============================================================================

# Don't import the real RNS here as it may have crypto dependencies
# Instead, check if RNS stub exists in src/RNS/ and use that
rns_stub_path = os.path.join(src_dir, 'RNS')
if os.path.exists(os.path.join(rns_stub_path, '__init__.py')):
    # RNS stub exists, we can import it
    try:
        import RNS
        # Add mock functions if not already present
        if not hasattr(RNS, 'LOG_INFO'):
            RNS.LOG_CRITICAL = 0
            RNS.LOG_ERROR = 1
            RNS.LOG_WARNING = 2
            RNS.LOG_NOTICE = 3
            RNS.LOG_INFO = 4
            RNS.LOG_VERBOSE = 5
            RNS.LOG_DEBUG = 6
            RNS.LOG_EXTREME = 7

        if not hasattr(RNS, 'log'):
            def mock_log(message, level=4):
                pass
            RNS.log = mock_log

        if not hasattr(RNS, 'prettyhexrep'):
            def mock_prettyhexrep(data):
                return data.hex() if isinstance(data, bytes) else str(data)
            RNS.prettyhexrep = mock_prettyhexrep

        if not hasattr(RNS, 'hexrep'):
            def mock_hexrep(data, delimit=True):
                if isinstance(data, bytes):
                    hex_str = data.hex()
                    if delimit:
                        return ':'.join(hex_str[i:i+2] for i in range(0, len(hex_str), 2))
                    return hex_str
                return str(data)
            RNS.hexrep = mock_hexrep
    except Exception as e:
        # If import fails, tests will handle RNS mocking individually
        pass


# ============================================================================
# Async Fixtures
# ============================================================================

@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def mock_event_loop():
    """Create a mock event loop that can be used in tests."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


# ============================================================================
# Mock BLE Components
# ============================================================================

@pytest.fixture
def mock_bleak_client():
    """Create a mock BleakClient for testing central mode operations."""
    client = AsyncMock()
    client.address = "AA:BB:CC:DD:EE:FF"
    client.is_connected = True
    client.mtu_size = 185
    client.connect = AsyncMock(return_value=True)
    client.disconnect = AsyncMock(return_value=True)
    client.start_notify = AsyncMock(return_value=True)
    client.stop_notify = AsyncMock(return_value=True)
    client.write_gatt_char = AsyncMock(return_value=True)
    return client


@pytest.fixture
def mock_bleak_device():
    """Create a mock BLE device discovered during scan."""
    device = Mock()
    device.address = "AA:BB:CC:DD:EE:FF"
    device.name = "Test-Device"
    device.metadata = {
        "uuids": ["00000001-5824-4f48-9e1a-3b3e8f0c1234"],
        "rssi": -65
    }
    return device


@pytest.fixture
def mock_bleak_scanner():
    """Create a mock BleakScanner for testing discovery."""
    async def mock_discover(timeout=1.0):
        """Return mock discovered devices."""
        device1 = Mock()
        device1.address = "AA:BB:CC:DD:EE:01"
        device1.name = "Device-1"
        device1.metadata = {
            "uuids": ["00000001-5824-4f48-9e1a-3b3e8f0c1234"],
            "rssi": -50
        }

        device2 = Mock()
        device2.address = "AA:BB:CC:DD:EE:02"
        device2.name = "Device-2"
        device2.metadata = {
            "uuids": ["00000001-5824-4f48-9e1a-3b3e8f0c1234"],
            "rssi": -70
        }

        return [device1, device2]

    with patch('bleak.BleakScanner.discover', side_effect=mock_discover):
        yield


@pytest.fixture
def mock_bless_server():
    """Create a mock BlessServer for testing GATT server operations."""
    server = AsyncMock()
    server.add_new_service = AsyncMock(return_value=True)
    server.add_new_characteristic = AsyncMock(return_value=True)
    server.update_value = AsyncMock(return_value=True)
    server.start = AsyncMock(return_value=True)
    server.stop = AsyncMock(return_value=True)
    return server


# ============================================================================
# Mock RNS Components
# ============================================================================

@pytest.fixture
def mock_rns_owner():
    """Create a mock Reticulum Transport owner for BLEInterface."""
    owner = Mock()
    owner.inbound = Mock()
    return owner


@pytest.fixture
def mock_rns_transport():
    """Mock RNS.Transport for interface registration."""
    with patch('RNS.Transport') as mock_transport:
        mock_transport.interfaces = []
        yield mock_transport


@pytest.fixture
def mock_rns_identity():
    """Mock RNS.Identity for testing."""
    with patch('RNS.Identity') as mock_identity:
        mock_identity.full_hash = Mock(return_value=b'\x01\x02\x03\x04')
        yield mock_identity


# ============================================================================
# Common Test Data
# ============================================================================

@pytest.fixture
def sample_packet_data():
    """Sample packet data for testing."""
    return {
        'small': b'Hello, BLE!' * 1,  # ~11 bytes
        'medium': b'Hello, BLE!' * 20,  # ~220 bytes
        'large': b'Hello, BLE!' * 100,  # ~1100 bytes
        'empty': b'',
        'single_byte': b'\x42',
    }


@pytest.fixture
def sample_configuration():
    """Sample BLEInterface configuration for testing."""
    return {
        'name': 'TestBLEInterface',
        'enabled': True,
        'service_uuid': '00000001-5824-4f48-9e1a-3b3e8f0c1234',
        'device_name': 'Test-Node',
        'discovery_interval': 5.0,
        'max_connections': 7,
        'min_rssi': -80,
        'connection_timeout': 10.0,
        'power_mode': 'balanced',
        'enable_peripheral': True,
        'connection_rotation_interval': 600,
        'connection_retry_backoff': 60,
        'max_connection_failures': 3,
    }


@pytest.fixture
def sample_discovered_peers():
    """Sample DiscoveredPeer objects for testing."""
    try:
        from RNS.Interfaces.BLEInterface import DiscoveredPeer
    except ImportError:
        # Create a simple mock DiscoveredPeer for testing
        import time

        class DiscoveredPeer:
            def __init__(self, address, name, rssi):
                self.address = address
                self.name = name
                self.rssi = rssi
                self.first_seen = time.time()
                self.last_seen = time.time()
                self.connection_attempts = 0
                self.successful_connections = 0
                self.failed_connections = 0
                self.last_connection_attempt = 0

            def update_rssi(self, rssi):
                self.rssi = rssi
                self.last_seen = time.time()

            def record_connection_attempt(self):
                self.connection_attempts += 1
                self.last_connection_attempt = time.time()

            def record_connection_success(self):
                self.successful_connections += 1

            def record_connection_failure(self):
                self.failed_connections += 1

            def get_success_rate(self):
                if self.connection_attempts == 0:
                    return 0.0
                return self.successful_connections / self.connection_attempts

    peer1 = DiscoveredPeer("AA:BB:CC:DD:EE:01", "Device-1", -50)
    peer2 = DiscoveredPeer("AA:BB:CC:DD:EE:02", "Device-2", -70)
    peer3 = DiscoveredPeer("AA:BB:CC:DD:EE:03", "Device-3", -90)

    return {
        'strong': peer1,
        'medium': peer2,
        'weak': peer3,
        'all': [peer1, peer2, peer3]
    }


# ============================================================================
# Helper Functions
# ============================================================================

def create_mock_ble_interface(owner=None, config=None):
    """
    Create a mock BLEInterface instance for testing.

    Args:
        owner: Mock RNS owner (optional)
        config: Configuration dict (optional)

    Returns:
        Mock BLEInterface with necessary attributes
    """
    interface = Mock()
    interface.name = config.get('name', 'TestBLE') if config else 'TestBLE'
    interface.online = True
    interface.owner = owner or Mock()
    interface.peers = {}
    interface.spawned_interfaces = {}
    interface.discovered_peers = {}
    interface.connection_blacklist = {}
    interface.fragmenters = {}
    interface.reassemblers = {}
    interface.peer_lock = asyncio.Lock()
    interface.frag_lock = asyncio.Lock()
    interface.loop = asyncio.get_event_loop()
    interface.max_peers = config.get('max_connections', 7) if config else 7
    interface.min_rssi = config.get('min_rssi', -80) if config else -80
    return interface


def wait_for_async(coro, timeout=2.0):
    """
    Helper to wait for an async coroutine in synchronous tests.

    Args:
        coro: Async coroutine to wait for
        timeout: Maximum time to wait in seconds

    Returns:
        Result of the coroutine
    """
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
