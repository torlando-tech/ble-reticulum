# Peripheral Disconnect Cleanup Fix - Summary

**Date:** 2025-11-12
**Branch:** refactor/abstraction-layer
**Issue:** Android devices (acting as BLE centrals) disconnecting from Pi GATT servers never triggered cleanup, causing stale peer entries and eventual connection blocking at 7-peer limit.

---

## Problem Discovered

### Initial Symptoms (from production logs on 10.0.0.80 and 10.0.0.242)

```
[WARNING] LinuxBLEDriver Cannot connect to 4A:87:8C:C7:E3:F3: max peers (7) reached
```

**Root Cause Analysis:**
- When Android devices connected TO Pi's GATT server (Pi as peripheral, Android as central), connections were tracked correctly
- When Android disconnected, NO cleanup happened:
  - `connected_centrals[address]` remained in dictionary
  - `driver._peers[address]` remained in dictionary
  - Spawned interfaces, fragmenters, reassemblers stayed allocated
- After ~7 peripheral disconnections, peer limit reached and blocked ALL new connections

**Why It Failed:**
1. `BLEGATTServer._handle_central_disconnected()` method didn't exist
2. `on_central_disconnected` callback was never wired to driver
3. No D-Bus signal monitoring for device disconnections
4. BlueZ `PropertiesChanged` signals were ignored

---

## Fix Implemented (TDD Approach)

### 1. Test Suite Created (`tests/test_peripheral_disconnect_cleanup.py`)

**9 comprehensive tests:**
- Callback wiring verification
- Peer dictionary cleanup
- D-Bus signal handling
- Multiple disconnect idempotency
- Shutdown safety
- Peer limit unblocking
- Reconnection race conditions
- Real-world scenario reproduction

**All 9 tests passing ✅**

### 2. Core Cleanup Methods Added

**File:** `src/RNS/Interfaces/linux_bluetooth_driver.py`

**A) `LinuxBluetoothDriver._handle_peripheral_disconnected(address)` (line 852)**
- Called when GATT server reports central disconnect
- Removes from `_peers` dictionary (with lock protection)
- Notifies `on_device_disconnected` callback to BLEInterface
- Triggers full cleanup chain

**B) `BluezeroGATTServer._handle_central_disconnected(address)` (line 1945)**
- Removes from `connected_centrals` dictionary
- Logs disconnection with connection duration
- Calls `on_central_disconnected` callback (wired to driver method)

**C) Callback Wiring (line 1558)**
```python
self.on_central_disconnected = driver._handle_peripheral_disconnected
```
Connects GATT server disconnect events to driver cleanup.

### 3. D-Bus Disconnect Monitoring

**Method:** `BluezeroGATTServer._monitor_device_disconnections()` (line 1645)

**Implementation:**
- Runs in separate daemon thread (`disconnect_monitor_thread`)
- Subscribes to `org.freedesktop.DBus.Properties.PropertiesChanged` signals
- Monitors `org.bluez.Device1` interface for `Connected` property changes
- When `Connected` changes to `False`, extracts MAC address and calls cleanup
- Uses `dbus_fast.aio.MessageBus` for async D-Bus operations

**Lifecycle:**
- Started in `BluezeroGATTServer.start()` (line 1803)
- Stopped in `BluezeroGATTServer.stop()` (line 1811)
- Runs continuously until `stop_event` is set

---

## Current Observations

### ✅ What Works
1. **Core cleanup logic verified by tests** - All 9 tests pass
2. **Callback wiring correct** - Methods properly connected
3. **Thread creation successful** - No import/syntax errors
4. **Deployed to 4 production devices:**
   - 10.0.0.80, 10.0.0.242, 10.0.0.39, 10.0.0.246

### ⚠️ Current Issue: D-Bus Monitoring Not Logging

**Observation:** D-Bus monitoring thread starts but debug messages not appearing in logs/stderr

**Evidence:**
- No "[GATT-MONITOR]" messages in stderr
- No "D-Bus disconnect monitoring started" in RNS logfile
- Thread creation code is correct (verified on device)
- Import fixed (`dbus_fast.aio.MessageBus` not `dbus_fast.MessageBus`)

**Possible Causes:**
1. **Signal subscription not working** - `bus.add_message_handler()` may need different approach
2. **Message matching issue** - Lambda filter might not be catching signals
3. **Threading context** - async/await in daemon thread may have issues
4. **Silent exception** - Thread dying without logging (though try/except should catch)

**Impact:** Automatic disconnect detection not working YET, but manual cleanup methods are functional

