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

**Fix**: When a service-filtered scan returns zero callbacks, run a short
UNFILTERED health scan (BleakScanner.discover with no service filter). If it sees
ANY device, the adapter is alive and we are simply waiting for a Reticulum peer -
reset the streak, do NOT fire critical. Only a fully blind adapter (unfiltered
scan also empty) counts toward the genuine-wedge streak.

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
    d._log = Mock()
    d.on_error = Mock()
    d.service_uuid = SERVICE_UUID
    d.min_rssi = -60
    # saver => 0.5s main-scan window (keeps tests fast without patching asyncio)
    d.power_mode = "saver"
    d._should_pause_scanning = Mock(return_value=False)
    d.on_device_discovered = Mock()
    return d


def _mock_scanner_class(discover_return):
    """Return a replacement for linux_bluetooth_driver.BleakScanner.

    The main discovery scan does `BleakScanner(detection_callback=...,
    service_uuids=...)` then `await scanner.start()/stop()`. The detection
    callback is never invoked (empty filtered scan). The health check does
    `await BleakScanner.discover(timeout=...)` which returns discover_return.
    """
    BS = Mock()
    inst = BS.return_value
    inst.start = AsyncMock()
    inst.stop = AsyncMock()
    BS.discover = AsyncMock(return_value=discover_return)
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
        with patch.object(m, "BleakScanner", _mock_scanner_class(two_devices)):
            for _ in range(5):
                await d._perform_scan()
        d.on_error.assert_not_called()
        assert d.consecutive_empty_scans == 0
        # Health check actually ran on every empty filtered scan.
        assert m.BleakScanner.discover.await_count >= 1

    @pytest.mark.asyncio
    async def test_fully_blind_adapter_still_declares_wedge_after_3(self):
        """Genuine wedge: unfiltered scan ALSO empty -> critical after 3 scans."""
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver()
        with patch.object(m, "BleakScanner", _mock_scanner_class([])):
            for i in range(3):
                await d._perform_scan()
        # After the 3rd fully-blind scan the critical error must fire.
        d.on_error.assert_called()
        args = d.on_error.call_args[0]
        assert args[0] == "critical"
        assert d.consecutive_empty_scans >= 3

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
    async def test_health_scan_exception_counts_as_blind(self):
        """If the health scan itself raises, the adapter is in a bad state and
        it counts toward the wedge streak (fail closed, not fail open)."""
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
