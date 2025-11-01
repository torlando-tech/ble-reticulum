# MIT License
#
# Copyright (c) 2025 Reticulum BLE Interface Contributors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
BLEInterface - Bluetooth Low Energy interface for Reticulum

This interface enables Reticulum mesh networking over BLE on Linux devices
without additional hardware.

Key features:
- Auto-discovery of BLE peers
- Multi-peer mesh support (up to 7 simultaneous connections)
- Packet fragmentation for BLE MTU limits
- Power management modes for battery efficiency
- Linux-only (requires BlueZ 5.x for GATT server)
"""

import RNS
import sys
import os
import threading
import time
import asyncio
from collections import deque

# Add interface directory to path for importing other BLE modules
# This is needed when loaded as external interface
try:
    # __file__ exists when imported normally
    _interface_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # __file__ doesn't exist when loaded via exec() by Reticulum
    # Try to get the config directory from RNS
    _interface_dir = None
    try:
        import RNS
        if hasattr(RNS.Reticulum, 'configdir') and RNS.Reticulum.configdir:
            _interface_dir = os.path.join(RNS.Reticulum.configdir, "interfaces")
    except (ImportError, AttributeError):
        pass

    # Fall back to default if we couldn't get it from RNS
    if _interface_dir is None:
        _interface_dir = os.path.expanduser("~/.reticulum/interfaces")

if _interface_dir not in sys.path:
    sys.path.insert(0, _interface_dir)

# Import base Interface class
# When integrated into Reticulum, this will be:
# from RNS.Interfaces.Interface import Interface
# For now, we'll need to handle the import path
try:
    from RNS.Interfaces.Interface import Interface
except ImportError:
    # Fallback for development
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../'))
    from RNS.Interfaces.Interface import Interface

# Import fragmentation module
# Note: When loaded as external interface, use absolute imports
try:
    from BLEFragmentation import BLEFragmenter, BLEReassembler
except ImportError:
    # Fallback for when loaded as part of RNS package
    from RNS.Interfaces.BLEFragmentation import BLEFragmenter, BLEReassembler

# Import GATT server for peripheral mode
try:
    from BLEGATTServer import BLEGATTServer
    HAS_GATT_SERVER = True
except ImportError:
    try:
        from RNS.Interfaces.BLEGATTServer import BLEGATTServer
        HAS_GATT_SERVER = True
    except ImportError:
        HAS_GATT_SERVER = False

# Check for bleak dependency
try:
    import bleak
    from bleak import BleakScanner, BleakClient
    HAS_BLEAK = True
except ImportError:
    HAS_BLEAK = False

# ============================================================================
# Monkey patch for Bleak 1.1.1 BlueZ ServicesResolved race condition
# ============================================================================
# Issue: When connecting to BlueZ-based GATT servers (like bluezero), BlueZ
#        sets ServicesResolved=True BEFORE services are fully exported to D-Bus
# Cause: BlueZ GATT database cache timing issue (bluez/bluez#1489)
# Impact: Bleak attempts to enumerate services before they're available,
#         causing -5 (EIO) error and immediate disconnect
# Fix: Poll D-Bus service map to verify services actually exist before proceeding
# Status: Works with bluezero; proper fix should be in BlueZ or Bleak upstream
# GitHub: https://github.com/hbldh/bleak/issues/1677
# ============================================================================
if HAS_BLEAK:
    try:
        from bleak.backends.bluezdbus.manager import BlueZManager

        # Store original method
        _original_wait_for_services_discovery = BlueZManager._wait_for_services_discovery

        async def _patched_wait_for_services_discovery(self, device_path: str) -> None:
            """
            Patched version that waits for services to actually appear in D-Bus.

            Fixes race condition where ServicesResolved=True before services
            are fully exported to D-Bus (common when connecting to BlueZ peripherals).
            """
            # Call original wait for ServicesResolved property
            await _original_wait_for_services_discovery(self, device_path)

            # Additional verification: Poll until services actually appear in D-Bus
            max_attempts = 20  # 20 attempts * 100ms = 2 seconds max
            retry_delay = 0.1  # 100ms between attempts

            for attempt in range(max_attempts):
                # Check if services are actually present in the service map
                service_paths = self._service_map.get(device_path, set())

                if service_paths and len(service_paths) > 0:
                    # Services found! Verify at least one service has been fully loaded
                    # by checking if it exists in the properties dictionary
                    try:
                        first_service_path = next(iter(service_paths))
                        if first_service_path in self._properties:
                            # Success: Services are actually in D-Bus
                            RNS.log(f"BLE BlueZ timing fix: Services verified in D-Bus after {attempt * retry_delay:.2f}s", RNS.LOG_DEBUG)
                            return
                    except (StopIteration, KeyError):
                        pass  # Service not ready yet

                # Services not ready yet, wait before next check
                if attempt < max_attempts - 1:  # Don't sleep on last attempt
                    await asyncio.sleep(retry_delay)

            # If we get here, services didn't appear within timeout
            # Log warning but don't raise - let get_services() handle it
            RNS.log(f"BLE BlueZ timing fix: Services not found in D-Bus after {max_attempts * retry_delay}s, proceeding anyway", RNS.LOG_WARNING)

        # Apply the patch
        BlueZManager._wait_for_services_discovery = _patched_wait_for_services_discovery

        RNS.log("Applied Bleak 1.1.1 BlueZ ServicesResolved timing patch for bluezero compatibility", RNS.LOG_INFO)

    except Exception as e:
        # If patching fails, log warning but don't prevent interface from loading
        RNS.log(f"Failed to apply Bleak BlueZ timing patch: {e}. Connections to bluezero peripherals may fail.", RNS.LOG_WARNING)


class DiscoveredPeer:
    """
    Tracks information about a discovered BLE peer for connection prioritization.

    This class stores signal strength (RSSI), connection history, and timing
    information to enable smart peer selection in mesh networks.

    Algorithm Design Decisions:
    ---------------------------
    1. RSSI Tracking: Signal strength is the primary indicator of connection
       quality in BLE networks. We track and update RSSI on every discovery
       to adapt to changing environmental conditions (movement, obstacles).

    2. Connection History: Past behavior is a strong predictor of future
       reliability. We track attempts vs successes to identify consistently
       reachable peers vs flaky ones.

    3. Temporal Data: Both first_seen and last_seen timestamps enable:
       - Recency-based prioritization (prefer active peers)
       - Stale peer cleanup (remove disappeared peers)
       - Connection attempt rate limiting

    4. Separation of Concerns: We track successful_connections separately
       from failed_connections to enable nuanced scoring (e.g., a peer with
       80% success from 100 attempts is more reliable than one with 100%
       from 2 attempts).
    """

    def __init__(self, address, name, rssi):
        """
        Initialize a discovered peer.

        Args:
            address: BLE MAC address of the peer
            name: Advertised device name
            rssi: Signal strength in dBm (typically -30 to -100)
        """
        self.address = address
        self.name = name
        self.rssi = rssi
        self.first_seen = time.time()
        self.last_seen = time.time()

        # Connection tracking
        self.connection_attempts = 0
        self.successful_connections = 0
        self.failed_connections = 0
        self.last_connection_attempt = 0

    def update_rssi(self, rssi):
        """Update RSSI and last seen timestamp."""
        self.rssi = rssi
        self.last_seen = time.time()

    def record_connection_attempt(self):
        """Record that a connection attempt is being made."""
        self.connection_attempts += 1
        self.last_connection_attempt = time.time()

    def record_connection_success(self):
        """Record a successful connection."""
        self.successful_connections += 1

    def record_connection_failure(self):
        """Record a failed connection."""
        self.failed_connections += 1

    def get_success_rate(self):
        """
        Get the connection success rate.

        Returns:
            float: Success rate from 0.0 to 1.0, or 0.0 if no attempts
        """
        if self.connection_attempts == 0:
            return 0.0
        return self.successful_connections / self.connection_attempts

    def __repr__(self):
        return (f"DiscoveredPeer({self.address}, {self.name}, "
                f"RSSI={self.rssi}, attempts={self.connection_attempts}, "
                f"success_rate={self.get_success_rate():.2f})")


class BLEInterface(Interface):
    """
    BLE interface for Reticulum networking.

    Implements the Reticulum Interface API for Bluetooth Low Energy
    transport, enabling mesh networking over BLE connections.

    ARCHITECTURE:
    - Dual-mode: Acts as both central (client) and peripheral (server)
    - Spawns BLEPeerInterface for each connected peer
    - Fragments packets larger than BLE MTU (~185 bytes)
    - Auto-reconnects on connection loss

    THREADING MODEL:
    - Main asyncio loop in separate thread (_run_async_loop)
    - LOCK ORDERING CONVENTION (to prevent deadlocks):
      1. peer_lock - ALWAYS acquire first for peer state access
      2. frag_lock - THEN acquire for fragmentation state
      NEVER acquire locks in reverse order! (HIGH #2: deadlock prevention)
    - Uses asyncio.run_coroutine_threadsafe for cross-thread calls

    MEMORY USAGE (per-peer overhead):
    - Fragmenter + Reassembler: ~400 bytes per peer
    - Max peers: configurable (default 7)
    - Reassembly buffers: Auto-cleanup after 30s timeout (CRITICAL #2)
    - Discovery cache: ~100 bytes per discovered device (limited to 100)

    ERROR RECOVERY:
    - Connection failure: Exponential backoff + blacklist
    - Transmission timeout: Packet dropped (Reticulum retransmits)
    - Fragmentation failure: Buffer cleanup after timeout
    - Adapter error: Interface marked offline, Transport handles
    """

    # Interface constants
    HW_MTU = 500  # Reticulum standard MTU
    BITRATE_GUESS = 700_000  # ~700 Kbps average BLE throughput
    DEFAULT_IFAC_SIZE = 16

    # BLE-specific constants
    SERVICE_UUID = "37145b00-442d-4a94-917f-8f42c5da28e3"  # Custom Reticulum BLE service
    CHARACTERISTIC_RX_UUID = "37145b00-442d-4a94-917f-8f42c5da28e5"  # RX characteristic
    CHARACTERISTIC_TX_UUID = "37145b00-442d-4a94-917f-8f42c5da28e4"  # TX characteristic
    CHARACTERISTIC_IDENTITY_UUID = "37145b00-442d-4a94-917f-8f42c5da28e6"  # Identity characteristic (Protocol v2)

    # Discovery and connection settings
    DISCOVERY_INTERVAL = 5.0  # seconds between discovery scans
    CONNECTION_TIMEOUT = 30.0  # seconds before connection times out
    MAX_PEERS = 7  # Maximum simultaneous BLE connections (conservative default)
    MIN_RSSI = -85  # Minimum signal strength (dBm) - more permissive for better peer discovery

    # Power management modes
    POWER_MODE_AGGRESSIVE = "aggressive"  # Continuous scanning
    POWER_MODE_BALANCED = "balanced"  # Intermittent scanning (default)
    POWER_MODE_SAVER = "saver"  # Minimal scanning

    # Fragmentation constants
    FRAG_TYPE_START = 0x01
    FRAG_TYPE_CONTINUE = 0x02
    FRAG_TYPE_END = 0x03
    FRAG_HEADER_SIZE = 5  # bytes: type(1) + sequence(2) + total(2)

    def __init__(self, owner, configuration):
        """
        Initialize BLE interface.

        Args:
            owner: The Reticulum.Transport instance that owns this interface
            configuration: Dictionary or ConfigObj with interface settings
        """
        # Check dependencies
        if not HAS_BLEAK:
            raise ImportError(
                "BLEInterface requires the 'bleak' library. "
                "Install with: pip install bleak==1.1.1"
            )

        super().__init__()

        # Parse configuration
        c = Interface.get_config_obj(configuration)

        # Basic interface setup
        self.IN = True
        self.OUT = True  # Enable bidirectional communication
        self.name = c.get("name", "BLEInterface")
        self.owner = owner
        self.online = False
        self.bitrate = BLEInterface.BITRATE_GUESS
        self.mode = Interface.MODE_FULL  # Full mode: enable announce propagation, meshing, transport

        # BLE configuration
        self.service_uuid = c.get("service_uuid", BLEInterface.SERVICE_UUID)
        # Device name will be set to identity-based name after Transport.identity is available
        # Format: RNS-{identity_hash} where identity_hash is first 16 hex chars of Transport.identity
        # This enables reliable discovery even when bluezero doesn't expose service UUIDs to Bleak
        self.device_name = c.get("device_name", None)  # Will be auto-generated from identity if None
        self.discovery_interval = float(c.get("discovery_interval", BLEInterface.DISCOVERY_INTERVAL))
        self.max_peers = int(c.get("max_connections", BLEInterface.MAX_PEERS))
        self.min_rssi = int(c.get("min_rssi", BLEInterface.MIN_RSSI))
        self.connection_timeout = float(c.get("connection_timeout", BLEInterface.CONNECTION_TIMEOUT))

        # Service discovery delay (for bluezero D-Bus registration timing)
        # bluezero registers characteristics asynchronously with BlueZ D-Bus
        # A small delay after connection allows registration to complete before discovery
        self.service_discovery_delay = float(c.get("service_discovery_delay", 1.5))  # Default 1.5s

        # Power management
        self.power_mode = c.get("power_mode", BLEInterface.POWER_MODE_BALANCED)
        if self.power_mode not in [BLEInterface.POWER_MODE_AGGRESSIVE,
                                     BLEInterface.POWER_MODE_BALANCED,
                                     BLEInterface.POWER_MODE_SAVER]:
            RNS.log(f"{self} Invalid power mode '{self.power_mode}', using balanced", RNS.LOG_WARNING)
            self.power_mode = BLEInterface.POWER_MODE_BALANCED

        # Central mode (scanning and connecting) configuration
        enable_central_val = c.get("enable_central", True)
        # Convert string "yes"/"no" to boolean
        if isinstance(enable_central_val, str):
            self.enable_central = enable_central_val.lower() in ["yes", "true", "1"]
        else:
            self.enable_central = bool(enable_central_val)

        # Peripheral mode (GATT server) configuration
        enable_peripheral_val = c.get("enable_peripheral", True)
        # Convert string "yes"/"no" to boolean
        if isinstance(enable_peripheral_val, str):
            self.enable_peripheral = enable_peripheral_val.lower() in ["yes", "true", "1"]
        else:
            self.enable_peripheral = bool(enable_peripheral_val)
        if self.enable_peripheral and not HAS_GATT_SERVER:
            RNS.log(f"{self} Peripheral mode requested but BLEGATTServer not available", RNS.LOG_WARNING)
            self.enable_peripheral = False

        # Local announce forwarding workaround
        # WORKAROUND: Reticulum Transport.py doesn't forward locally-originated announces (hops=0)
        # to physical interfaces. This option enables manual forwarding of local announces to BLE peers.
        # See: Transport.py lines 987-1069 (locally originated announces skip forwarding block)
        # Default: False (disabled, assume Transport behavior is intentional)
        enable_local_announce_val = c.get("enable_local_announce_forwarding", False)
        if isinstance(enable_local_announce_val, str):
            self.enable_local_announce_forwarding = enable_local_announce_val.lower() in ["yes", "true", "1"]
        else:
            self.enable_local_announce_forwarding = bool(enable_local_announce_val)

        # State tracking
        self.peers = {}  # address -> (client, last_seen, mtu)
        self.peer_lock = threading.Lock()

        # NEW: Identity-based interface tracking (unified dual-connection architecture)
        self.spawned_interfaces = {}  # identity_hash -> BLEPeerInterface (unified interface per peer)
                                       # OLD format (legacy): "AA:BB:CC:DD:EE:FF-central" or "AA:BB:CC:DD:EE:FF-peripheral"
                                       # NEW format: identity_hash (first 16 hex chars of full hash)
        self.address_to_identity = {}  # address -> peer_identity (16-byte identity)
        self.identity_to_address = {}  # identity_hash -> address (for reverse lookup)

        # GATT server for peripheral mode
        self.gatt_server = None
        if self.enable_peripheral:
            try:
                self.gatt_server = BLEGATTServer(self, device_name=self.device_name)
                # Set up callbacks for server events
                self.gatt_server.on_data_received = self.handle_peripheral_data
                self.gatt_server.on_central_connected = self.handle_central_connected
                self.gatt_server.on_central_disconnected = self.handle_central_disconnected
                RNS.log(f"{self} GATT server initialized for peripheral mode", RNS.LOG_DEBUG)
                RNS.log(f"{self} registered peripheral callbacks: on_data_received={self.handle_peripheral_data.__name__}, on_central_connected={self.handle_central_connected.__name__}", RNS.LOG_DEBUG)
            except Exception as e:
                RNS.log(f"{self} Failed to initialize GATT server: {e}", RNS.LOG_ERROR)
                self.gatt_server = None
                self.enable_peripheral = False

        # Fragmentation
        self.fragmenters = {}  # address -> BLEFragmenter (per MTU)
        self.reassemblers = {}  # address -> BLEReassembler
        self.frag_lock = threading.Lock()

        # Async event loop (will be created in separate thread)
        self.loop = None
        self.loop_thread = None

        # Discovery state with prioritization
        self.discovered_peers = {}  # address -> DiscoveredPeer
        self.connection_blacklist = {}  # address -> (blacklist_until_timestamp, failure_count)
        self.scanning = False

        # HIGH #4: Limit discovered peers to prevent unbounded memory growth
        self.max_discovered_peers = int(c.get("max_discovered_peers", 100))  # Reasonable limit for discovery cache

        # Connection prioritization configuration
        self.connection_rotation_interval = float(c.get("connection_rotation_interval", 600))  # 10 minutes
        self.connection_retry_backoff = float(c.get("connection_retry_backoff", 60))  # 1 minute
        self.max_connection_failures = int(c.get("max_connection_failures", 3))  # blacklist threshold

        # Local adapter address (will be populated on first scan)
        self.local_address = None

        # BlueZ version and capabilities (for LE-specific connection support)
        self.bluez_version = self._detect_bluez_version()
        self.has_connect_device = False  # Set to True if ConnectDevice() available

        RNS.log(f"{self} initializing with service UUID {self.service_uuid}", RNS.LOG_INFO)
        RNS.log(f"{self} power mode: {self.power_mode}, max peers: {self.max_peers}", RNS.LOG_DEBUG)
        RNS.log(f"{self} central mode: {'ENABLED' if self.enable_central else 'DISABLED'}", RNS.LOG_INFO)
        RNS.log(f"{self} peripheral mode: {'ENABLED' if self.enable_peripheral else 'DISABLED'}", RNS.LOG_INFO)

        # Local announce forwarding status log
        if self.enable_local_announce_forwarding:
            RNS.log(f"{self} local packet forwarding ENABLED (workaround for Transport hops=0 bug)", RNS.LOG_INFO)
        else:
            RNS.log(f"{self} local packet forwarding DISABLED (relies on Transport for propagation)", RNS.LOG_DEBUG)

        # Start the interface
        self.start()

    def start(self):
        """Start the BLE interface operations."""
        RNS.log(f"{self} starting BLE operations", RNS.LOG_INFO)

        # Create and start async event loop in separate thread
        self.loop_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self.loop_thread.start()

        # Wait for loop to initialize
        max_wait = 5
        waited = 0
        while self.loop is None and waited < max_wait:
            time.sleep(0.1)
            waited += 0.1

        if self.loop is None:
            RNS.log(f"{self} failed to start async event loop", RNS.LOG_ERROR)
            return

        # Schedule discovery to start (if central mode enabled)
        if self.enable_central:
            asyncio.run_coroutine_threadsafe(self._start_discovery(), self.loop)
        else:
            RNS.log(f"{self} central mode disabled, skipping peer discovery", RNS.LOG_INFO)

        # Start periodic cleanup task (CRITICAL #2: prevent unbounded reassembly buffer growth)
        asyncio.run_coroutine_threadsafe(self._periodic_cleanup(), self.loop)

        # Bug #13 workaround: Clear stale BLE paths from Transport.path_table
        # Reticulum core bug: Paths loaded from storage may have timestamp=0,
        # causing immediate expiration and message delivery failures.
        # This workaround removes stale BLE paths on interface startup.
        # TODO: Remove when upstream Transport.py is fixed (see session notes)
        self._clear_stale_ble_paths()

        # Set interface online
        self.online = True
        RNS.log(f"{self} interface online", RNS.LOG_INFO)

    def final_init(self):
        """
        Interface lifecycle hook called AFTER interface is added to Transport.interfaces
        but BEFORE Transport.start() loads Transport.identity.

        Use this to start a background thread that waits for Transport.identity to be
        loaded, then starts the GATT server with a valid identity value.
        """
        if self.gatt_server:
            RNS.log(f"{self} Launching GATT server startup thread (will wait for Transport.identity)", RNS.LOG_DEBUG)
            server_thread = threading.Thread(target=self._start_gatt_when_identity_ready, daemon=True, name="BLE-GATT-Startup")
            server_thread.start()

    def _start_gatt_when_identity_ready(self):
        """
        Background thread that waits for Transport.identity, sets it on GATT server,
        then starts the server. No timeout - identity loading is guaranteed.
        """
        import RNS.Transport as Transport

        attempt = 0
        start_time = time.time()

        RNS.log(f"{self} Waiting for Transport.identity to be loaded...", RNS.LOG_DEBUG)

        # Poll until Transport.identity is available (no timeout - it WILL load)
        while True:
            attempt += 1

            try:
                if hasattr(Transport, 'identity') and Transport.identity:
                    identity_hash = Transport.identity.hash
                    if identity_hash and len(identity_hash) == 16:
                        elapsed = time.time() - start_time
                        RNS.log(f"{self} ✓ Transport.identity available after {elapsed:.1f}s", RNS.LOG_INFO)

                        # Generate identity-based device name if not configured
                        # Protocol v2.1: Encode full identity.hash (16 bytes) in BLE device name for reliable discovery
                        # This bypasses bluezero service_uuid exposure bug (service_uuids=[] in Bleak scans)
                        # Format: RNS-{32-hex-chars} = RNS-{16-byte-identity-hex} (36 chars, fits in 248-byte BLE name limit)
                        if self.device_name is None:
                            identity_str = identity_hash.hex()  # Full 16 bytes as 32 hex chars
                            self.device_name = f"RNS-{identity_str}"
                            RNS.log(f"{self} Auto-generated identity-based device name: {self.device_name}", RNS.LOG_INFO)
                        else:
                            RNS.log(f"{self} Using configured device name: {self.device_name}", RNS.LOG_INFO)

                        # Set identity on GATT server
                        self.gatt_server.set_transport_identity(identity_hash)
                        RNS.log(f"{self} Transport.identity set on GATT server: {identity_hash.hex()}", RNS.LOG_INFO)

                        # Update GATT server's device_name to use identity-based name
                        self.gatt_server.device_name = self.device_name
                        RNS.log(f"{self} GATT server will advertise as: {self.device_name}", RNS.LOG_INFO)

                        # Start GATT server with valid identity
                        RNS.log(f"{self} Starting GATT server with Protocol v2.1 (identity-based naming)...", RNS.LOG_INFO)
                        asyncio.run_coroutine_threadsafe(self._start_server(), self.loop)
                        return
            except Exception as e:
                if attempt == 1:
                    RNS.log(f"{self} Error checking Transport.identity: {e}", RNS.LOG_DEBUG)

            # Log progress every 50 attempts (~5 seconds)
            if attempt % 50 == 0:
                RNS.log(f"{self} Still waiting for Transport.identity... ({attempt} attempts, {time.time() - start_time:.1f}s)", RNS.LOG_DEBUG)

            time.sleep(0.1)  # Poll every 100ms

    def _run_async_loop(self):
        """Run the asyncio event loop in a separate thread."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _clear_stale_ble_paths(self):
        """
        Clear stale BLE paths from Transport.path_table on interface startup.

        Bug #13 workaround: Reticulum core loads path table entries from storage
        with timestamp=0 (or very old timestamps), causing paths to immediately
        expire. This prevents LXMF message delivery as messages wait for paths
        that are constantly expiring and being recreated.

        This workaround clears any BLE paths with invalid timestamps on startup,
        forcing fresh path discovery via announces.

        TODO: Remove this workaround when Reticulum core is fixed to refresh
        timestamps when loading paths from storage (Transport.py:252).
        """
        try:
            import RNS.Transport as Transport

            if not hasattr(Transport, 'path_table') or not Transport.path_table:
                return

            current_time = time.time()
            stale_threshold = 60  # Paths older than 60 seconds are considered stale
            stale_paths = []

            # Scan for stale BLE paths
            for dest_hash, entry in list(Transport.path_table.items()):
                try:
                    timestamp = entry[0]  # IDX_PT_TIMESTAMP
                    receiving_interface = entry[5]  # IDX_PT_RVCD_IF

                    # Check if this is a BLE path
                    if receiving_interface and "BLE" in str(type(receiving_interface).__name__):
                        # Check for timestamp=0 bug or very old timestamps
                        if timestamp == 0:
                            stale_paths.append((dest_hash, timestamp, "timestamp=0 (Unix epoch bug)"))
                        elif (current_time - timestamp) > stale_threshold:
                            stale_paths.append((dest_hash, timestamp, f"age={(current_time - timestamp):.0f}s (stale from previous session)"))
                except (IndexError, TypeError) as e:
                    # Malformed path entry
                    RNS.log(f"{self} Skipping malformed path table entry: {e}", RNS.LOG_DEBUG)
                    continue

            # Remove stale paths
            if stale_paths:
                RNS.log(f"{self} Bug #13 workaround: Found {len(stale_paths)} stale BLE path(s) to clear", RNS.LOG_INFO)
                for dest_hash, old_timestamp, reason in stale_paths:
                    Transport.path_table.pop(dest_hash)
                    RNS.log(f"{self} Cleared stale BLE path for {RNS.prettyhexrep(dest_hash)} - {reason}", RNS.LOG_DEBUG)
                RNS.log(f"{self} Stale path cleanup complete. Fresh paths will be discovered via announces.", RNS.LOG_INFO)
            else:
                RNS.log(f"{self} No stale BLE paths found in path table", RNS.LOG_DEBUG)

        except Exception as e:
            RNS.log(f"{self} Error during stale path cleanup (non-fatal): {e}", RNS.LOG_WARNING)

    def _detect_bluez_version(self):
        """
        Detect BlueZ version from bluetoothctl command.

        Returns:
            tuple: Version tuple like (5, 84) or None if detection fails
        """
        try:
            import subprocess
            result = subprocess.run(
                ['bluetoothctl', '--version'],
                capture_output=True,
                text=True,
                timeout=5
            )
            version_str = result.stdout.strip().split()[-1]
            version_tuple = tuple(map(int, version_str.split('.')))
            RNS.log(f"{self} detected BlueZ version {version_str}", RNS.LOG_DEBUG)

            # Also log BlueZ configuration for pairing
            self._log_bluez_config()

            return version_tuple
        except Exception as e:
            RNS.log(f"{self} could not detect BlueZ version: {e}", RNS.LOG_DEBUG)
            return None

    def _log_bluez_config(self):
        """Log relevant BlueZ configuration settings for BLE mesh networking."""
        try:
            with open('/etc/bluetooth/main.conf', 'r') as f:
                config_content = f.read()

            # Extract JustWorksRepairing setting
            just_works = None
            for line in config_content.split('\n'):
                line = line.strip()
                if line.startswith('JustWorksRepairing'):
                    just_works = line.split('=')[1].strip()
                    break

            if just_works == 'always':
                RNS.log(f"{self} BlueZ JustWorksRepairing: always (automatic pairing enabled for mesh)", RNS.LOG_INFO)
            elif just_works == 'never' or just_works is None:
                RNS.log(f"{self} BlueZ JustWorksRepairing: never (default - may cause pairing failures)", RNS.LOG_WARNING)
                RNS.log(f"{self} Recommendation: Set JustWorksRepairing=always in /etc/bluetooth/main.conf for automatic mesh pairing", RNS.LOG_WARNING)
            else:
                RNS.log(f"{self} BlueZ JustWorksRepairing: {just_works}", RNS.LOG_DEBUG)

        except FileNotFoundError:
            RNS.log(f"{self} Could not read /etc/bluetooth/main.conf (not on Linux/BlueZ)", RNS.LOG_DEBUG)
        except Exception as e:
            RNS.log(f"{self} Could not read BlueZ config: {e}", RNS.LOG_DEBUG)

    async def _connect_via_dbus_le(self, peer_address):
        """
        Connect to peer using D-Bus Adapter.ConnectDevice() with explicit LE type.

        This method forces an LE (BLE) connection instead of BR/EDR, bypassing
        BlueZ's default preference for BR/EDR on dual-mode devices.

        Requirements:
        - BlueZ >= 5.49 (when ConnectDevice was introduced)
        - bluetoothd running with -E flag (experimental mode)

        Args:
            peer_address: BLE MAC address to connect to

        Returns:
            bool: True if ConnectDevice succeeded

        Raises:
            AttributeError: If ConnectDevice method not available
            PermissionError: If experimental mode not enabled
        """
        from dbus_fast.aio import MessageBus
        from dbus_fast import BusType, Variant

        RNS.log(f"{self} attempting LE-specific connection via ConnectDevice()", RNS.LOG_DEBUG)

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        # Get adapter interface
        introspection = await bus.introspect('org.bluez', '/org/bluez/hci0')
        adapter_obj = bus.get_proxy_object('org.bluez', '/org/bluez/hci0', introspection)
        adapter_iface = adapter_obj.get_interface('org.bluez.Adapter1')

        # Call ConnectDevice with LE parameters
        # This explicitly specifies LE connection type
        params = {
            "Address": Variant("s", peer_address),
            "AddressType": Variant("s", "public")  # Force LE public address type
        }

        # Call the experimental method
        result = await adapter_iface.call_connect_device(params)

        RNS.log(f"{self} ConnectDevice() succeeded for {peer_address}", RNS.LOG_DEBUG)
        self.has_connect_device = True  # Mark as available for future use
        return True

    async def _get_local_adapter_address(self):
        """
        Get local Bluetooth adapter address reliably across platforms.

        This function tries multiple methods to retrieve the adapter address:
        1. Platform-specific scanner attribute (if available)
        2. BlueZ D-Bus interface (Linux/BlueZ)

        Returns:
            str: Local BLE adapter MAC address, or None if unavailable
        """
        # Try BlueZ D-Bus approach for Linux
        try:
            from bleak.backends.bluezdbus import defs
            from dbus_fast.aio import MessageBus
            from dbus_fast import BusType

            RNS.log(f"{self} attempting to get local adapter address via D-Bus", RNS.LOG_DEBUG)

            # Connect to system bus
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

            # Try hci0 first (most common)
            try:
                introspection = await bus.introspect('org.bluez', '/org/bluez/hci0')
                obj = bus.get_proxy_object('org.bluez', '/org/bluez/hci0', introspection)
                adapter = obj.get_interface(defs.ADAPTER_INTERFACE)
                properties_interface = obj.get_interface('org.freedesktop.DBus.Properties')
                address = await properties_interface.call_get(defs.ADAPTER_INTERFACE, 'Address')

                # Extract value from Variant object
                if hasattr(address, 'value'):
                    address = address.value

                RNS.log(f"{self} local adapter address retrieved via D-Bus: {address}", RNS.LOG_INFO)
                return address
            except Exception as e:
                RNS.log(f"{self} could not get address from hci0: {e}, trying to enumerate adapters", RNS.LOG_DEBUG)

                # If hci0 fails, enumerate all adapters
                introspection = await bus.introspect('org.bluez', '/')
                obj = bus.get_proxy_object('org.bluez', '/', introspection)
                object_manager = obj.get_interface('org.freedesktop.DBus.ObjectManager')
                objects = await object_manager.call_get_managed_objects()

                for path, interfaces in objects.items():
                    if defs.ADAPTER_INTERFACE in interfaces:
                        adapter_props = interfaces[defs.ADAPTER_INTERFACE]
                        if 'Address' in adapter_props:
                            address = adapter_props['Address']
                            # Extract value from Variant object
                            if hasattr(address, 'value'):
                                address = address.value
                            RNS.log(f"{self} local adapter address retrieved via D-Bus (path {path}): {address}", RNS.LOG_INFO)
                            return address

                RNS.log(f"{self} no adapters found via D-Bus enumeration", RNS.LOG_WARNING)
        except ImportError:
            RNS.log(f"{self} D-Bus not available (not on Linux/BlueZ)", RNS.LOG_DEBUG)
        except Exception as e:
            RNS.log(f"{self} D-Bus adapter address retrieval failed: {type(e).__name__}: {e}", RNS.LOG_DEBUG)

        RNS.log(f"{self} could not get local adapter address, MAC-based connection direction preference disabled", RNS.LOG_WARNING)
        return None

    async def _start_discovery(self):
        """Start BLE discovery process."""
        RNS.log(f"{self} starting peer discovery", RNS.LOG_DEBUG)

        # Get local adapter address before first scan (for MAC-based connection direction preference)
        if self.local_address is None:
            self.local_address = await self._get_local_adapter_address()
            if self.local_address:
                RNS.log(f"{self} connection direction preference enabled (local MAC: {self.local_address})", RNS.LOG_INFO)
            else:
                RNS.log(f"{self} connection direction preference disabled (could not get local MAC)", RNS.LOG_WARNING)

        while self.online:
            try:
                # Saver mode: Skip scanning when we have connected peers
                # This dramatically reduces CPU usage on low-power devices (Pi Zero)
                skip_scan = False
                if self.power_mode == BLEInterface.POWER_MODE_SAVER:
                    with self.peer_lock:
                        connected_count = len(self.peers)

                    # If we have any connected peers, skip scanning
                    if connected_count > 0:
                        skip_scan = True
                        RNS.log(f"{self} saver mode: skipping scan ({connected_count} connected peer(s))", RNS.LOG_DEBUG)

                if not skip_scan:
                    await self._discover_peers()

                # Calculate sleep time based on power mode
                if self.power_mode == BLEInterface.POWER_MODE_AGGRESSIVE:
                    sleep_time = 1.0  # Fast discovery
                elif self.power_mode == BLEInterface.POWER_MODE_SAVER:
                    # Long sleep in saver mode, even longer if we skipped scan
                    sleep_time = 60.0 if skip_scan else 30.0
                else:  # BALANCED
                    sleep_time = self.discovery_interval  # Default 5.0s

                await asyncio.sleep(sleep_time)

            except Exception as e:
                RNS.log(f"{self} error in discovery loop: {e}", RNS.LOG_ERROR)
                await asyncio.sleep(5)  # Back off on errors

    async def _start_server(self):
        """
        Start GATT server for peripheral mode (non-blocking).

        This method launches the server startup in the background and doesn't block
        the interface initialization. If the server fails to start, the interface
        continues in central-only mode.
        """
        if not self.gatt_server:
            return

        RNS.log(f"{self} starting GATT server in background", RNS.LOG_INFO)

        # Start server in background with timeout
        async def start_with_timeout():
            try:
                # Give server 10 seconds to start
                await asyncio.wait_for(self.gatt_server.start(), timeout=10.0)
                RNS.log(f"{self} GATT server started and advertising", RNS.LOG_INFO)
            except asyncio.TimeoutError:
                RNS.log(f"{self} GATT server startup timed out after 10s, disabling peripheral mode", RNS.LOG_WARNING)
                self.gatt_server = None
                self.enable_peripheral = False
            except Exception as e:
                RNS.log(f"{self} failed to start GATT server: {type(e).__name__}: {e}, disabling peripheral mode", RNS.LOG_WARNING)
                self.gatt_server = None
                self.enable_peripheral = False

        # Fire and forget - don't wait for completion
        asyncio.create_task(start_with_timeout())

    async def _periodic_cleanup(self):
        """
        Periodically clean up stale reassembly buffers (CRITICAL #2: prevent memory leak)

        This task runs every 30 seconds to remove incomplete packet reassembly buffers
        that have timed out. Without this, failed transmissions would leave buffers in
        memory indefinitely, leading to memory exhaustion on long-running instances
        (especially critical on Pi Zero with only 512MB RAM).
        """
        while self.online:
            await asyncio.sleep(30.0)  # Every 30 seconds

            with self.frag_lock:
                total_cleaned = 0
                for peer_address, reassembler in list(self.reassemblers.items()):
                    cleaned = reassembler.cleanup_stale_buffers()
                    if cleaned > 0:
                        total_cleaned += cleaned
                        RNS.log(f"{self} cleaned {cleaned} stale reassembly buffer(s) for {peer_address}",
                               RNS.LOG_DEBUG)

                if total_cleaned > 0:
                    RNS.log(f"{self} periodic cleanup: removed {total_cleaned} stale reassembly buffer(s) total",
                           RNS.LOG_INFO)

    async def _discover_peers(self):
        """Scan for BLE peers advertising Reticulum service."""
        if self.scanning:
            return  # Already scanning

        self.scanning = True

        try:
            # Use callback-based scanner for proper AdvertisementData access
            # This avoids the deprecated device.metadata API
            discovered_devices = []  # List of (device, advertisement_data) tuples

            def detection_callback(device, advertisement_data):
                """Callback invoked for each discovered BLE device."""
                # Debug: Log ALL devices to diagnose why matching fails
                RNS.log(f"{self} DEBUG: Device {device.address} name={device.name} "
                        f"service_uuids={advertisement_data.service_uuids} "
                        f"local_name={advertisement_data.local_name}", RNS.LOG_DEBUG)
                discovered_devices.append((device, advertisement_data))

            # Scan duration based on power mode
            # aggressive: 2.0s (thorough discovery)
            # balanced: 1.0s (default)
            # saver: 0.5s (quick scan, low CPU)
            if self.power_mode == BLEInterface.POWER_MODE_AGGRESSIVE:
                scan_time = 2.0
            elif self.power_mode == BLEInterface.POWER_MODE_SAVER:
                scan_time = 0.5  # Shorter scan for CPU reduction
            else:  # BALANCED
                scan_time = 1.0

            RNS.log(f"{self} scanning for peers (scan_time={scan_time:.1f}s)...", RNS.LOG_EXTREME)

            scanner = BleakScanner(detection_callback=detection_callback)
            try:
                await scanner.start()
                await asyncio.sleep(scan_time)
                await scanner.stop()
            except Exception as e:
                error_msg = str(e)
                # Check for "Not Powered" or similar adapter power issues
                if "No powered Bluetooth adapters" in error_msg or "Not Powered" in error_msg:
                    RNS.log(f"{self} Bluetooth adapter is not powered!", RNS.LOG_ERROR)
                    RNS.log(f"{self} Solution: Run 'bluetoothctl power on' or 'sudo rfkill unblock bluetooth'", RNS.LOG_ERROR)
                    RNS.log(f"{self} See troubleshooting: https://github.com/torlando-tech/ble-reticulum#bluetooth-adapter-not-powered", RNS.LOG_ERROR)
                    # Don't raise, just return - the discovery loop will retry
                    self.scanning = False
                    return
                else:
                    # Re-raise other errors
                    raise

            # Get local adapter address if we don't have it yet (for connection direction preference)
            if self.local_address is None:
                try:
                    # Get the adapter address from the scanner
                    # Note: This is platform-specific, may not work on all platforms
                    if hasattr(scanner, '_adapter') and hasattr(scanner._adapter, 'address'):
                        self.local_address = scanner._adapter.address
                        RNS.log(f"{self} local adapter address: {self.local_address}", RNS.LOG_DEBUG)
                except Exception as e:
                    RNS.log(f"{self} could not get local adapter address: {e}, connection direction preference disabled", RNS.LOG_DEBUG)

            # Process discovered devices
            matching_peers = 0
            now = time.time()

            for device, adv_data in discovered_devices:
                # Check if device matches our service (UUID or name fallback)
                matched = False
                match_method = None

                # Primary: Match by service UUID (standard BLE discovery)
                if self.service_uuid in adv_data.service_uuids:
                    matched = True
                    match_method = "service UUID"

                # Fallback: Match by device name pattern
                # Protocol v2.1: Extract identity from device name (format: RNS-{16-char-hex-hash})
                # This bypasses bluezero service_uuid bug where service_uuids=[] in Bleak scans
                # Also handles Protocol v1 devices with generic RNS- names
                elif device.name and device.name.startswith("RNS-"):
                    # Ensure it's not our own device (self-filtering)
                    if device.name != self.device_name:
                        matched = True
                        match_method = "name pattern (fallback)"
                        RNS.log(f"{self} ⚠ Matched {device.name} by name pattern (fallback)", RNS.LOG_DEBUG)

                if matched:
                    matching_peers += 1
                    rssi = adv_data.rssi
                    device_name = device.name or f"BLE-{device.address[-8:]}"

                    # Protocol v2.1: Try to parse identity from device name (format: RNS-{32-hex-chars})
                    # This bypasses the need to read Identity characteristic over GATT
                    peer_identity_from_name = None
                    if device.name and match_method == "name pattern (fallback)":
                        import re
                        identity_pattern = r'^RNS-([0-9a-f]{32})$'  # 32 hex chars = 16 bytes
                        name_match = re.match(identity_pattern, device.name)
                        if name_match:
                            try:
                                # Parse full 16-byte identity.hash from device name
                                identity_hex = name_match.group(1)
                                peer_identity_from_name = bytes.fromhex(identity_hex)  # 16 bytes
                                self.address_to_identity[device.address] = peer_identity_from_name
                                self.identity_to_address[identity_hex[:16]] = device.address  # Store mapping
                                RNS.log(f"{self} parsed identity from device name {device.name}: {identity_hex[:16]}...", RNS.LOG_INFO)
                            except (ValueError, IndexError) as e:
                                RNS.log(f"{self} failed to parse identity from name {device.name}: {e}", RNS.LOG_DEBUG)

                    # Log all matching peers at DEBUG level for visibility
                    RNS.log(f"{self} found matching peer {device_name} ({device.address}) via {match_method}, "
                            f"RSSI: {rssi}dBm (min: {self.min_rssi}dBm)", RNS.LOG_DEBUG)

                    # Accept if RSSI meets minimum OR is -127 (BlueZ sentinel for "unknown")
                    # -127 means BlueZ doesn't have RSSI data, but device is discoverable
                    if rssi >= self.min_rssi or rssi == -127:
                        # Create or update DiscoveredPeer
                        if device.address in self.discovered_peers:
                            # Update existing peer's RSSI and timestamp
                            self.discovered_peers[device.address].update_rssi(rssi)
                            RNS.log(f"{self} updated peer {device_name} ({device.address}) RSSI: {rssi}dBm", RNS.LOG_EXTREME)
                        else:
                            # New peer discovered
                            self.discovered_peers[device.address] = DiscoveredPeer(device.address, device_name, rssi)
                            RNS.log(f"{self} discovered new peer {device_name} ({device.address}) RSSI: {rssi}dBm, "
                                    f"total_discovered={len(self.discovered_peers)}", RNS.LOG_DEBUG)
                    else:
                        # Log rejection at DEBUG level (not EXTREME) so it's visible with --verbose
                        RNS.log(f"{self} rejecting weak peer {device_name} ({device.address}) "
                                f"RSSI: {rssi}dBm < min_rssi: {self.min_rssi}dBm", RNS.LOG_DEBUG)

            RNS.log(f"{self} scan complete: {len(discovered_devices)} total devices, {matching_peers} matching service UUID, "
                    f"{len(self.discovered_peers)} total discovered, {len(self.peers)} connected", RNS.LOG_DEBUG)

            # After discovery, select and connect to best peers
            selected_peers = self._select_peers_to_connect()
            for peer in selected_peers:
                await self._connect_to_peer(peer)

            # Clean up old discoveries (not seen in 60 seconds)
            stale_timeout = 60.0
            stale = [addr for addr, peer in self.discovered_peers.items()
                     if now - peer.last_seen > stale_timeout]
            if stale:
                RNS.log(f"{self} removing {len(stale)} stale peers not seen in {stale_timeout}s", RNS.LOG_DEBUG)
                for addr in stale:
                    RNS.log(f"{self} removing stale peer {self.discovered_peers[addr].name} ({addr})", RNS.LOG_EXTREME)
                    del self.discovered_peers[addr]

            # HIGH #4: Prune old peers if limit exceeded (prevent unbounded memory growth)
            if len(self.discovered_peers) > self.max_discovered_peers:
                # Remove oldest non-connected peers (those not in self.peers)
                to_remove = []
                with self.peer_lock:
                    for addr, peer in self.discovered_peers.items():
                        if addr not in self.peers:  # Not currently connected
                            to_remove.append((peer.last_seen, addr, peer.name))

                # Sort by last_seen and remove oldest 20%
                to_remove.sort()
                num_to_remove = max(1, len(to_remove) // 5)
                for _, addr, name in to_remove[:num_to_remove]:
                    del self.discovered_peers[addr]
                    RNS.log(f"{self} pruned old peer {name} ({addr}) (discovery cache limit: {self.max_discovered_peers})",
                           RNS.LOG_DEBUG)

        except PermissionError as e:
            RNS.log(f"{self} permission denied during BLE scan: {e}. "
                    f"Try running with elevated privileges or check Bluetooth permissions", RNS.LOG_ERROR)
        except Exception as e:
            error_type = type(e).__name__
            RNS.log(f"{self} error during peer discovery: {error_type}: {e}", RNS.LOG_ERROR)
        finally:
            self.scanning = False

    def _score_peer(self, peer):
        """
        Calculate priority score for peer selection.

        Scoring is weighted as follows:
        - Signal strength (RSSI): 60% (0-70 points based on signal quality)
        - Connection history: 30% (0-50 points based on success rate)
        - Recency: 10% (0-25 points based on how recently seen)

        Algorithm Design Decisions:
        ---------------------------
        1. RSSI Dominance (60% weight): In BLE networks, signal strength is
           the most reliable predictor of connection success and data throughput.
           A peer at -40 dBm will consistently outperform one at -90 dBm,
           regardless of history. This weight ensures we prioritize physically
           close or unobstructed peers.

        2. History Matters (30% weight): Past reliability is important but
           shouldn't override current signal conditions. A previously reliable
           peer that has moved away (poor RSSI) should be deprioritized.
           The 30% weight balances this appropriately.

        3. Recency Bonus (10% weight): Recently seen peers are more likely
           to be currently available. This small weight gives a tiebreaker
           advantage to active peers without dominating the score.

        4. New Peer Benefit: Peers with no history get 25/50 points (50%)
           on history scoring. This "benefit of the doubt" allows new peers
           to compete while requiring them to have good RSSI to be selected.

        5. Clamping RSSI: We clamp RSSI to [-100, -30] dBm range based on
           real-world BLE behavior. Below -100 is essentially no signal,
           above -30 is uncommon and offers no practical benefit.

        6. Linear Recency Decay: Recent peers (<5s) get full points, then
           decay linearly to 0 over 30 seconds. This matches typical BLE
           discovery intervals (5-10s) and prevents stale peer selection.

        Args:
            peer: DiscoveredPeer object

        Returns:
            float: Priority score (higher = better), typically 0-145
                  - Perfect score: 70 (RSSI) + 50 (history) + 25 (recent) = 145
                  - New peer: 70 (RSSI) + 25 (new bonus) + 25 (recent) = 120
                  - Poor peer: 0 (RSSI) + 0 (history) + 0 (old) = 0
        """
        score = 0.0

        # Signal strength component (0-100 points)
        # RSSI typically ranges from -30 (excellent) to -100 (poor)
        # Convert to 0-100 scale
        if peer.rssi is not None:
            # Clamp RSSI to reasonable range
            rssi_clamped = max(-100, min(-30, peer.rssi))
            # Convert to 0-70 range (-100 → 0, -30 → 70)
            rssi_normalized = (rssi_clamped + 100) * (70.0 / 70.0)
            score += rssi_normalized

        # Connection history component (0-50 points)
        # Reward peers with good connection history
        if peer.connection_attempts > 0:
            success_rate = peer.get_success_rate()
            score += success_rate * 50.0
        else:
            # New peers get a moderate score (benefit of the doubt)
            score += 25.0

        # Recency component (0-25 points)
        # Prefer recently seen peers
        age_seconds = time.time() - peer.last_seen
        if age_seconds < 5.0:
            # Very recent (< 5 seconds) - full points
            score += 25.0
        elif age_seconds < 30.0:
            # Recent (< 30 seconds) - decay linearly
            score += 25.0 * (1.0 - (age_seconds - 5.0) / 25.0)
        # Older peers get 0 recency points

        return score

    def _select_peers_to_connect(self):
        """
        Select which peers to connect to based on scoring.

        This method:
        1. Scores all discovered peers
        2. Filters out already-connected peers
        3. Filters out blacklisted peers
        4. Selects top N peers up to max_peers limit

        Algorithm Design Decisions:
        ---------------------------
        1. Greedy Selection: We select the top N highest-scoring peers rather
           than using a threshold. This ensures we always utilize available
           connection slots even if all peers have mediocre scores.

        2. Already-Connected Filter: Skip peers we're already connected to.
           This prevents redundant connection attempts and allows the discovery
           process to focus on finding new peers.

        3. Blacklist Respect: Temporarily blacklisted peers are excluded
           entirely. This prevents connection churn from repeatedly attempting
           to connect to consistently failing peers.

        4. Sort by Score: Sorting ensures deterministic selection and allows
           for easy debugging (highest-scored peers are always chosen first).

        5. Slot-Based Limits: We calculate available_slots = max_peers - current
           rather than a fixed number. This adapts to disconnections and ensures
           we maintain target connection count.

        Returns:
            list: List of DiscoveredPeer objects to connect to
        """
        # Calculate how many connection slots are available
        available_slots = self.max_peers - len(self.peers)
        if available_slots <= 0:
            return []

        # Score all discovered peers
        scored_peers = []
        for address, peer in self.discovered_peers.items():
            # Skip if already connected
            if address in self.peers:
                continue

            # Skip if blacklisted
            if self._is_blacklisted(address):
                continue

            # Calculate score
            score = self._score_peer(peer)
            scored_peers.append((score, peer))

        # Sort by score (highest first)
        scored_peers.sort(reverse=True, key=lambda x: x[0])

        # Select top N peers
        selected = [peer for score, peer in scored_peers[:available_slots]]

        if selected:
            RNS.log(f"{self} selected {len(selected)} peers to connect from {len(scored_peers)} candidates", RNS.LOG_DEBUG)
            for score, peer in scored_peers[:available_slots]:
                RNS.log(f"{self}   -> {peer.name} (score: {score:.1f}, RSSI: {peer.rssi})", RNS.LOG_EXTREME)

        return selected

    def _is_blacklisted(self, address):
        """
        Check if a peer is temporarily blacklisted.

        Args:
            address: BLE address to check

        Returns:
            bool: True if peer is blacklisted
        """
        if address not in self.connection_blacklist:
            return False

        blacklist_until, failure_count = self.connection_blacklist[address]

        # Check if blacklist has expired
        if time.time() >= blacklist_until:
            # Blacklist expired, remove it
            del self.connection_blacklist[address]
            RNS.log(f"{self} blacklist expired for {address}", RNS.LOG_DEBUG)
            return False

        return True

    def _record_connection_success(self, address):
        """
        Record a successful connection.

        Args:
            address: BLE address of peer
        """
        if address in self.discovered_peers:
            self.discovered_peers[address].record_connection_success()

            # Clear blacklist on success
            if address in self.connection_blacklist:
                del self.connection_blacklist[address]
                RNS.log(f"{self} cleared blacklist for {address} after successful connection", RNS.LOG_DEBUG)

    def _record_connection_failure(self, address):
        """
        Record a failed connection and update blacklist.

        Algorithm Design Decisions:
        ---------------------------
        1. Exponential Backoff: Blacklist duration increases exponentially
           with consecutive failures. This prevents connection churn while
           still allowing eventual retries if conditions improve.
           Formula: backoff * min(failures - threshold + 1, 8)
           Example: 60s, 120s, 240s, 480s (capped at 8x = 480s)

        2. Threshold-Based Activation: We only blacklist after N failures
           (default 3) to tolerate temporary issues like brief signal loss
           or interference without permanently marking peers as bad.

        3. Capped Multiplier: We cap the backoff multiplier at 8x to prevent
           excessively long blacklist periods (e.g., hours). After 480s, a
           peer is likely to have moved or conditions changed enough to retry.

        4. Failure Counter Persists: We track total failed_connections rather
           than resetting on blacklist. This provides long-term reliability
           data for scoring even after blacklist expires.

        Args:
            address: BLE address of peer
        """
        if address in self.discovered_peers:
            peer = self.discovered_peers[address]
            peer.record_connection_failure()

            # Check if we should blacklist this peer
            if peer.failed_connections >= self.max_connection_failures:
                # Blacklist with exponential backoff
                backoff_multiplier = min(peer.failed_connections - self.max_connection_failures + 1, 8)
                blacklist_duration = self.connection_retry_backoff * backoff_multiplier
                blacklist_until = time.time() + blacklist_duration

                self.connection_blacklist[address] = (blacklist_until, peer.failed_connections)
                RNS.log(f"{self} blacklisted {peer.name} for {blacklist_duration:.0f}s after {peer.failed_connections} failures", RNS.LOG_WARNING)

    async def _connect_to_peer(self, peer):
        """
        Attempt to connect to a discovered peer.

        This method handles:
        - Connection attempt tracking
        - Success/failure recording
        - Blacklist management
        - BLE client setup
        - Peer interface creation

        Args:
            peer: DiscoveredPeer object to connect to
        """
        # Check if already connected (either as central or if they connected to us as peripheral)
        with self.peer_lock:
            if peer.address in self.peers:
                RNS.log(f"{self} already connected to {peer.name} (central mode)", RNS.LOG_EXTREME)
                return

            # Dual-connection mode (BitChat model): Always attempt central connection
            # Both devices connect to each other, creating TWO interfaces per peer:
            #   - "address-central" (we connect to their peripheral)
            #   - "address-peripheral" (they connect to our peripheral)
            # Reticulum Transport handles deduplication if packets sent on both paths

            # Skip if we're trying to connect to ourselves
            if self.local_address and peer.address == self.local_address:
                RNS.log(f"{self} skipping connection to self ({peer.address})", RNS.LOG_DEBUG)
                return

            # Check if we already have a CENTRAL connection to this peer
            conn_id = f"{peer.address}-central"
            if conn_id in self.spawned_interfaces:
                RNS.log(f"{self} already connected to {peer.name} as central", RNS.LOG_EXTREME)
                return

        # Record connection attempt
        peer.record_connection_attempt()

        # Attempt connection
        try:
            RNS.log(f"{self} connecting to {peer.name} ({peer.address}) "
                    f"RSSI: {peer.rssi}dBm, success_rate: {peer.get_success_rate():.0%}, "
                    f"attempt {peer.connection_attempts + 1}", RNS.LOG_DEBUG)

            # Create disconnection callback for diagnostic logging
            def disconnected_callback(client_obj):
                """Called when BlueZ reports the device has disconnected"""
                RNS.log(f"{self} BLE client for {peer.name} ({peer.address}) disconnected unexpectedly", RNS.LOG_WARNING)

                # Clean up all peer state atomically (CRITICAL #1: memory leak fix)
                # This prevents fragmentation state from leaking when peers disconnect mid-transmission

                # 1. Clean up peer connection state
                with self.peer_lock:
                    if peer.address in self.peers:
                        del self.peers[peer.address]

                # 2. Remove central connection from unified interface
                peer_identity = self.address_to_identity.get(peer.address, None)

                if peer_identity:
                    # Protocol v2: Use identity-based lookup
                    identity_hash = RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]
                    if identity_hash in self.spawned_interfaces:
                        peer_if = self.spawned_interfaces[identity_hash]
                        peer_if.remove_central_connection()

                        # If no connections remain, detach and remove
                        if not peer_if.has_central_connection and not peer_if.has_peripheral_connection:
                            peer_if.detach()
                            del self.spawned_interfaces[identity_hash]
                            RNS.log(f"{self} detached unified interface for {peer.address} (no connections remain)", RNS.LOG_DEBUG)
                else:
                    # Protocol v1 fallback: Use address-based lookup
                    conn_id = f"{peer.address}-central"
                    if conn_id in self.spawned_interfaces:
                        self.spawned_interfaces[conn_id].detach()
                        del self.spawned_interfaces[conn_id]
                        RNS.log(f"{self} cleaned up legacy spawned interface for {peer.address}", RNS.LOG_DEBUG)

                # 3. Clean up fragmentation state only if no connections remain
                should_cleanup_frag = True
                if peer_identity:
                    identity_hash = RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]
                    if identity_hash in self.spawned_interfaces:
                        should_cleanup_frag = False  # Interface still has peripheral connection
                else:
                    # Check legacy peripheral connection
                    if f"{peer.address}-peripheral" in self.spawned_interfaces:
                        should_cleanup_frag = False

                if should_cleanup_frag:
                    with self.frag_lock:
                        if peer.address in self.fragmenters:
                            del self.fragmenters[peer.address]
                            RNS.log(f"{self} cleaned up fragmenter for {peer.address}", RNS.LOG_DEBUG)
                        if peer.address in self.reassemblers:
                            del self.reassemblers[peer.address]
                            RNS.log(f"{self} cleaned up reassembler for {peer.address}", RNS.LOG_DEBUG)

            # Try LE-specific connection if BlueZ >= 5.49 and we haven't confirmed ConnectDevice unavailable
            le_connection_attempted = False
            if self.bluez_version and self.bluez_version >= (5, 49) and not self.has_connect_device:
                try:
                    # Attempt D-Bus ConnectDevice with explicit LE type
                    # This bypasses BlueZ's BR/EDR priority for dual-mode devices
                    await self._connect_via_dbus_le(peer.address)
                    le_connection_attempted = True
                    RNS.log(f"{self} LE-specific connection initiated for {peer.name}", RNS.LOG_DEBUG)
                except (AttributeError, PermissionError, Exception) as e:
                    # ConnectDevice not available (experimental mode disabled or unsupported)
                    RNS.log(f"{self} ConnectDevice() unavailable ({type(e).__name__}), falling back to standard connection", RNS.LOG_DEBUG)
                    self.has_connect_device = False  # Don't try again

            # Create BleakClient
            client = BleakClient(peer.address, disconnected_callback=disconnected_callback)

            # Connect (either complete the LE connection or do standard connection)
            if not le_connection_attempted:
                await client.connect(timeout=self.connection_timeout)
            else:
                # Device already connected via ConnectDevice(), just set up bleak's state
                try:
                    await client.connect(timeout=5.0)  # Shorter timeout since device should be connected
                except Exception as e:
                    # If this fails, ConnectDevice didn't actually connect the device
                    RNS.log(f"{self} ConnectDevice() didn't establish connection, falling back", RNS.LOG_DEBUG)
                    await client.connect(timeout=self.connection_timeout)

            if client.is_connected:
                # bluezero D-Bus registration delay
                # bluezero registers characteristics asynchronously with BlueZ D-Bus.
                # We need to wait for registration to complete before discovering services.
                if self.service_discovery_delay > 0:
                    RNS.log(f"{self} connection established, waiting {self.service_discovery_delay}s for bluezero D-Bus registration", RNS.LOG_INFO)
                    await asyncio.sleep(self.service_discovery_delay)
                else:
                    RNS.log(f"{self} connection established, no service discovery delay configured", RNS.LOG_DEBUG)

                # Service discovery diagnostics
                try:
                    RNS.log(f"{self} discovering services for {peer.name} ({peer.address})...", RNS.LOG_DEBUG)

                    discovery_start = time.time()

                    # Bleak 1.1.1: Try new services property first
                    services = list(client.services) if client.services else []

                    # Fallback: If services property is empty, force discovery with deprecated method
                    # This is needed for bluezero GATT servers where automatic discovery doesn't complete
                    if not services:
                        RNS.log(f"{self} services property empty, forcing discovery with get_services()", RNS.LOG_DEBUG)
                        services_collection = await client.get_services()
                        services = list(services_collection)

                    discovery_time = time.time() - discovery_start

                    RNS.log(f"{self} service discovery completed in {discovery_time:.3f}s, found {len(services)} services", RNS.LOG_DEBUG)

                    # Find Reticulum service
                    reticulum_service = None
                    for svc in services:
                        target_uuid = self.service_uuid.lower()
                        svc_uuid = svc.uuid.lower()

                        if svc_uuid == target_uuid:
                            reticulum_service = svc
                            RNS.log(f"{self} found Reticulum service with {len(svc.characteristics)} characteristics", RNS.LOG_DEBUG)
                            break

                    if not reticulum_service:
                        RNS.log(f"{self} Reticulum service not found (expected UUID: {self.service_uuid}, will retry)", RNS.LOG_WARNING)

                except Exception as e:
                    RNS.log(f"{self} service discovery failed: {type(e).__name__}: {e} (will retry)", RNS.LOG_WARNING)

                # Read Identity characteristic (Protocol v2) if available
                peer_identity = None
                identity_hash = None
                if reticulum_service:
                    try:
                        identity_char = None
                        for char in reticulum_service.characteristics:
                            if char.uuid.lower() == BLEInterface.CHARACTERISTIC_IDENTITY_UUID.lower():
                                identity_char = char
                                break

                        if identity_char:
                            RNS.log(f"{self} reading Identity characteristic from {peer.name}...", RNS.LOG_DEBUG)
                            identity_value = await client.read_gatt_char(identity_char)
                            if identity_value and len(identity_value) == 16:
                                # Store as bytes for identity-based interface tracking
                                peer_identity = bytes(identity_value)
                                identity_hash = RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]

                                # Store identity mappings for unified interface architecture
                                self.address_to_identity[peer.address] = peer_identity
                                self.identity_to_address[identity_hash] = peer.address

                                RNS.log(f"{self} received peer identity from {peer.name}: {identity_hash}", RNS.LOG_INFO)
                            else:
                                RNS.log(f"{self} invalid identity size from {peer.name}: {len(identity_value) if identity_value else 0} bytes", RNS.LOG_WARNING)
                        else:
                            RNS.log(f"{self} Identity characteristic not found on {peer.name} (Protocol v1 device)", RNS.LOG_DEBUG)
                    except Exception as e:
                        RNS.log(f"{self} failed to read identity from {peer.name}: {type(e).__name__}: {e}", RNS.LOG_DEBUG)
                        # Continue without identity

                # Send connection handshake WITH our identity to trigger peripheral callback
                # This enables the peripheral to create a unified interface with our identity
                # without needing to discover us via scanning (solves asymmetric discovery issue)
                try:
                    # Get our own identity to send in handshake
                    our_identity = self.gatt_server.identity_hash if (self.gatt_server and self.gatt_server.identity_hash) else b'\x00' * 16
                    await client.write_gatt_char(self.CHARACTERISTIC_RX_UUID, our_identity, response=True)
                    identity_preview = our_identity[:8].hex() if len(our_identity) >= 8 else "null"
                    RNS.log(f"{self} sent connection handshake WITH identity to {peer.name} ({len(our_identity)} bytes, {identity_preview}...)", RNS.LOG_DEBUG)
                except Exception as e:
                    RNS.log(f"{self} handshake write failed (non-critical): {e}", RNS.LOG_WARNING)

                # Get negotiated MTU
                try:
                    # For BlueZ backend, acquire MTU first to avoid warning
                    # This queries D-Bus for the actual negotiated MTU value
                    if hasattr(client, '_backend') and hasattr(client._backend, '_acquire_mtu'):
                        try:
                            await client._backend._acquire_mtu()
                            RNS.log(f"{self} acquired MTU from BlueZ D-Bus for {peer.name}", RNS.LOG_EXTREME)
                        except Exception as e:
                            RNS.log(f"{self} failed to acquire MTU via D-Bus: {e}, will use default", RNS.LOG_DEBUG)

                    mtu = client.mtu_size
                    RNS.log(f"{self} negotiated MTU {mtu} with {peer.name}", RNS.LOG_DEBUG)
                except Exception as e:
                    RNS.log(f"{self} could not get MTU from {peer.name}, using default 23: {type(e).__name__}: {e}", RNS.LOG_WARNING)
                    mtu = 23  # BLE 4.0 minimum

                with self.peer_lock:
                    self.peers[peer.address] = (client, time.time(), mtu)

                # Create fragmenter for this peer's MTU
                # KEY CHANGE: Use identity_hash for keying (survives MAC rotation, fixes dev: prefix issue)
                frag_key = self._get_fragmenter_key(peer_identity, peer.address)
                with self.frag_lock:
                    self.fragmenters[frag_key] = BLEFragmenter(mtu=mtu)
                    self.reassemblers[frag_key] = BLEReassembler(timeout=self.connection_timeout)
                RNS.log(f"{self} created fragmenter/reassembler for peer (key: {frag_key[:16]})", RNS.LOG_DEBUG)

                # Create or update unified peer interface with central connection
                self._spawn_or_update_peer_interface(
                    address=peer.address,
                    name=peer.name,
                    peer_identity=peer_identity,  # May be None for Protocol v1 devices
                    client=client,
                    mtu=mtu,
                    connection_type="central"
                )

                # Set up notification handler for incoming data
                RNS.log(f"{self} setting up TX characteristic notifications for {peer.name}...", RNS.LOG_INFO)
                notification_success = False
                max_retries = 3
                retry_delays = [0.2, 0.5, 1.0]  # Exponential backoff

                for attempt in range(max_retries):
                    try:
                        if attempt > 0:
                            # Wait before retry
                            await asyncio.sleep(retry_delays[attempt - 1])
                            RNS.log(f"{self} retrying notification setup for {peer.name} (attempt {attempt + 1}/{max_retries})", RNS.LOG_DEBUG)

                        RNS.log(f"{self} calling start_notify() for TX characteristic (attempt {attempt + 1})...", RNS.LOG_INFO)

                        await client.start_notify(
                            BLEInterface.CHARACTERISTIC_TX_UUID,
                            lambda sender, data: self._handle_ble_data(peer.address, data)
                        )

                        notification_success = True
                        RNS.log(f"{self} ✓ notification setup SUCCEEDED on attempt {attempt + 1} for {peer.name}", RNS.LOG_INFO)
                        break  # Success, exit retry loop

                    except (EOFError, KeyError) as e:
                        # EOFError/KeyError typically indicate GATT services not discovered/ready yet
                        if attempt < max_retries - 1:
                            error_name = type(e).__name__
                            RNS.log(f"{self} GATT services not ready for {peer.name}, will retry ({error_name})", RNS.LOG_DEBUG)
                            continue  # Try again
                        else:
                            error_name = type(e).__name__
                            RNS.log(f"{self} failed to start notifications for {peer.name} after {max_retries} attempts: {error_name} (GATT services may not be fully discovered, will retry connection)", RNS.LOG_WARNING)
                    except Exception as e:
                        # Other errors are not retryable
                        RNS.log(f"{self} failed to start notifications for {peer.name}: {type(e).__name__}: {e} (will retry connection)", RNS.LOG_WARNING)
                        break  # Don't retry non-service-discovery exceptions

                # If notification setup failed after all retries, clean up
                if not notification_success:
                    # Clean up the failed connection
                    with self.peer_lock:
                        if peer.address in self.peers:
                            del self.peers[peer.address]
                    with self.frag_lock:
                        if peer.address in self.fragmenters:
                            del self.fragmenters[peer.address]
                        if peer.address in self.reassemblers:
                            del self.reassemblers[peer.address]
                    # Clean up central connection peer interface
                    conn_id = f"{peer.address}-central"
                    if conn_id in self.spawned_interfaces:
                        self.spawned_interfaces[conn_id].detach()
                        del self.spawned_interfaces[conn_id]
                    await client.disconnect()
                    # Record failure and return (don't raise exception)
                    self._record_connection_failure(peer.address)
                    return

                # Record success
                self._record_connection_success(peer.address)

                RNS.log(f"{self} connected to {peer.name} ({peer.address}), "
                        f"MTU={mtu}, total_peers={len(self.peers)}/{self.max_peers}", RNS.LOG_INFO)

        except asyncio.TimeoutError as e:
            # Connection timeout - likely peer moved out of range or is busy
            self._record_connection_failure(peer.address)
            RNS.log(f"{self} connection timeout to {peer.name} ({peer.address}) "
                    f"after {self.connection_timeout}s, failures={peer.failed_connections}", RNS.LOG_WARNING)
        except PermissionError as e:
            # Permission denied - need special permissions on this platform
            self._record_connection_failure(peer.address)
            RNS.log(f"{self} permission denied connecting to {peer.name}: {e}. "
                    f"Try running with elevated privileges or check Bluetooth permissions", RNS.LOG_ERROR)
        except Exception as e:
            # Other errors - hardware issues, invalid address, etc.
            self._record_connection_failure(peer.address)
            error_type = type(e).__name__

            # Special handling for BR/EDR vs LE connection errors
            error_str = str(e)
            if "BREDR.ProfileUnavailable" in error_str or "No more profiles to connect to" in error_str:
                # BlueZ is trying BR/EDR instead of LE
                version_str = f"{self.bluez_version[0]}.{self.bluez_version[1]}" if self.bluez_version else "unknown"
                RNS.log(f"{self} BR/EDR connection failed to {peer.name} (BLE GATT device). BlueZ is "
                        f"prioritizing BR/EDR over LE. BlueZ version: {version_str}", RNS.LOG_WARNING)

                if self.bluez_version and self.bluez_version >= (5, 49):
                    RNS.log(f"{self} To enable LE-specific connections on BlueZ {version_str}:", RNS.LOG_WARNING)
                    RNS.log(f"{self}   1. Enable experimental mode: sudo systemctl edit bluetooth", RNS.LOG_WARNING)
                    RNS.log(f"{self}      Add: ExecStart=", RNS.LOG_WARNING)
                    RNS.log(f"{self}      Add: ExecStart=/usr/lib/bluetooth/bluetoothd -E", RNS.LOG_WARNING)
                    RNS.log(f"{self}   2. Restart: sudo systemctl restart bluetooth", RNS.LOG_WARNING)
                else:
                    RNS.log(f"{self} Alternative: Set target device to LE-only mode in /etc/bluetooth/main.conf", RNS.LOG_WARNING)

            else:
                # Standard error logging
                RNS.log(f"{self} failed to connect to {peer.name} ({peer.address}): "
                        f"{error_type}: {e}, failures={peer.failed_connections}", RNS.LOG_WARNING)

    def _get_fragmenter_key(self, peer_identity, peer_address):
        """
        Compute fragmenter/reassembler dictionary key.

        Uses identity_hash for Protocol v2 devices (survives MAC rotation),
        falls back to normalized MAC for Protocol v1 legacy devices.

        Args:
            peer_identity: 16-byte peer identity (None for Protocol v1)
            peer_address: BLE MAC address (may have "dev:" prefix)

        Returns:
            str: Identity hash (16 hex chars) or normalized MAC address
        """
        if peer_identity:
            # Protocol v2: Use identity hash (immune to MAC rotation)
            return RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]
        else:
            # Protocol v1 fallback: Use normalized MAC address
            return peer_address.replace("dev:", "")

    def _spawn_or_update_peer_interface(self, address, name, peer_identity=None, client=None, mtu=None, connection_type="central"):
        """
        Create or update a unified peer interface that can handle both central and peripheral connections.

        This implements the unified interface architecture where one BLEPeerInterface manages
        both connection types for a given peer identity, eliminating duplicate interfaces.

        Args:
            address: BLE address of peer
            name: Name of peer device
            peer_identity: 16-byte peer identity (None for Protocol v1 legacy devices)
            client: BleakClient instance (for central connections)
            mtu: Negotiated MTU (for central connections)
            connection_type: "central" (we connected to them) or "peripheral" (they connected to us)

        Returns:
            BLEPeerInterface: The spawned or updated interface
        """
        # Compute lookup key: identity_hash for v2, address-based for v1 legacy
        if peer_identity:
            identity_hash = RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]
        else:
            # Legacy Protocol v1 device - use address-based key
            identity_hash = f"{address}-{connection_type}"
            RNS.log(f"{self} no identity for {name}, using legacy address-based tracking", RNS.LOG_DEBUG)

        # Check if unified interface already exists for this peer
        if identity_hash in self.spawned_interfaces:
            peer_if = self.spawned_interfaces[identity_hash]

            # Add the new connection type to existing interface
            if connection_type == "central":
                peer_if.add_central_connection(client, mtu)
                RNS.log(f"{self} added central connection to existing interface for {name} (now {peer_if._get_connection_state_str()})", RNS.LOG_INFO)
            else:  # peripheral
                peer_if.add_peripheral_connection()
                RNS.log(f"{self} added peripheral connection to existing interface for {name} (now {peer_if._get_connection_state_str()})", RNS.LOG_INFO)

            return peer_if

        # Create new unified interface
        peer_if = BLEPeerInterface(self, address, name, peer_identity)
        peer_if.OUT = self.OUT
        peer_if.IN = self.IN
        peer_if.parent_interface = self
        peer_if.bitrate = self.bitrate
        peer_if.HW_MTU = self.HW_MTU
        peer_if.online = True

        # Add the first connection
        if connection_type == "central":
            peer_if.add_central_connection(client, mtu)
        else:  # peripheral
            peer_if.add_peripheral_connection()

        # Register with transport
        RNS.Transport.interfaces.append(peer_if)

        # Note: No tunnel registration needed - direct peer connections use
        # RNS.Transport.interfaces[] only (same pattern as I2PInterface)

        # Store in unified tracking
        self.spawned_interfaces[identity_hash] = peer_if

        identity_str = identity_hash[:8] if peer_identity else "legacy"
        RNS.log(f"{self} created NEW unified interface for {name} ({identity_str}), state: {peer_if._get_connection_state_str()}", RNS.LOG_INFO)

        return peer_if

    def _handle_ble_data(self, peer_address, data):
        """
        Handle incoming BLE data from a peer (may be fragment).

        Args:
            peer_address: Address of peer that sent data
            data: Raw bytes received (might be fragment)
        """
        # Attempt reassembly
        complete_packet = None
        peer_name = None

        # HIGH #2: Lock ordering - get reassembler reference with frag_lock, release before processing
        # This prevents holding frag_lock during reassembly which could block other threads
        with self.frag_lock:
            if peer_address not in self.reassemblers:
                return  # No reassembler for this peer
            reassembler = self.reassemblers[peer_address]

        # Process fragment without holding lock (reassemblers are per-peer, no contention)
        try:
            # Ensure data is bytes (Bleak notifications may return bytearray)
            data_bytes = bytes(data) if not isinstance(data, bytes) else data
            complete_packet = reassembler.receive_fragment(data_bytes, peer_address)

            # Periodic cleanup of stale buffers (if packet complete)
            if complete_packet:
                cleaned = reassembler.cleanup_stale_buffers()
                if cleaned > 0:
                    RNS.log(f"{self} cleaned {cleaned} stale reassembly buffers for {peer_address}", RNS.LOG_DEBUG)

                # Log fragmentation statistics for this peer
                stats = reassembler.get_statistics()
                # Get peer name from unified interface lookup
                peer_identity = self.address_to_identity.get(peer_address, None)
                peer_if = None

                if peer_identity:
                    # Protocol v2: identity-based lookup
                    identity_hash = RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]
                    peer_if = self.spawned_interfaces.get(identity_hash, None)
                else:
                    # Protocol v1 fallback: try address-based lookup
                    peer_if = self.spawned_interfaces.get(f"{peer_address}-central", None)
                    if not peer_if:
                        peer_if = self.spawned_interfaces.get(f"{peer_address}-peripheral", None)

                peer_name = peer_if.peer_name if peer_if else peer_address[-8:]
                RNS.log(f"{self} reassembled packet from {peer_name}: "
                        f"total_packets={stats['packets_reassembled']}, "
                        f"total_fragments={stats['fragments_received']}, "
                        f"pending={stats['pending_packets']}, "
                        f"timeouts={stats['packets_timeout']}", RNS.LOG_DEBUG)

        except Exception as e:
            RNS.log(f"{self} error reassembling fragment from {peer_address}: {type(e).__name__}: {e}", RNS.LOG_ERROR)
            return

        # If we have a complete packet, route to unified peer interface
        if complete_packet:
            peer_identity = self.address_to_identity.get(peer_address, None)
            peer_if = None

            if peer_identity:
                # Protocol v2: identity-based lookup
                identity_hash = RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]
                peer_if = self.spawned_interfaces.get(identity_hash, None)
            else:
                # Protocol v1 fallback: address-based lookup (try central first)
                peer_if = self.spawned_interfaces.get(f"{peer_address}-central", None)

            if peer_if:
                peer_if.process_incoming(complete_packet)
            else:
                RNS.log(f"{self} no interface found for peer {peer_address}, packet dropped", RNS.LOG_WARNING)

    def handle_peripheral_data(self, data, sender_address):
        """
        Handle incoming data from a central device connected to our GATT server.

        This is called by the BLEGATTServer when a central writes to the RX characteristic.

        Args:
            data: Raw bytes received from central
            sender_address: BLE address of the central device
        """
        RNS.log(f"{self} received {len(data)} bytes from central {sender_address}", RNS.LOG_EXTREME)

        # Detect identity handshake (16 bytes, likely the first write from this peer)
        # The central sends its own identity in the handshake to enable unified interface creation
        # even when the peripheral hasn't discovered the central via scanning
        if len(data) == 16:
            central_identity = bytes(data)
            central_identity_hash = RNS.Identity.full_hash(central_identity)[:16].hex()[:16]

            # Store or verify identity mapping
            if sender_address not in self.address_to_identity:
                # First time seeing this identity for this address
                self.address_to_identity[sender_address] = central_identity
                self.identity_to_address[central_identity_hash] = sender_address
                RNS.log(f"{self} received identity handshake from {sender_address}: {central_identity_hash}", RNS.LOG_INFO)
            else:
                # Already know identity - verify it matches
                existing_identity = self.address_to_identity[sender_address]
                if existing_identity == central_identity:
                    RNS.log(f"{self} received identity handshake confirmation from {sender_address}: {central_identity_hash}", RNS.LOG_DEBUG)
                else:
                    RNS.log(f"{self} WARNING: identity mismatch for {sender_address}! Existing vs received", RNS.LOG_WARNING)

            # Check if we need to merge interfaces
            legacy_conn_id = f"{sender_address}-peripheral"
            if legacy_conn_id in self.spawned_interfaces:
                # Legacy peripheral interface exists - need to migrate or merge
                if central_identity_hash in self.spawned_interfaces:
                    # We already have an identity-based interface (from central connection)
                    # Add peripheral connection to it and remove legacy interface
                    identity_if = self.spawned_interfaces[central_identity_hash]
                    legacy_if = self.spawned_interfaces[legacy_conn_id]

                    # Add peripheral connection to unified interface
                    identity_if.add_peripheral_connection()

                    # Clean up legacy interface
                    legacy_if.detach()
                    del self.spawned_interfaces[legacy_conn_id]

                    RNS.log(f"{self} merged legacy peripheral into identity-based interface {central_identity_hash} (now {identity_if._get_connection_state_str()})", RNS.LOG_INFO)
                else:
                    # No identity-based interface yet - migrate the legacy one
                    legacy_if = self.spawned_interfaces[legacy_conn_id]
                    del self.spawned_interfaces[legacy_conn_id]

                    legacy_if.peer_identity = central_identity
                    self.spawned_interfaces[central_identity_hash] = legacy_if

                    RNS.log(f"{self} migrated interface from legacy ({legacy_conn_id}) to identity-based ({central_identity_hash})", RNS.LOG_INFO)

            # Create fragmenter/reassembler for peripheral connection to enable bidirectional data flow
            # This is critical: fragmenters must exist for BOTH central and peripheral connections
            frag_key = self._get_fragmenter_key(central_identity, sender_address)
            with self.frag_lock:
                if frag_key not in self.fragmenters:
                    # Get MTU from GATT server for this peripheral connection
                    mtu = self.gatt_server.get_central_mtu(sender_address) if self.gatt_server else 23
                    self.fragmenters[frag_key] = BLEFragmenter(mtu=mtu)
                    self.reassemblers[frag_key] = BLEReassembler(timeout=self.connection_timeout)
                    RNS.log(f"{self} created fragmenter for peripheral connection (key: {frag_key[:16]}, MTU: {mtu})", RNS.LOG_DEBUG)
                else:
                    RNS.log(f"{self} fragmenter already exists for peripheral connection (key: {frag_key[:16]})", RNS.LOG_EXTREME)

            return  # Don't process handshake as data

        # Update fragmenter MTU if GATT server has learned a new MTU
        # (MTU is provided by BlueZ in write callback options)
        if self.gatt_server and hasattr(self.gatt_server, 'get_central_mtu'):
            current_mtu = self.gatt_server.get_central_mtu(sender_address)
            with self.frag_lock:
                if sender_address in self.fragmenters:
                    existing_mtu = self.fragmenters[sender_address].mtu
                    if current_mtu != existing_mtu:
                        RNS.log(f"{self} updating fragmenter MTU for {sender_address}: {existing_mtu} -> {current_mtu}", RNS.LOG_INFO)
                        self.fragmenters[sender_address] = BLEFragmenter(mtu=current_mtu)

        # Attempt reassembly
        complete_packet = None

        with self.frag_lock:
            if sender_address not in self.reassemblers:
                # Create reassembler for this peer
                self.reassemblers[sender_address] = BLEReassembler(timeout=self.connection_timeout)

            try:
                # Ensure data is bytes (bluezero may pass different types)
                data_bytes = bytes(data) if not isinstance(data, bytes) else data
                complete_packet = self.reassemblers[sender_address].receive_fragment(data_bytes, sender_address)

                # Periodic cleanup
                if complete_packet:
                    cleaned = self.reassemblers[sender_address].cleanup_stale_buffers()
                    if cleaned > 0:
                        RNS.log(f"{self} cleaned {cleaned} stale reassembly buffers for central {sender_address}", RNS.LOG_DEBUG)

                    # Log fragmentation statistics for this central
                    stats = self.reassemblers[sender_address].get_statistics()
                    # Get peer name from unified interface lookup
                    peer_identity = self.address_to_identity.get(sender_address, None)
                    peer_if = None

                    if peer_identity:
                        # Protocol v2: identity-based lookup
                        identity_hash = RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]
                        peer_if = self.spawned_interfaces.get(identity_hash, None)
                    else:
                        # Protocol v1 fallback: try address-based lookup
                        peer_if = self.spawned_interfaces.get(f"{sender_address}-peripheral", None)
                        if not peer_if:
                            peer_if = self.spawned_interfaces.get(f"{sender_address}-central", None)

                    peer_name = peer_if.peer_name if peer_if else sender_address[-8:]
                    RNS.log(f"{self} reassembled packet from {peer_name}: "
                            f"total_packets={stats['packets_reassembled']}, "
                            f"total_fragments={stats['fragments_received']}, "
                            f"pending={stats['pending_packets']}, "
                            f"timeouts={stats['packets_timeout']}", RNS.LOG_DEBUG)

            except Exception as e:
                RNS.log(f"{self} error reassembling fragment from central {sender_address}: {type(e).__name__}: {e}", RNS.LOG_ERROR)
                return

        # If we have a complete packet, route to unified peer interface
        if complete_packet:
            peer_identity = self.address_to_identity.get(sender_address, None)
            peer_if = None

            if peer_identity:
                # Protocol v2: identity-based lookup
                identity_hash = RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]
                peer_if = self.spawned_interfaces.get(identity_hash, None)
            else:
                # Protocol v1 fallback: address-based lookup (try peripheral first)
                peer_if = self.spawned_interfaces.get(f"{sender_address}-peripheral", None)

            if peer_if:
                RNS.log(f"{self} DIAGNOSTIC: Routing packet to {peer_if}", RNS.LOG_DEBUG)
                peer_if.process_incoming(complete_packet)
            else:
                RNS.log(f"{self} DIAGNOSTIC: No interface found for {sender_address}, packet dropped!", RNS.LOG_WARNING)
        elif not complete_packet:
            RNS.log(f"{self} DIAGNOSTIC: No complete packet yet from {sender_address} (waiting for more fragments)", RNS.LOG_DEBUG)

    def _create_peripheral_peer(self, address):
        """
        Create a peer interface for a central device connected to our GATT server.

        Args:
            address: BLE address of the central device
        """
        conn_id = f"{address}-peripheral"

        if conn_id in self.spawned_interfaces:
            return  # Already exists

        # Create peer interface
        peer_if = BLEPeerInterface(self, address, f"Central-{address[-8:]}")
        peer_if.OUT = self.OUT
        peer_if.IN = self.IN
        peer_if.parent_interface = self
        peer_if.bitrate = self.bitrate
        peer_if.HW_MTU = self.HW_MTU
        peer_if.online = True
        peer_if.connection_type = "peripheral"
        peer_if.is_peripheral_connection = True

        # Register with transport
        RNS.Transport.interfaces.append(peer_if)

        # Note: No tunnel registration needed - direct peer connections use
        # RNS.Transport.interfaces[] only (same pattern as I2PInterface)

        self.spawned_interfaces[conn_id] = peer_if

        # Create fragmenter using negotiated MTU from GATT server (if available)
        # Fragmenters are keyed by ADDRESS (shared between central and peripheral connections)
        with self.frag_lock:
            if address not in self.fragmenters:
                # Query GATT server for negotiated MTU
                mtu = 185  # Default fallback
                if self.gatt_server and hasattr(self.gatt_server, 'get_central_mtu'):
                    mtu = self.gatt_server.get_central_mtu(address)
                    RNS.log(f"{self} using negotiated MTU {mtu} for peripheral connection from {address}", RNS.LOG_DEBUG)
                else:
                    RNS.log(f"{self} GATT server doesn't support MTU query, using default {mtu}", RNS.LOG_DEBUG)

                self.fragmenters[address] = BLEFragmenter(mtu=mtu)

        RNS.log(f"{self} created peer interface for central {address} (MTU: {mtu}) via peripheral", RNS.LOG_DEBUG)

    def handle_central_connected(self, address):
        """
        Handle a central device connecting to our GATT server.

        With the unified interface architecture, this either creates a new interface
        or adds a peripheral connection to an existing interface for this peer.

        Args:
            address: BLE address of the central device
        """
        RNS.log(f"{self} central {address} connected to our peripheral", RNS.LOG_INFO)

        # Look up peer identity if we have it (from when we connected as central)
        peer_identity = self.address_to_identity.get(address, None)

        # Create or update unified interface with peripheral connection
        self._spawn_or_update_peer_interface(
            address=address,
            name=f"Central-{address[-8:]}",  # Will be updated if we learn better name
            peer_identity=peer_identity,  # May be None if we haven't connected as central yet
            client=None,  # No client for peripheral connections
            mtu=None,  # MTU managed by GATT server
            connection_type="peripheral"
        )

    def handle_central_disconnected(self, address):
        """
        Handle a central device disconnecting from our GATT server.

        With unified interface architecture, this removes the peripheral connection
        from the interface. The interface is only detached if no connections remain.

        Args:
            address: BLE address of the central device
        """
        RNS.log(f"{self} central disconnected: {address}", RNS.LOG_INFO)

        # Look up peer identity
        peer_identity = self.address_to_identity.get(address, None)

        if peer_identity:
            # Protocol v2: Use identity-based lookup
            identity_hash = RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]
            if identity_hash in self.spawned_interfaces:
                peer_if = self.spawned_interfaces[identity_hash]
                peer_if.remove_peripheral_connection()

                # If no connections remain, detach and remove interface
                if not peer_if.has_central_connection and not peer_if.has_peripheral_connection:
                    peer_if.detach()
                    del self.spawned_interfaces[identity_hash]
                    RNS.log(f"{self} detached unified interface for {address} (no connections remain)", RNS.LOG_DEBUG)
        else:
            # Protocol v1 fallback: Use address-based lookup
            conn_id = f"{address}-peripheral"
            if conn_id in self.spawned_interfaces:
                peer_if = self.spawned_interfaces[conn_id]
                peer_if.detach()
                del self.spawned_interfaces[conn_id]
                RNS.log(f"{self} cleaned up legacy peripheral peer interface for {address}", RNS.LOG_DEBUG)

        # Clean up shared fragmenter/reassembler only if NO connections remain
        # Check both v2 (identity-based) and v1 (address-based) tracking
        should_cleanup = True
        if peer_identity:
            identity_hash = RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]
            if identity_hash in self.spawned_interfaces:
                should_cleanup = False  # Interface still exists
        else:
            # Check if any address-based interface exists
            if f"{address}-central" in self.spawned_interfaces:
                should_cleanup = False

        if should_cleanup:
            with self.frag_lock:
                if address in self.reassemblers:
                    del self.reassemblers[address]
                    RNS.log(f"{self} cleaned up reassembler for {address} (no connections remain)", RNS.LOG_DEBUG)
                if address in self.fragmenters:
                    del self.fragmenters[address]
                    RNS.log(f"{self} cleaned up fragmenter for {address} (no connections remain)", RNS.LOG_DEBUG)

    def process_incoming(self, data):
        """
        Process incoming data from BLE (called by peer interface).

        Args:
            data: Raw packet data
        """
        # This will be called by spawned peer interfaces
        # For now, just pass to owner
        if self.online and self.owner:
            self.rxb += len(data)
            RNS.log(f"{self} RX: {len(data)} bytes from peer interface", RNS.LOG_DEBUG)
            self.owner.inbound(data, self)

    def process_outgoing(self, data):
        """
        Process outgoing data to be sent over BLE.

        WORKAROUND: Transport.py (lines 987-1069) doesn't forward locally-originated packets (hops=0)
        to physical interfaces - they skip the forwarding block entirely. When this method is called
        by Transport, we manually forward to all connected BLE peer interfaces.

        This catches both:
        - Packets that Transport DOES forward (hops>0, received from other interfaces)
        - Packets that Transport DOESN'T forward (hops=0, local programs) - if workaround enabled

        Args:
            data: Raw packet data to transmit
        """
        if not self.online:
            return

        # Get snapshot of peers without holding lock during I/O operations
        # This prevents deadlock when peer_if.process_outgoing() tries to acquire the same lock
        with self.peer_lock:
            peers_to_send = [(address, peer_if) for address, peer_if in self.spawned_interfaces.items() if peer_if.online]

        # Log packet transmission
        RNS.log(f"{self} TX: {len(data)} bytes to {len(peers_to_send)} peer(s)", RNS.LOG_DEBUG)

        # Send to each peer WITHOUT holding the lock (avoid deadlock)
        for address, peer_if in peers_to_send:
            peer_if.process_outgoing(data)

    def detach(self):
        """Detach and shutdown the interface."""
        RNS.log(f"{self} detaching interface", RNS.LOG_INFO)
        self.online = False

        # MEDIUM #4: Graceful shutdown - wait for operations to complete before stopping event loop

        # Stop GATT server gracefully
        if self.gatt_server:
            try:
                future = asyncio.run_coroutine_threadsafe(self.gatt_server.stop(), self.loop)
                future.result(timeout=5.0)  # Wait for graceful shutdown
                RNS.log(f"{self} GATT server stopped", RNS.LOG_DEBUG)
            except Exception as e:
                RNS.log(f"{self} error stopping GATT server: {e}", RNS.LOG_ERROR)

        # Disconnect all peers gracefully
        disconnect_futures = []
        with self.peer_lock:
            for address, (client, last_seen, mtu) in list(self.peers.items()):
                try:
                    future = asyncio.run_coroutine_threadsafe(client.disconnect(), self.loop)
                    disconnect_futures.append((address, future))
                except Exception as e:
                    RNS.log(f"{self} error scheduling disconnect for {address}: {e}", RNS.LOG_ERROR)

            self.peers.clear()

        # Wait for all disconnections (with timeout)
        for address, future in disconnect_futures:
            try:
                future.result(timeout=2.0)
                RNS.log(f"{self} disconnected from {address}", RNS.LOG_DEBUG)
            except Exception as e:
                RNS.log(f"{self} disconnect timeout for {address}: {e}", RNS.LOG_WARNING)

        # Detach spawned interfaces
        for peer_if in list(self.spawned_interfaces.values()):
            peer_if.detach()
        self.spawned_interfaces.clear()

        # Clear fragmentation state
        with self.frag_lock:
            self.fragmenters.clear()
            self.reassemblers.clear()

        # NOW safe to stop event loop (all operations completed)
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
            # Give it a moment to actually stop
            time.sleep(0.1)

        RNS.log(f"{self} detached", RNS.LOG_INFO)

    def should_ingress_limit(self):
        """
        BLE uses point-to-point connections with dedicated channels per peer.
        Ingress limiting is designed for shared-medium interfaces (LoRa, etc.)
        where multiple nodes compete for airtime. Disable for BLE.

        Bug #12 fix: Ingress limiting was holding announces indefinitely,
        preventing them from being validated and processed by Transport.
        """
        return False

    def __str__(self):
        return f"BLEInterface[{self.name}]"


