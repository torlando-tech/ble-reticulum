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
health scan (BleakScanner.discover with no service filter). Outcomes:
  * health scan sees >= 1 device -> adapter is ALIVE, just waiting for a
    Reticulum peer; reset the streak, do NOT fire critical (the false-positive
    case at boot);
  * health scan itself raises (D-Bus/adapter error) -> a scan that cannot run
    is direct evidence the adapter is in a fault state; escalate to critical;
  * health scan runs but sees 0 devices (clean zero) -> an empty scan CANNOT
    by itself distinguish a genuinely quiet RF environment (adapter healthy,
    no BLE devices in range) from a dead/wedged adapter. Disambiguate with the
    adapter's OWN BlueZ "Powered" state (_adapter_is_powered):
      - Powered=False (adapter present but not powered) -> genuine fault ->
        fire the "reboot required" critical (this catches both an adapter that
        was never healthy and one that went down);
      - Powered=True (working adapter, quiet room) OR unknown (could not
        determine) -> no positive fault evidence -> warn only, never mandating
        a reboot, so a healthy self-recovering adapter is never torn down over
        a quiet environment (this is the Greptile 3/5 P1 overclaim fix).

**Test strategy**: Drive the REAL LinuxBluetoothDriver._perform_scan() with a
mocked BleakScanner class and a mocked _adapter_is_powered(). The main scan's
detection callback never fires (empty filtered scan) unless fire_callback=True;
the health scan's BleakScanner.discover and the Powered-state result are
controlled per test. No real Bluetooth is touched.

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


def _make_driver(adapter_powered=None):
    """Build a LinuxBluetoothDriver without running the heavy __init__.

    Only the attributes _perform_scan / _adapter_health_check /
    _adapter_is_powered touch are set. Using __new__ avoids the real
    constructor, which requires RNS + BlueZ plumbing that is irrelevant to the
    wedge-detection logic under test.

    adapter_powered controls the mocked _adapter_is_powered() result:
      True  -> adapter is powered (working, e.g. quiet room)
      False -> adapter present but NOT powered (genuine fault)
      None  -> state could not be determined (no positive fault evidence)
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
    d._adapter_is_powered = AsyncMock(return_value=adapter_powered)
    return d


def _make_driver_real_powered():
    """Same as _make_driver but keeps the REAL _adapter_is_powered() (no
    AsyncMock) so tests can drive the actual D-Bus probe with a mocked bus.
    Sets the minimal attributes the probe reads (_log, adapter_path)."""
    from ble_reticulum import linux_bluetooth_driver as m
    d = m.LinuxBluetoothDriver.__new__(m.LinuxBluetoothDriver)
    d._log = Mock()
    d.adapter_path = "/org/bluez/hci0"
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
    async def test_not_powered_adapter_declares_wedge_after_3(self):
        """
        Genuine fault: after 3 clean-zero blind scans, the adapter is present
        on the bus but NOT powered (Powered=False). That is positive fault
        evidence (a working adapter in a quiet room IS powered), so the
        "reboot required" critical must fire. Covers both an adapter that was
        never healthy and one that went down.
        """
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver(adapter_powered=False)  # adapter present but not powered
        with patch.object(m, "BleakScanner", _mock_scanner_class([])):
            for _ in range(3):
                await d._perform_scan()
        # After the 3rd blind scan the critical error must fire.
        d.on_error.assert_called()
        args = d.on_error.call_args[0]
        assert args[0] == "critical"
        assert d.consecutive_empty_scans >= 3
        # The Powered state was actually consulted before escalating.
        d._adapter_is_powered.assert_awaited()

    @pytest.mark.asyncio
    async def test_quiet_room_powered_does_not_reboot(self):
        """
        Greptile P1 overclaim fix (quiet room): a clean zero on a POWERED
        adapter is a working adapter in a room with no BLE devices, NOT a
        fault. Must NOT fire the reboot-required critical; it only warns.
        This holds even after many scans past the threshold.
        """
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver(adapter_powered=True)  # working adapter, quiet room
        with patch.object(m, "BleakScanner", _mock_scanner_class([])):
            for _ in range(5):  # well past the 3-scan threshold
                await d._perform_scan()
        # Streak incremented, but no critical fired (quiet room, not a fault).
        assert d.consecutive_empty_scans >= 3
        d.on_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_powered_state_does_not_reboot(self):
        """
        When the Powered state cannot be determined (None), there is no
        positive fault evidence - fail safe and warn without mandating a
        reboot, so a healthy self-recovering adapter is never torn down.
        """
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver(adapter_powered=None)  # state unknown
        with patch.object(m, "BleakScanner", _mock_scanner_class([])):
            for _ in range(5):
                await d._perform_scan()
        assert d.consecutive_empty_scans >= 3
        d.on_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_scan_resets_streak(self):
        """
        A filtered scan that DOES discover a Reticulum device (detection
        callback fires) proves the adapter is working: it must reset the
        empty-streak and NOT fire any error. This covers the callback-fired
        path in _perform_scan.
        """
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver()
        d.consecutive_empty_scans = 2  # start from a non-zero streak
        BS = _mock_scanner_class([], fire_callback=True)
        with patch.object(m, "BleakScanner", BS):
            await d._perform_scan()
        assert d.consecutive_empty_scans == 0
        d.on_error.assert_not_called()
        # The device was forwarded to the discovered-device callback.
        d.on_device_discovered.assert_called_once()
        # No health scan / powered query needed when the filtered scan works.
        assert BS.discover.await_count == 0
        d._adapter_is_powered.assert_not_awaited()

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
        escalates to critical regardless of the Powered state - fail closed."""
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver(adapter_powered=True)  # even if "powered", scan failing is fault
        BS = _mock_scanner_class([])
        BS.discover = AsyncMock(side_effect=RuntimeError("dbus gone"))
        with patch.object(m, "BleakScanner", BS):
            for _ in range(3):
                await d._perform_scan()
        d.on_error.assert_called()
        assert d.on_error.call_args[0][0] == "critical"
        # The failure short-circuits before consulting the Powered state.
        d._adapter_is_powered.assert_not_awaited()

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


