"""
Regression test: adapter health-check disambiguation for the scanner-wedge detector.

**Problem**: The discovery scan is SERVICE-FILTERED (only devices advertising the
Reticulum service UUID trigger the detection callback). At boot, no peer has
started advertising yet, so the filtered scan legitimately returns zero callbacks
for several scans while the peer's GATT server comes up. The old detector treated
3 empty filtered scans as "adapter corrupted / system reboot required" and fired
on_error("critical") - a FALSE POSITIVE that tore the BLE interface down right in
the window the peer was about to come online. (Observed on two Pi Zero 2 W: both
wedge at boot, ~3-5 empty filtered scans, then recover on their own - which a
genuinely wedged adapter can never do without a reboot.)

**Fix**: When a service-filtered scan returns zero callbacks, re-check that no
connection is in progress (the pause check at the top of _perform_scan ran
before the main scan; a connection could have started during that window, and
a second scan mid-connection would collide with it). Then run a short UNFILTERED
health scan (BleakScanner.discover with no service filter). Three outcomes:
  * health scan sees >= 1 device -> adapter is ALIVE, just waiting for a
    Reticulum peer; reset the streak, do NOT fire critical (the false-positive
    case at boot);
  * health scan runs but sees 0 devices (clean zero) -> only escalate to the
    "reboot required" critical if the adapter was PREVIOUSLY proven healthy
    (healthy_ever) and has now gone blind. A clean zero in a genuinely quiet
    RF environment (no BLE devices in range at all) is NOT proof the adapter is
    broken - both scans legitimately return nothing, so warn without mandating
    a reboot (this is the Greptile 3/5 P1 overclaim fix);
  * health scan itself raises (D-Bus/adapter error) -> that is direct evidence
    the adapter is in a fault state; escalate regardless of healthy_ever.

**Test strategy**: Drive the REAL LinuxBluetoothDriver._perform_scan() with a
mocked BleakScanner class. The main scan's detection callback never fires
(empty filtered scan); the health scan's BleakScanner.discover is controlled per
test. No real Bluetooth is touched.

The healthy-adapter case is the RED->GREEN assertion: with the OLD code (no
health check) the empty filtered scan would fire on_error("critical") after 3
scans; with the fix it must not.
"""

import pytest
import sys
import os
import asyncio
import threading
from unittest.mock import Mock, AsyncMock, patch

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

# Mock RNS module before importing (the driver has no Reticulum dependency, but
# keep the pattern for environments where ble_reticulum/__init__ or a sibling
# module expects RNS constants). Tolerate RNS being absent (e.g. this box).
try:
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
except ImportError:
    pass

SERVICE_UUID = "37145b00-442d-4a94-917f-8f42c5da28e3"


def _make_driver():
    """Build a LinuxBluetoothDriver without running the heavy __init__.

    Only the attributes _perform_scan / _adapter_health_check touch are set.
    Using __new__ avoids the real constructor, which requires RNS + BlueZ
    plumbing that is irrelevant to the wedge-detection logic under test.
    """
    from ble_reticulum import linux_bluetooth_driver as m
    d = m.LinuxBluetoothDriver.__new__(m.LinuxBluetoothDriver)
    d._running = True
    d.consecutive_empty_scans = 0
    d.healthy_ever = False
    d._log = Mock()
    d.on_error = Mock()
    d.service_uuid = SERVICE_UUID
    d.min_rssi = -60
    # saver => 0.5s main-scan window (keeps tests fast without patching asyncio)
    d.power_mode = "saver"
    d._should_pause_scanning = Mock(return_value=False)
    d.on_device_discovered = Mock()
    return d


def _mock_scanner_class(discover_return, fire_callback=False):
    """Return a replacement for linux_bluetooth_driver.BleakScanner.

    The main discovery scan does `BleakScanner(detection_callback=...,
    service_uuids=...)` then `await scanner.start()/stop()`. By default the
    detection callback is never invoked (empty filtered scan). With
    fire_callback=True, start() invokes the captured detection_callback once
    with a device advertising the Reticulum service UUID, so the scan reports a
    real discovery (callback_count > 0). The health check does
    `await BleakScanner.discover(timeout=...)` which returns discover_return.
    """
    BS = Mock()
    BS.discover = AsyncMock(return_value=discover_return)

    def _factory(*args, **kwargs):
        cb = kwargs.get("detection_callback")
        inst = Mock()

        async def _start(*a, **k):
            if fire_callback and cb is not None:
                device = Mock()
                device.address = "AA:BB:CC:DD:EE:FF"
                device.name = "PeerPi"
                adv = Mock()
                adv.rssi = -50
                adv.service_uuids = [SERVICE_UUID]
                adv.manufacturer_data = {}
                cb(device, adv)

        inst.start = _start
        inst.stop = AsyncMock()
        return inst

    BS.side_effect = _factory
    return BS