class BLEPeerInterface(Interface):
    """
    Spawned interface representing a single BLE peer connection.

    This follows the pattern used by AutoInterface to create per-peer
    interfaces for routing and statistics tracking.
    """

    def __init__(self, parent, peer_address, peer_name, peer_identity=None):
        """
        Initialize peer interface.

        This interface can now handle BOTH central and peripheral connections
        to the same peer identity, eliminating duplicate interfaces and fixing
        ACK routing issues.

        Args:
            parent: Parent BLEInterface
            peer_address: BLE address of peer
            peer_name: Name of peer device
            peer_identity: 16-byte peer identity from GATT characteristic (optional, can be set later)
        """
        super().__init__()

        self.parent_interface = parent
        self.peer_address = peer_address
        self.peer_name = peer_name
        self.peer_identity = peer_identity  # 16-byte identity for stable tracking
        self.online = True

        # Dual connection state tracking
        self.has_central_connection = False  # True if we connected to them
        self.has_peripheral_connection = False  # True if they connected to us
        self.central_client = None  # BleakClient reference (if central connection exists)
        self.central_mtu = None  # MTU for central connection

        # Copy settings from parent
        self.HW_MTU = parent.HW_MTU
        self.bitrate = parent.bitrate

        # Set interface mode (required by Transport for routing decisions)
        self.mode = Interface.MODE_FULL  # Full mode: can send and receive

        # Announce rate limiting (required by Transport.inbound announce processing)
        self.announce_rate_target = None  # No announce rate limiting for BLE peer interfaces

        RNS.log(f"BLEPeerInterface initialized for {peer_name} ({peer_address}), identity={'set' if peer_identity else 'pending'}", RNS.LOG_DEBUG)

    def add_central_connection(self, client, mtu):
        """
        Add a central connection to this peer interface.

        Called when we successfully connect as a GATT client to this peer.

        Args:
            client: BleakClient instance
            mtu: Negotiated MTU for this connection
        """
        self.has_central_connection = True
        self.central_client = client
        self.central_mtu = mtu
        conn_state = self._get_connection_state_str()
        RNS.log(f"{self} added central connection (MTU: {mtu}), state now: {conn_state}", RNS.LOG_DEBUG)

    def add_peripheral_connection(self):
        """
        Add a peripheral connection to this peer interface.

        Called when this peer connects as a GATT client to our GATT server.
        """
        self.has_peripheral_connection = True
        conn_state = self._get_connection_state_str()
        RNS.log(f"{self} added peripheral connection, state now: {conn_state}", RNS.LOG_DEBUG)

    def remove_central_connection(self):
        """Remove the central connection from this peer interface."""
        if self.has_central_connection:
            self.has_central_connection = False
            self.central_client = None
            self.central_mtu = None
            conn_state = self._get_connection_state_str()
            RNS.log(f"{self} removed central connection, state now: {conn_state}", RNS.LOG_DEBUG)

            # Mark offline if no connections remain
            if not self.has_peripheral_connection:
                self.online = False
                RNS.log(f"{self} no connections remain, marking offline", RNS.LOG_DEBUG)

    def remove_peripheral_connection(self):
        """Remove the peripheral connection from this peer interface."""
        if self.has_peripheral_connection:
            self.has_peripheral_connection = False
            conn_state = self._get_connection_state_str()
            RNS.log(f"{self} removed peripheral connection, state now: {conn_state}", RNS.LOG_DEBUG)

            # Mark offline if no connections remain
            if not self.has_central_connection:
                self.online = False
                RNS.log(f"{self} no connections remain, marking offline", RNS.LOG_DEBUG)

    def _get_connection_state_str(self):
        """Get a string describing the current connection state."""
        if self.has_central_connection and self.has_peripheral_connection:
            return "central+peripheral"
        elif self.has_central_connection:
            return "central only"
        elif self.has_peripheral_connection:
            return "peripheral only"
        else:
            return "no connections"

    def process_incoming(self, data):
        """
        Process incoming data from this peer.

        Args:
            data: Raw bytes received from peer
        """
        if self.online and self.parent_interface.online:
            self.rxb += len(data)
            self.parent_interface.rxb += len(data)

            # Log packet reception
            RNS.log(f"{self} RX: {len(data)} bytes from {self.peer_name}", RNS.LOG_DEBUG)

            # DIAGNOSTIC: Log before calling Transport
            RNS.log(f"DIAGNOSTIC: Calling owner.inbound() with {len(data)} bytes on interface {self}", RNS.LOG_DEBUG)
            RNS.log(f"DIAGNOSTIC: Interface attributes - IN={self.IN}, OUT={self.OUT}, mode={getattr(self, 'mode', 'NOT_SET')}, online={self.online}", RNS.LOG_DEBUG)
            RNS.log(f"DIAGNOSTIC: Packet first bytes (hex): {data[:10].hex()}", RNS.LOG_DEBUG)

            # Pass to Reticulum transport
            self.parent_interface.owner.inbound(data, self)

            RNS.log(f"DIAGNOSTIC: owner.inbound() returned for {self}", RNS.LOG_DEBUG)

    def process_outgoing(self, data):
        """
        Process outgoing data to send to this peer (with fragmentation).

        This method intelligently selects the best available connection path:
        - If both central and peripheral connections exist, prefer central (lower latency)
        - If only one connection exists, use that path
        - Falls back gracefully if one path fails

        Args:
            data: Raw packet data to transmit
        """
        if not self.online:
            return

        # Log packet transmission
        RNS.log(f"{self} TX: {len(data)} bytes to {self.peer_name}", RNS.LOG_DEBUG)

        # Get fragmenter for this peer (using identity-based key for MAC rotation immunity)
        frag_key = self.parent_interface._get_fragmenter_key(self.peer_identity, self.peer_address)

        with self.parent_interface.frag_lock:
            if frag_key not in self.parent_interface.fragmenters:
                RNS.log(f"No fragmenter for peer {self.peer_name} (key: {frag_key})", RNS.LOG_WARNING)
                return

            fragmenter = self.parent_interface.fragmenters[frag_key]

        # Fragment the data
        try:
            fragments = fragmenter.fragment_packet(data)

            if len(fragments) > 1:
                RNS.log(f"Fragmenting {len(data)} byte packet into {len(fragments)} fragments for {self.peer_name}", RNS.LOG_EXTREME)

        except Exception as e:
            RNS.log(f"Failed to fragment data for {self.peer_name}: {e}", RNS.LOG_ERROR)
            return

        # Intelligently route based on available connections
        if self.has_central_connection and self.has_peripheral_connection:
            # Both paths available - prefer central for lower latency
            RNS.log(f"{self} using central path (both connections available)", RNS.LOG_EXTREME)
            success = self._send_via_central(fragments)
            if not success:
                # Fallback to peripheral if central fails
                RNS.log(f"{self} central send failed, falling back to peripheral", RNS.LOG_WARNING)
                self._send_via_peripheral(fragments)
        elif self.has_central_connection:
            # Only central connection available
            RNS.log(f"{self} using central path (only connection)", RNS.LOG_EXTREME)
            self._send_via_central(fragments)
        elif self.has_peripheral_connection:
            # Only peripheral connection available
            RNS.log(f"{self} using peripheral path (only connection)", RNS.LOG_EXTREME)
            self._send_via_peripheral(fragments)
        else:
            RNS.log(f"{self} no connections available for transmission!", RNS.LOG_ERROR)

    def _send_via_peripheral(self, fragments):
        """
        Send fragments via GATT server notifications.

        Args:
            fragments: List of fragment bytes to send

        Returns:
            bool: True if all fragments sent successfully, False otherwise
        """
        if not self.parent_interface.gatt_server:
            RNS.log(f"No GATT server available for {self.peer_name}", RNS.LOG_ERROR)
            return False

        for i, fragment in enumerate(fragments):
            try:
                # Schedule the async notification in the parent's event loop
                future = asyncio.run_coroutine_threadsafe(
                    self.parent_interface.gatt_server.send_notification(fragment, self.peer_address),
                    self.parent_interface.loop
                )

                # Wait for completion (with timeout)
                future.result(timeout=2.0)

                self.txb += len(fragment)
                self.parent_interface.txb += len(fragment)

            except Exception as e:
                RNS.log(f"Failed to send notification {i+1}/{len(fragments)} to {self.peer_name}: {e}", RNS.LOG_ERROR)
                return False

        return True

    def _send_via_central(self, fragments):
        """
        Send fragments via GATT characteristic write (central mode).

        Args:
            fragments: List of fragment bytes to send

        Returns:
            bool: True if all fragments sent successfully, False otherwise
        """
        # Use stored central_client if available (dual-connection architecture)
        client = self.central_client if self.has_central_connection else None

        # Fallback to legacy peers dict lookup (for compatibility during transition)
        if not client:
            with self.parent_interface.peer_lock:
                if self.peer_address not in self.parent_interface.peers:
                    RNS.log(f"{self} peer {self.peer_name} ({self.peer_address}) no longer connected", RNS.LOG_WARNING)
                    return False

                # Get reference to client and release lock immediately
                client, _, _ = self.parent_interface.peers[self.peer_address]

        # Check if client is still connected before sending
        if not client or not client.is_connected:
            RNS.log(f"{self} peer {self.peer_name} ({self.peer_address}) disconnected before transmission", RNS.LOG_WARNING)
            return False

        # Send each fragment via BLE characteristic write
        for i, fragment in enumerate(fragments):
            try:
                # Schedule the async write in the parent's event loop
                future = asyncio.run_coroutine_threadsafe(
                    client.write_gatt_char(BLEInterface.CHARACTERISTIC_RX_UUID, fragment),
                    self.parent_interface.loop
                )

                # Wait for completion (with timeout)
                future.result(timeout=2.0)

                self.txb += len(fragment)
                self.parent_interface.txb += len(fragment)

            except asyncio.TimeoutError:
                RNS.log(f"{self} timeout sending fragment {i+1}/{len(fragments)} to {self.peer_name}, "
                        f"packet lost (Reticulum will retransmit)", RNS.LOG_WARNING)
                return False

            # HIGH #3: Comprehensive asyncio exception handling
            except (asyncio.CancelledError, RuntimeError) as e:
                RNS.log(f"{self} event loop error sending fragment {i+1}/{len(fragments)}: "
                        f"{type(e).__name__}: {e}", RNS.LOG_ERROR)
                # Mark interface as offline if event loop died
                if isinstance(e, RuntimeError) and "closed" in str(e).lower():
                    RNS.log(f"{self} event loop is closed, marking interface offline", RNS.LOG_ERROR)
                    self.parent_interface.online = False
                return False

            except ConnectionError as e:
                RNS.log(f"{self} connection lost to {self.peer_name} while sending fragment {i+1}/{len(fragments)}: "
                        f"{type(e).__name__}: {e}, packet lost", RNS.LOG_WARNING)
                return False

            except Exception as e:
                error_type = type(e).__name__
                RNS.log(f"{self} unexpected exception sending fragment {i+1}/{len(fragments)} to {self.peer_name}: "
                        f"{error_type}: {e}, packet lost (Reticulum will retransmit)", RNS.LOG_WARNING)
                # If one fragment fails, the whole packet is lost
                # Reticulum's upper layers will handle retransmission
                return False

        return True

    def detach(self):
        """Detach this peer interface."""
        self.online = False

        # Remove from transport
        if self in RNS.Transport.interfaces:
            RNS.Transport.interfaces.remove(self)

        RNS.log(f"BLEPeerInterface detached for {self.peer_name}", RNS.LOG_DEBUG)

    def should_ingress_limit(self):
        """Inherit ingress limiting from parent."""
        return self.parent_interface.should_ingress_limit()

    @property
    def connection_id(self):
        """Get the unique connection ID for this peer interface"""
        # For unified interfaces, use identity hash if available, otherwise address
        if self.peer_identity:
            try:
                import RNS
                identity_hash = RNS.Identity.full_hash(self.peer_identity)[:16].hex()[:8]
                return f"{identity_hash}"
            except:
                pass
        return f"{self.peer_address}"

    def __str__(self):
        return f"BLEPeerInterface[{self.peer_name}/{self._get_connection_state_str()}]"


# Register interface for Reticulum
interface_class = BLEInterface