class TestAdapterIsPowered:
    """The real _adapter_is_powered() D-Bus probe: powered / not-powered /
    unknown / no-dbus outcomes, and that the bus connection is always closed."""

    def _patched_bus(self, m, powered_result):
        """Patch m.MessageBus so the driver's _adapter_is_powered() D-Bus
        sequence yields the given powered value (or raises).

        The driver calls: await connect(); await introspect();
        get_proxy_object() [sync]; get_interface() [sync]; await get_powered().
        Returns (bus, adapter_iface) for assertions and the started patch.
        """
        bus = Mock()
        bus.connect = AsyncMock(return_value=bus)
        # `await bus.introspect(...)` -> Mock (truthy, unused beyond existence)
        bus.introspect = AsyncMock(return_value=Mock())
        # `bus.get_proxy_object(...)` [SYNC] -> adapter_obj
        adapter_obj = Mock()
        bus.get_proxy_object = Mock(return_value=adapter_obj)
        # `adapter_obj.get_interface('org.bluez.Adapter1')` [SYNC] -> iface
        adapter_iface = Mock()
        adapter_obj.get_interface = Mock(return_value=adapter_iface)
        # `await adapter_iface.get_powered()` -> powered value (or raises)
        if isinstance(powered_result, Exception):
            adapter_iface.get_powered = AsyncMock(side_effect=powered_result)
        else:
            adapter_iface.get_powered = AsyncMock(return_value=powered_result)

        mb = patch.object(m, "MessageBus")
        mock_bus_class = mb.start()  # start() returns the mock (not the _patch)
        mock_bus_class.return_value = bus
        return bus, adapter_iface, mb

    @pytest.mark.asyncio
    async def test_powered_true(self):
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver_real_powered()
        with patch.object(m, "HAS_DBUS", True):
            bus, adapter_iface, mb = self._patched_bus(m, True)
            try:
                result = await d._adapter_is_powered()
            finally:
                mb.stop()
        assert result is True
        adapter_iface.get_powered.assert_awaited()
        bus.disconnect.assert_called()

    @pytest.mark.asyncio
    async def test_powered_false(self):
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver_real_powered()
        with patch.object(m, "HAS_DBUS", True):
            bus, adapter_iface, mb = self._patched_bus(m, False)
            try:
                result = await d._adapter_is_powered()
            finally:
                mb.stop()
        assert result is False
        bus.disconnect.assert_called()

    @pytest.mark.asyncio
    async def test_query_error_is_unknown(self):
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver_real_powered()
        with patch.object(m, "HAS_DBUS", True):
            bus, _, mb = self._patched_bus(m, RuntimeError("UnknownObject"))
            try:
                result = await d._adapter_is_powered()
            finally:
                mb.stop()
        # A query failure is "unknown", not a positive fault.
        assert result is None
        bus.disconnect.assert_called()

    @pytest.mark.asyncio
    async def test_no_dbus_is_unknown(self):
        from ble_reticulum import linux_bluetooth_driver as m
        d = _make_driver_real_powered()
        with patch.object(m, "HAS_DBUS", False):
            result = await d._adapter_is_powered()
        # No D-Bus at all: unknown, and no bus connection is attempted.
        assert result is None
