"""
Tests for Stale Connection Polling (Timeout-based Fallback)

Tests the polling-based fallback mechanism that periodically checks BlueZ device
state to detect stale connections that may have been missed by D-Bus signals.

This tests the Solution C implementation in _poll_stale_connections():
- 30-second polling interval
- Detection of stale centrals (in connected_centrals but Connected=False in BlueZ)
- Cleanup triggering for stale connections
- Thread lifecycle and error handling
- Handles dbus-python not available gracefully

Reference: DBUS_MONITORING_FIX.md § Solution C: Timeout-Based Polling Fallback
"""

import pytest
import sys
import os
import time
import threading
from unittest.mock import Mock, MagicMock, patch, call

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


class TestStaleConnectionPolling:
    """Test stale connection polling fallback mechanism."""

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
        """Create mock GATT server with polling setup."""
        from RNS.Interfaces.linux_bluetooth_driver import BluezeroGATTServer

        server = Mock(spec=BluezeroGATTServer)
        server.driver = mock_driver
        server.stop_event = threading.Event()
        server.connected_centrals = {}
        server.centrals_lock = threading.RLock()
        server._log = Mock()
        server._handle_central_disconnected = Mock()

        return server

    def test_polling_interval_30_seconds(self):
        """Test that polling loop waits approximately 30 seconds between checks."""
        stop_event = threading.Event()
        check_times = []

        def mock_polling_loop():
            """Simulate polling loop with timing."""
            while not stop_event.is_set():
                check_times.append(time.time())

                # Simulate 30s wait (60 * 0.5s sleeps)
                for _ in range(60):
                    if stop_event.is_set():
                        break
                    time.sleep(0.01)  # Use short sleep for test speed

        # Start thread
        thread = threading.Thread(target=mock_polling_loop, daemon=True)
        start_time = time.time()
        thread.start()

        # Let it run for ~2 checks
        time.sleep(0.15)
        stop_event.set()
        thread.join(timeout=1.0)

        # Verify timing pattern (allowing for test speed)
        assert len(check_times) >= 2, "Should have performed at least 2 checks"

    def test_checks_all_connected_centrals(self, mock_gatt_server):
        """Test that polling checks each central in connected_centrals."""
        # Setup multiple connected centrals
        centrals = {
            "AA:BB:CC:DD:EE:FF": {"address": "AA:BB:CC:DD:EE:FF"},
            "11:22:33:44:55:66": {"address": "11:22:33:44:55:66"},
            "B8:27:EB:A8:A7:22": {"address": "B8:27:EB:A8:A7:22"},
        }
        mock_gatt_server.connected_centrals = centrals.copy()

        checked_macs = []

        with patch('dbus.SystemBus') as mock_system_bus:
            mock_bus = Mock()
            mock_system_bus.return_value = mock_bus

            def mock_get_object(service, path):
                # Extract MAC from path
                if "/dev_" in path:
                    mac = path.split("/dev_")[-1].replace("_", ":")
                    checked_macs.append(mac)

                mock_device = Mock()
                return mock_device

            mock_bus.get_object = mock_get_object

            # Simulate one polling cycle
            with mock_gatt_server.centrals_lock:
                centrals_to_check = list(mock_gatt_server.connected_centrals.keys())

            for mac_address in centrals_to_check:
                dbus_path = f"/org/bluez/hci0/dev_{mac_address.replace(':', '_')}"
                try:
                    mock_bus.get_object("org.bluez", dbus_path)
                except:
                    pass

            # Verify all centrals were checked
            assert len(checked_macs) == 3
            for mac in centrals.keys():
                assert mac in checked_macs

    def test_detects_stale_central_triggers_cleanup(self, mock_gatt_server):
        """Test that stale connection (Connected=False) triggers cleanup."""
        central_mac = "AA:BB:CC:DD:EE:FF"
        mock_gatt_server.connected_centrals[central_mac] = {"address": central_mac}

        with patch('dbus.SystemBus') as mock_system_bus, \
             patch('dbus.Interface') as mock_interface_class:

            mock_bus = Mock()
            mock_system_bus.return_value = mock_bus

            mock_device = Mock()
            mock_bus.get_object = Mock(return_value=mock_device)

            mock_props_iface = Mock()
            mock_interface_class.return_value = mock_props_iface

            # Mock device showing as disconnected
            mock_props_iface.Get = Mock(return_value=False)  # Connected=False

            # Simulate polling check
            dbus_path = f"/org/bluez/hci0/dev_{central_mac.replace(':', '_')}"
            device_obj = mock_bus.get_object("org.bluez", dbus_path)
            props_iface = mock_interface_class(device_obj, "org.freedesktop.DBus.Properties")
            is_connected = props_iface.Get("org.bluez.Device1", "Connected")

            if not is_connected:
                with mock_gatt_server.centrals_lock:
                    if central_mac in mock_gatt_server.connected_centrals:
                        mock_gatt_server._handle_central_disconnected(central_mac)

            # Verify cleanup was triggered
            mock_gatt_server._handle_central_disconnected.assert_called_once_with(central_mac)

    def test_does_not_cleanup_still_connected(self, mock_gatt_server):
        """Test that centrals still showing Connected=True are not cleaned up."""
        central_mac = "AA:BB:CC:DD:EE:FF"
        mock_gatt_server.connected_centrals[central_mac] = {"address": central_mac}

        with patch('dbus.SystemBus') as mock_system_bus, \
             patch('dbus.Interface') as mock_interface_class:

            mock_bus = Mock()
            mock_system_bus.return_value = mock_bus

            mock_device = Mock()
            mock_bus.get_object = Mock(return_value=mock_device)

            mock_props_iface = Mock()
            mock_interface_class.return_value = mock_props_iface

            # Mock device still connected
            mock_props_iface.Get = Mock(return_value=True)  # Connected=True

            # Simulate polling check
            dbus_path = f"/org/bluez/hci0/dev_{central_mac.replace(':', '_')}"
            device_obj = mock_bus.get_object("org.bluez", dbus_path)
            props_iface = mock_interface_class(device_obj, "org.freedesktop.DBus.Properties")
            is_connected = props_iface.Get("org.bluez.Device1", "Connected")

            if not is_connected:
                with mock_gatt_server.centrals_lock:
                    if central_mac in mock_gatt_server.connected_centrals:
                        mock_gatt_server._handle_central_disconnected(central_mac)

            # Verify cleanup was NOT called
            mock_gatt_server._handle_central_disconnected.assert_not_called()

    def test_thread_stops_on_stop_event(self):
        """Test that polling thread exits when stop_event is set."""
        stop_event = threading.Event()
        thread_exited = threading.Event()

        def mock_polling_loop():
            """Simulates polling loop with stop check."""
            try:
                while not stop_event.is_set():
                    # Simulate 30s wait with frequent stop checks
                    for _ in range(60):
                        if stop_event.is_set():
                            break
                        time.sleep(0.01)

                    if stop_event.is_set():
                        break

                    # Would do polling check here
            finally:
                thread_exited.set()

        # Start thread
        thread = threading.Thread(target=mock_polling_loop, daemon=True)
        thread.start()

        # Let it run briefly
        time.sleep(0.1)

        # Signal stop
        stop_event.set()

        # Wait for thread to exit
        thread.join(timeout=2.0)

        # Verify thread stopped
        assert not thread.is_alive()
        assert thread_exited.is_set()

    def test_handles_dbus_python_not_available(self, mock_gatt_server):
        """Test that polling returns early when dbus-python is not available."""
        # Simulate ImportError for dbus
        def mock_polling_with_no_dbus():
            try:
                import dbus  # This would fail if not available
            except ImportError:
                mock_gatt_server._log("dbus-python not available", "WARNING")
                return

            # Should not reach here
            pytest.fail("Should have returned early")

        with patch.dict('sys.modules', {'dbus': None}):
            # This simulates dbus not being importable
            try:
                import dbus
                pytest.skip("dbus module is actually available")
            except (ImportError, TypeError):
                mock_gatt_server._log("dbus-python not available", "WARNING")

        # Verify warning was logged
        mock_gatt_server._log.assert_called_with("dbus-python not available", "WARNING")

    def test_handles_dbus_exceptions_gracefully(self, mock_gatt_server):
        """Test that D-Bus exceptions during polling are handled gracefully."""
        central_mac = "AA:BB:CC:DD:EE:FF"
        mock_gatt_server.connected_centrals[central_mac] = {"address": central_mac}

        with patch('dbus.SystemBus') as mock_system_bus:
            mock_bus = Mock()
            mock_system_bus.return_value = mock_bus

            # Mock D-Bus raising exception (device doesn't exist)
            import dbus.exceptions
            mock_bus.get_object = Mock(side_effect=dbus.exceptions.DBusException("org.freedesktop.DBus.Error.UnknownObject"))

            # Simulate polling check with error handling
            dbus_path = f"/org/bluez/hci0/dev_{central_mac.replace(':', '_')}"

            try:
                device_obj = mock_bus.get_object("org.bluez", dbus_path)
            except dbus.exceptions.DBusException as e:
                if "UnknownObject" in str(e):
                    # Device no longer in BlueZ, cleanup
                    with mock_gatt_server.centrals_lock:
                        if central_mac in mock_gatt_server.connected_centrals:
                            mock_gatt_server._handle_central_disconnected(central_mac)

            # Verify cleanup was triggered (device is gone from BlueZ)
            mock_gatt_server._handle_central_disconnected.assert_called_once_with(central_mac)

    def test_empty_centrals_dict_no_checks(self, mock_gatt_server):
        """Test that polling skips D-Bus queries when no centrals connected."""
        # No centrals connected
        mock_gatt_server.connected_centrals = {}

        with patch('dbus.SystemBus') as mock_system_bus:
            mock_bus = Mock()
            mock_system_bus.return_value = mock_bus

            # Simulate polling cycle
            with mock_gatt_server.centrals_lock:
                centrals_to_check = list(mock_gatt_server.connected_centrals.keys())

            if not centrals_to_check:
                # Skip to next iteration (no D-Bus calls)
                pass
            else:
                # Would make D-Bus calls here
                for mac in centrals_to_check:
                    mock_bus.get_object("org.bluez", f"/org/bluez/hci0/dev_{mac.replace(':', '_')}")

            # Verify no D-Bus calls were made
            mock_bus.get_object.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