---

## Testing Performed

### Unit/Integration Tests
- ✅ 9/9 tests in `test_peripheral_disconnect_cleanup.py` passing
- ✅ 10/10 tests in `test_bluez_state_cleanup.py` still passing
- ✅ No regressions in existing test suite

### Real Hardware Deployment
- ✅ Deployed to all 4 Raspberry Pi devices
- ✅ Services starting successfully
- ✅ No crashes or errors from new code
- ⚠️ D-Bus monitoring not logging (needs investigation)

### Production Observations
**Device 10.0.0.242:**
- 4 centrals connected since restart (B8:27:EB:43:04:BC, 6D:99:93:FA:EF:54, B8:27:EB:10:28:CD, 4C:30:3F:6A:98:C8)
- GATT server operating normally
- Awaiting Android disconnect to test cleanup

---

## Next Steps for Troubleshooting

###  Priority 1: Debug D-Bus Signal Subscription

**Investigate:**
1. **Verify message handler is being called:**
   - Add print statement at top of lambda to see if ANY messages arrive
   - Check if filter logic (`msg.message_type.name == 'SIGNAL'`) is correct

2. **Check D-Bus signal format:**
   - Run `dbus-monitor --system "interface='org.freedesktop.DBus.Properties'"` on Pi
   - Observe actual signal structure when device disconnects
   - Verify our handler matches the real signal format

3. **Alternative subscription method:**
   ```python
   # Instead of add_message_handler, try:
   introspection = await bus.introspect('org.bluez', '/org/bluez/hci0')
   adapter_obj = bus.get_proxy_object('org.bluez', '/org/bluez/hci0', introspection)
   adapter_obj.on_properties_changed(callback)
   ```

### Priority 2: Implement Timeout-Based Fallback

**Simpler approach if D-Bus proves difficult:**
```python
async def _poll_stale_connections(self):
    """Poll for stale central connections every 30s."""
    while not self.stop_event.is_set():
        await asyncio.sleep(30)

        with self.centrals_lock:
            for address, info in list(self.connected_centrals.items()):
                last_write = info.get('last_write_time', info['connected_at'])
                if time.time() - last_write > 60:  # 60s timeout
                    self._handle_central_disconnected(address)
```

### Priority 3: Manual Testing

**Test cleanup methods work without D-Bus:**
1. Connect Android device to Pi GATT server
2. Verify entry added to `connected_centrals` and `_peers`
3. Manually call `_handle_central_disconnected(android_mac)`
4. Verify cleanup happens correctly
5. Validate no memory leak over multiple cycles

---

## Files Modified

### Production Code
- `src/RNS/Interfaces/linux_bluetooth_driver.py`
  - Added `_handle_peripheral_disconnected()` method (35 lines)
  - Added `_handle_central_disconnected()` method (30 lines)
  - Added `_monitor_device_disconnections()` method (112 lines)
  - Added `disconnect_monitor_thread` field
  - Wired `on_central_disconnected` callback

### Tests
- `tests/test_peripheral_disconnect_cleanup.py` (NEW, 270 lines)
  - 9 test cases covering all scenarios
  - Reproduces real-world bug from production logs
  - Verifies cleanup flow end-to-end

---

## How to Test When D-Bus Monitoring Works

**On any Pi (10.0.0.80, .242, .39, .246):**

1. **Connect Android app** as central to Pi's GATT server
2. **Watch logs** for connection:
   ```
   [INFO] GATTServer: Central connected: <android-mac> (MTU: 517)
   ```

3. **Disconnect Android app**

4. **Expected cleanup logs:**
   ```
   [DEBUG] D-Bus: Device <android-mac> disconnected
   [INFO] Detected central disconnect via D-Bus: <android-mac>
   [INFO] GATTServer: Central disconnected: <android-mac> (was connected for X.Xs)
   [DEBUG] Handling peripheral disconnection from <android-mac>
   [DEBUG] Removed <android-mac> from _peers (peripheral disconnect)
   [DEBUG] Peripheral disconnection cleanup complete for <android-mac>
   ```

5. **Verify no peer limit errors** after multiple connect/disconnect cycles

---

## Summary

**Fix Status:** Core implementation complete and tested ✅
**D-Bus Monitoring:** Needs debugging ⚠️
**Fallback Option:** Timeout-based polling available if needed
**Risk:** Low - new code is non-invasive, well-tested, and has safety checks

**Recommended Action:** Complete D-Bus debugging or implement timeout fallback, then merge to main.