class TestAdapterHealthCheckWedgeDetection:
    """Empty service-filtered scan must be disambiguated by an unfiltered scan."""

    @pytest.mark.asyncio
    async def test_empty_filtered_but_healthy_adapter_is_not_a_wedge(self):
        """
        RED before fix / GREEN after fix.

        The filtered scan sees nothing (peer not advertising yet) but the
        unfiltered health scan sees devices -> adapter is alive. Must NOT fire
        on_error("critical") and must reset the empty-streak to 0, even across
        many scans (this is the boot race that used to false-positive).
        """
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver()
        two_devices = [Mock(), Mock()]
        # Bind the mock to a local so we can assert on its call count after the
        # patch context has exited (m.BleakScanner reverts to the real class
        # once the with-block ends, where .discover is a plain function).
        BS = _mock_scanner_class(two_devices)
        with patch.object(m, "BleakScanner", BS):
            for _ in range(5):
                await d._perform_scan()
        d.on_error.assert_not_called()
        assert d.consecutive_empty_scans == 0
        # Health check actually ran on every empty filtered scan.
        assert BS.discover.await_count >= 1

    @pytest.mark.asyncio
    async def test_was_healthy_then_blind_declares_wedge_after_3(self):
        """
        Genuine wedge: adapter was proven healthy, then goes fully blind.

        This is the real corruption case - the adapter USED to see devices
        (healthy_ever=True) and now its unfiltered scan is clean-zero for 3
        scans. Must fire on_error("critical").
        """
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver()
        d.healthy_ever = True  # adapter previously saw devices
        with patch.object(m, "BleakScanner", _mock_scanner_class([])):
            for _ in range(3):
                await d._perform_scan()
        # After the 3rd fully-blind scan the critical error must fire.
        d.on_error.assert_called()
        args = d.on_error.call_args[0]
        assert args[0] == "critical"
        assert d.consecutive_empty_scans >= 3

    @pytest.mark.asyncio
    async def test_quiet_rf_never_healthy_does_not_reboot(self):
        """
        Greptile P1 overclaim fix: a clean zero (unfiltered scan ran but saw
        0 devices) on an adapter that has NEVER demonstrated a working scan is
        NOT proof the adapter is broken - in a genuinely quiet RF environment
        both scans legitimately return nothing. Must NOT fire the reboot-
        required critical; it only warns.
        """
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver()
        # healthy_ever stays False: adapter has never seen a device.
        with patch.object(m, "BleakScanner", _mock_scanner_class([])):
            for _ in range(5):  # well past the 3-scan threshold
                await d._perform_scan()
        # Streak incremented, but no critical fired (quiet-room, not a fault).
        assert d.consecutive_empty_scans >= 3
        d.on_error.assert_not_called()
        assert d.healthy_ever is False

    @pytest.mark.asyncio
    async def test_successful_scan_marks_healthy(self):
        """
        A filtered scan that DOES discover a Reticulum device (detection
        callback fires) proves the adapter is healthy: it must set
        healthy_ever=True, reset the empty-streak, and NOT fire any error.
        This covers the callback-fired path in _perform_scan.
        """
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver()
        d.consecutive_empty_scans = 2  # start from a non-zero streak
        BS = _mock_scanner_class([], fire_callback=True)
        with patch.object(m, "BleakScanner", BS):
            await d._perform_scan()
        assert d.healthy_ever is True
        assert d.consecutive_empty_scans == 0
        d.on_error.assert_not_called()
        # The device was forwarded to the discovered-device callback.
        d.on_device_discovered.assert_called_once()

    @pytest.mark.asyncio
    async def test_blind_then_healthy_resets_streak(self):
        """Two blind scans, then the adapter recovers -> streak resets, no critical."""
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver()
        one_device = [Mock()]
        # Scans 1-2: adapter blind (unfiltered empty). Scan 3: adapter recovers.
        with patch.object(m, "BleakScanner", _mock_scanner_class([])):
            await d._perform_scan()
            await d._perform_scan()
            assert d.consecutive_empty_scans == 2
            d.on_error.assert_not_called()
        with patch.object(m, "BleakScanner", _mock_scanner_class(one_device)):
            await d._perform_scan()
        assert d.consecutive_empty_scans == 0
        d.on_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_health_scan_failure_is_fault_evidence(self):
        """If the health scan itself raises, that is direct evidence the adapter
        is in a fault state (a scan that cannot run is not a quiet room). It
        escalates to critical regardless of healthy_ever - fail closed."""
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver()
        BS = _mock_scanner_class([])
        BS.discover = AsyncMock(side_effect=RuntimeError("dbus gone"))
        with patch.object(m, "BleakScanner", BS):
            for _ in range(3):
                await d._perform_scan()
        d.on_error.assert_called()
        assert d.on_error.call_args[0][0] == "critical"

    @pytest.mark.asyncio
    async def test_connection_started_during_scan_skips_health_scan(self):
        """
        Greptile P1 race fix: a connection that starts during the main-scan
        window must suppress the follow-up health scan. Re-checking
        _should_pause_scanning() before the unfiltered scan prevents a second
        BlueZ scan from colliding with an active connection ("Operation already
        in progress"). Must NOT call the health scan and must NOT fire critical.
        """
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver()
        # Model the real race: the pause check at the TOP of _perform_scan sees
        # "not paused" (False) so the main scan proceeds, but a connection starts
        # DURING the scan window, so the re-check at the health-scan guard (after
        # the main scan) sees "paused" (True). Each scan calls the check twice:
        # top, then health-guard. So the sequence is F,T,F,T,...
        d._should_pause_scanning = Mock(side_effect=[False, True] * 5)
        BS = _mock_scanner_class([])
        with patch.object(m, "BleakScanner", BS):
            for _ in range(5):
                await d._perform_scan()
        # The unfiltered health scan must never have run (guard caught the
        # in-progress connection each cycle).
        assert BS.discover.await_count == 0
        d.on_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_adapter_health_check_returns_count(self):
        """_adapter_health_check returns the number of distinct devices seen."""
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver()
        BS = _mock_scanner_class([Mock(), Mock(), Mock()])
        with patch.object(m, "BleakScanner", BS):
            count = await d._adapter_health_check()
        assert count == 3

        # And 0 when nothing is visible.
        BS0 = _mock_scanner_class([])
        with patch.object(m, "BleakScanner", BS0):
            count0 = await d._adapter_health_check()
        assert count0 == 0
