#!/usr/bin/env python3
"""
Quick test script to verify D-Bus monitoring threads start correctly.
"""
import sys
import time
import threading

# Add src to path
sys.path.insert(0, 'src')

from RNS.Interfaces.linux_bluetooth_driver import BluezeroGATTServer

print("=" * 60)
print("Testing D-Bus Monitoring Thread Startup")
print("=" * 60)

# Create a mock driver with minimal attributes needed
class MockDriver:
    def __init__(self):
        self._peers = {}
        self._peers_lock = threading.RLock()

    def _log(self, msg, level="INFO"):
        print(f"[{level}] {msg}")

    def _handle_peripheral_disconnected(self, address):
        print(f"[MOCK] Peripheral disconnected callback: {address}")

# Create GATT server instance
driver = MockDriver()
gatt_server = BluezeroGATTServer(
    driver=driver,
    adapter_index=0,
    service_uuid="00000000-0000-0000-0000-000000000000",
    rx_char_uuid="00000000-0000-0000-0000-000000000001",
    tx_char_uuid="00000000-0000-0000-0000-000000000002",
    identity_char_uuid="00000000-0000-0000-0000-000000000003"
)

# Set identity (required before start)
gatt_server.identity_bytes = b'0' * 16

print("\nAttempting to start monitoring threads (without full GATT server)...")
print("This will test if the threads can be created and started.\n")

# Manually start just the monitoring threads
print("[TEST] Starting D-Bus disconnect monitoring thread...")
try:
    gatt_server.disconnect_monitor_thread = threading.Thread(
        target=gatt_server._monitor_device_disconnections,
        daemon=True,
        name="test-dbus-monitor"
    )
    gatt_server.disconnect_monitor_thread.start()
    print("[TEST] ✓ D-Bus monitoring thread started")
except Exception as e:
    print(f"[TEST] ✗ Failed to start D-Bus monitoring thread: {e}")
    import traceback
    traceback.print_exc()

print("\n[TEST] Starting stale connection polling thread...")
try:
    gatt_server.stale_poll_thread = threading.Thread(
        target=gatt_server._poll_stale_connections,
        daemon=True,
        name="test-stale-poller"
    )
    gatt_server.stale_poll_thread.start()
    print("[TEST] ✓ Stale polling thread started")
except Exception as e:
    print(f"[TEST] ✗ Failed to start stale polling thread: {e}")
    import traceback
    traceback.print_exc()

print("\n[TEST] Waiting 5 seconds to observe thread behavior...")
print("[TEST] Check stderr output above for [GATT-MONITOR] and [STALE-POLL] messages")
time.sleep(5)

print("\n[TEST] Stopping threads...")
gatt_server.stop_event.set()

# Wait for threads to exit
if gatt_server.disconnect_monitor_thread and gatt_server.disconnect_monitor_thread.is_alive():
    gatt_server.disconnect_monitor_thread.join(timeout=3.0)
    if not gatt_server.disconnect_monitor_thread.is_alive():
        print("[TEST] ✓ D-Bus monitoring thread stopped cleanly")
    else:
        print("[TEST] ✗ D-Bus monitoring thread did not stop")

if gatt_server.stale_poll_thread and gatt_server.stale_poll_thread.is_alive():
    gatt_server.stale_poll_thread.join(timeout=3.0)
    if not gatt_server.stale_poll_thread.is_alive():
        print("[TEST] ✓ Stale polling thread stopped cleanly")
    else:
        print("[TEST] ✗ Stale polling thread did not stop")

print("\n" + "=" * 60)
print("Test complete!")
print("=" * 60)
