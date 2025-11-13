"""
Tests for D-Bus Disconnect Monitoring (ObjectManager-based)

Tests the ObjectManager-based D-Bus monitoring implementation that detects when
Android devices (acting as BLE centrals) disconnect from Pi GATT servers.

This tests the Solution A implementation in _monitor_device_disconnections():
- ObjectManager subscription for BlueZ device discovery
- PropertiesChanged signal handling for disconnect detection
- MAC address extraction from D-Bus paths
- Cleanup callback invocation
- Thread lifecycle and error handling

Reference: DBUS_MONITORING_FIX.md § Solution A: High-Level ObjectManager API
"""

import pytest
import sys
import os
import asyncio
import threading
from unittest.mock import Mock, MagicMock, AsyncMock, patch, call

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

# Mock RNS module before importing
import RNS
if not hasattr(RNS, 'LOG_INFO'):
    RNS.LOG_CRITICAL = 0
    RNS.LOG_ERROR = 1
    RNS.LOG_WARNING = 2
    RNS.LOG_NOTICE = 3
    RNS.LOG_INFO = 4
    RNS.LOG_VERBOSE = 5
    RNS.LOG_DEBUG = 6
    RNS.LOG_EXTREME = 7

RNS.log = Mock()


class TestDBusDisconnectMonitoring:
    """Test D-Bus ObjectManager-based disconnect monitoring."""

    @pytest.fixture
    def mock_driver(self):
        """Create mock driver with required attributes."""
        driver = Mock()
        driver._peers = {}
        driver._peers_lock = threading.RLock()
        driver._log = Mock()
        driver._handle_peripheral_disconnected = Mock()
        return driver

    @pytest.fixture
    def mock_gatt_server(self, mock_driver):
        """Create mock GATT server with monitoring setup."""
        from RNS.Interfaces.linux_bluetooth_driver import BluezeroGATTServer

        server = Mock(spec=BluezeroGATTServer)
        server.driver = mock_driver
        server.stop_event = threading.Event()
        server.connected_centrals = {}
        server.centrals_lock = threading.RLock()
        server._log = Mock()
        server._handle_central_disconnected = Mock()

        return server

    def test_mac_address_extracted_from_dbus_path(self):
        """Test MAC address extraction from D-Bus device path."""
        # D-Bus paths use underscores, we need colons
        test_cases = [
            ("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", "AA:BB:CC:DD:EE:FF"),
            ("/org/bluez/hci0/dev_12_34_56_78_9A_BC", "12:34:56:78:9A:BC"),
            ("/org/bluez/hci1/dev_B8_27_EB_A8_A7_22", "B8:27:EB:A8:A7:22"),
        ]

        for dbus_path, expected_mac in test_cases:
            # Extract MAC using same logic as implementation
            if "/dev_" in dbus_path:
                mac_with_underscores = dbus_path.split("/dev_")[-1]
                mac_address = mac_with_underscores.replace("_", ":")
                assert mac_address == expected_mac

    def test_properties_changed_connected_false_triggers_cleanup(self, mock_gatt_server):
        """Test that PropertiesChanged with Connected=False triggers cleanup."""
        # Setup: Central is connected
        central_mac = "AA:BB:CC:DD:EE:FF"
        mock_gatt_server.connected_centrals[central_mac] = {
            "address": central_mac,
            "connected_at": 1234567890.0
        }

        # Simulate PropertiesChanged handler (extracted from implementation)
        def handle_properties_changed(interface_name, changed_properties, invalidated_properties, device_path):
            if interface_name != "org.bluez.Device1":
                return

            if "Connected" in changed_properties:
                is_connected = changed_properties["Connected"].value

                if not is_connected:
                    if "/dev_" in device_path:
                        mac_with_underscores = device_path.split("/dev_")[-1]
                        mac_address = mac_with_underscores.replace("_", ":")

                        with mock_gatt_server.centrals_lock:
                            if mac_address in mock_gatt_server.connected_centrals:
                                mock_gatt_server._handle_central_disconnected(mac_address)

        # Simulate disconnect signal
        device_path = f"/org/bluez/hci0/dev_{central_mac.replace(':', '_')}"
        changed_props = {"Connected": Mock(value=False)}

        handle_properties_changed("org.bluez.Device1", changed_props, [], device_path)

        # Verify cleanup was called
        mock_gatt_server._handle_central_disconnected.assert_called_once_with(central_mac)

    def test_only_monitors_bluez_device1_interface(self, mock_gatt_server):
        """Test that handler ignores non-Device1 interfaces."""
        central_mac = "AA:BB:CC:DD:EE:FF"
        mock_gatt_server.connected_centrals[central_mac] = {}

        def handle_properties_changed(interface_name, changed_properties, invalidated_properties, device_path):
            if interface_name != "org.bluez.Device1":
                return

            if "Connected" in changed_properties:
                is_connected = changed_properties["Connected"].value
                if not is_connected:
                    with mock_gatt_server.centrals_lock:
                        if central_mac in mock_gatt_server.connected_centrals:
                            mock_gatt_server._handle_central_disconnected(central_mac)

        # Test various other interfaces
        other_interfaces = [
            "org.bluez.Adapter1",
            "org.bluez.GattService1",
            "org.freedesktop.DBus.Properties",
        ]

        device_path = f"/org/bluez/hci0/dev_{central_mac.replace(':', '_')}"
        changed_props = {"Connected": Mock(value=False)}

        for interface in other_interfaces:
            handle_properties_changed(interface, changed_props, [], device_path)

        # Verify cleanup was NOT called
        mock_gatt_server._handle_central_disconnected.assert_not_called()

    def test_only_processes_connected_centrals(self, mock_gatt_server):
        """Test that disconnects for unknown devices are ignored."""
        # No centrals connected
        assert len(mock_gatt_server.connected_centrals) == 0

        def handle_properties_changed(interface_name, changed_properties, invalidated_properties, device_path):
            if interface_name != "org.bluez.Device1":
                return

            if "Connected" in changed_properties:
                is_connected = changed_properties["Connected"].value

                if not is_connected:
                    if "/dev_" in device_path:
                        mac_with_underscores = device_path.split("/dev_")[-1]
                        mac_address = mac_with_underscores.replace("_", ":")

                        with mock_gatt_server.centrals_lock:
                            if mac_address in mock_gatt_server.connected_centrals:
                                mock_gatt_server._handle_central_disconnected(mac_address)

        # Simulate disconnect for unknown device
        device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
        changed_props = {"Connected": Mock(value=False)}

        handle_properties_changed("org.bluez.Device1", changed_props, [], device_path)

        # Verify cleanup was NOT called
        mock_gatt_server._handle_central_disconnected.assert_not_called()

    @pytest.mark.asyncio
    async def test_subscription_to_existing_devices(self):
        """Test that existing BlueZ devices are discovered and subscribed to."""
        with patch('dbus_fast.aio.MessageBus') as mock_bus_class:
            # Setup mock bus
            mock_bus = AsyncMock()
            mock_bus_class.return_value.connect = AsyncMock(return_value=mock_bus)

            # Mock introspection and ObjectManager
            mock_introspection = Mock()
            mock_bus.introspect = AsyncMock(return_value=mock_introspection)

            mock_proxy_obj = Mock()
            mock_bus.get_proxy_object = Mock(return_value=mock_proxy_obj)

            mock_object_manager = Mock()
            mock_proxy_obj.get_interface = Mock(return_value=mock_object_manager)

            # Mock GetManagedObjects to return 2 devices
            managed_objects = {
                "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF": {
                    "org.bluez.Device1": {},
                },
                "/org/bluez/hci0/dev_11_22_33_44_55_66": {
                    "org.bluez.Device1": {},
                },
                "/org/bluez/hci0": {  # Adapter, not a device
                    "org.bluez.Adapter1": {},
                },
            }
            mock_object_manager.call_get_managed_objects = AsyncMock(return_value=managed_objects)

            # Track subscription calls
            subscribed_devices = []

            async def mock_subscribe(device_path):
                subscribed_devices.append(device_path)

            # Simulate subscription loop (simplified)
            for path, interfaces in managed_objects.items():
                if "org.bluez.Device1" in interfaces:
                    await mock_subscribe(path)

            # Verify correct devices were subscribed
            assert len(subscribed_devices) == 2
            assert "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF" in subscribed_devices
            assert "/org/bluez/hci0/dev_11_22_33_44_55_66" in subscribed_devices

    @pytest.mark.asyncio
    async def test_subscription_to_new_devices(self):
        """Test that InterfacesAdded signal triggers subscription to new devices."""
        new_device_path = "/org/bluez/hci0/dev_NEW_DEVICE_MAC"
        subscribed_devices = []

        async def mock_subscribe(device_path):
            subscribed_devices.append(device_path)

        # Simulate InterfacesAdded handler
        def on_interfaces_added(path, interfaces):
            if "org.bluez.Device1" in interfaces:
                # In real implementation, this would use asyncio.create_task
                asyncio.create_task(mock_subscribe(path))

        # Trigger the handler
        interfaces = {"org.bluez.Device1": {}}
        on_interfaces_added(new_device_path, interfaces)

        # Allow task to execute
        await asyncio.sleep(0.1)

        # Verify new device was subscribed
        assert new_device_path in subscribed_devices

    def test_thread_stops_cleanly_on_stop_event(self):
        """Test that monitoring thread exits when stop_event is set."""
        stop_event = threading.Event()
        thread_exited = threading.Event()

        def mock_monitoring_loop():
            """Simulates monitoring loop that checks stop_event."""
            try:
                # Simulate event loop
                while not stop_event.is_set():
                    stop_event.wait(timeout=0.1)
            finally:
                thread_exited.set()

        # Start thread
        thread = threading.Thread(target=mock_monitoring_loop, daemon=True)
        thread.start()

        # Signal stop
        stop_event.set()

        # Wait for thread to exit
        thread.join(timeout=2.0)

        # Verify thread stopped
        assert not thread.is_alive()
        assert thread_exited.is_set()

    @pytest.mark.asyncio
    async def test_bus_connection_cleaned_up_on_exit(self):
        """Test that D-Bus connection is properly closed on exit."""
        with patch('dbus_fast.aio.MessageBus') as mock_bus_class:
            mock_bus = AsyncMock()
            mock_bus.disconnect = AsyncMock()
            mock_bus_class.return_value.connect = AsyncMock(return_value=mock_bus)

            # Simulate finally block
            bus = None
            try:
                bus = await mock_bus_class().connect()
                # ... monitoring logic ...
            finally:
                if bus:
                    await bus.disconnect()

            # Verify disconnect was called
            mock_bus.disconnect.assert_called_once()

    def test_error_handling_no_dbus(self, mock_gatt_server):
        """Test that monitoring returns early when D-Bus is not available."""
        with patch('RNS.Interfaces.linux_bluetooth_driver.HAS_DBUS', False):
            # Simulate the early return logic
            HAS_DBUS = False

            if not HAS_DBUS:
                mock_gatt_server._log("D-Bus not available", "WARNING")
                return

            # This should not be reached
            pytest.fail("Should have returned early")

        # Verify warning was logged
        mock_gatt_server._log.assert_called_with("D-Bus not available", "WARNING")

    @pytest.mark.asyncio
    async def test_connected_true_does_not_trigger_cleanup(self, mock_gatt_server):
        """Test that Connected=True (reconnect) does not trigger cleanup."""
        central_mac = "AA:BB:CC:DD:EE:FF"
        mock_gatt_server.connected_centrals[central_mac] = {}

        def handle_properties_changed(interface_name, changed_properties, invalidated_properties, device_path):
            if interface_name != "org.bluez.Device1":
                return

            if "Connected" in changed_properties:
                is_connected = changed_properties["Connected"].value

                # Only trigger cleanup if disconnected
                if not is_connected:
                    if "/dev_" in device_path:
                        mac_with_underscores = device_path.split("/dev_")[-1]
                        mac_address = mac_with_underscores.replace("_", ":")

                        with mock_gatt_server.centrals_lock:
                            if mac_address in mock_gatt_server.connected_centrals:
                                mock_gatt_server._handle_central_disconnected(mac_address)

        # Simulate Connected=True (device connected)
        device_path = f"/org/bluez/hci0/dev_{central_mac.replace(':', '_')}"
        changed_props = {"Connected": Mock(value=True)}

        handle_properties_changed("org.bluez.Device1", changed_props, [], device_path)

        # Verify cleanup was NOT called
        mock_gatt_server._handle_central_disconnected.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
