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

# Import driver abstraction
try:
    from bluetooth_driver import BLEDriverInterface, BLEDevice
except ImportError:
    from RNS.Interfaces.bluetooth_driver import BLEDriverInterface, BLEDevice

# Import platform-specific driver
try:
    from linux_bluetooth_driver import LinuxBluetoothDriver
except ImportError:
    from RNS.Interfaces.linux_bluetooth_driver import LinuxBluetoothDriver

HAS_DRIVER = True

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
    - Driver owns async event loop in separate thread
    - LOCK ORDERING CONVENTION (to prevent deadlocks):
      1. peer_lock - ALWAYS acquire first for peer state access
      2. frag_lock - THEN acquire for fragmentation state
      NEVER acquire locks in reverse order! (HIGH #2: deadlock prevention)
    - Driver callbacks invoked from driver thread

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
        if not HAS_DRIVER:
            raise ImportError(
                "BLEInterface requires the driver abstraction. "
                "Ensure bluetooth_driver.py and linux_bluetooth_driver.py are available."
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

        # Identity-based interface tracking
        self.spawned_interfaces = {}  # identity_hash (16 hex chars) -> BLEPeerInterface
        self.address_to_identity = {}  # address -> peer_identity (16-byte identity)
        self.identity_to_address = {}  # identity_hash -> address (for reverse lookup)

        # Fragmentation
        self.fragmenters = {}  # address -> BLEFragmenter (per MTU)
        self.reassemblers = {}  # address -> BLEReassembler
        self.frag_lock = threading.Lock()

        # Discovery state with prioritization

        # Initialize BLE driver
        self.driver = LinuxBluetoothDriver(
            discovery_interval=self.discovery_interval,
            connection_timeout=self.connection_timeout,
            min_rssi=self.min_rssi,
            service_discovery_delay=self.service_discovery_delay,
            max_peers=self.max_peers,
            adapter_index=0  # TODO: Make configurable
        )

        # Set driver callbacks
        self.driver.on_device_discovered = self._device_discovered_callback
        self.driver.on_device_connected = self._device_connected_callback
        self.driver.on_mtu_negotiated = self._mtu_negotiated_callback
        self.driver.on_data_received = self._data_received_callback
        self.driver.on_device_disconnected = self._device_disconnected_callback
        self.driver.on_error = self._error_callback

        # Set driver power mode
        self.driver.set_power_mode(self.power_mode)

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


        RNS.log(f"{self} initializing with service UUID {self.service_uuid}", RNS.LOG_INFO)
        RNS.log(f"{self} power mode: {self.power_mode}, max peers: {self.max_peers}", RNS.LOG_DEBUG)
        RNS.log(f"{self} central mode: {'ENABLED' if self.enable_central else 'DISABLED'}", RNS.LOG_INFO)
        RNS.log(f"{self} peripheral mode: {'ENABLED' if self.enable_peripheral else 'DISABLED'}", RNS.LOG_INFO)

        # Local announce forwarding status log
        if self.enable_local_announce_forwarding:
            RNS.log(f"{self} local packet forwarding ENABLED (workaround for Transport hops=0 bug)", RNS.LOG_INFO)
        else:
            RNS.log(f"{self} local packet forwarding DISABLED (relies on Transport for propagation)", RNS.LOG_DEBUG)

        # CRITICAL #2: Periodic cleanup task for stale reassembly buffers
        # This prevents memory leaks from incomplete packet transmissions (disconnects, corrupted data)
        # Runs every 30 seconds to clean up timed-out buffers
        self.cleanup_timer = None
        self._start_cleanup_timer()

        # Start the interface
        self.start()

    def start(self):
        """Start the BLE interface operations."""
        RNS.log(f"{self} starting BLE operations", RNS.LOG_INFO)

        # Start the BLE driver
        try:
            self.driver.start(
                service_uuid=self.service_uuid,
                rx_char_uuid=BLEInterface.CHARACTERISTIC_RX_UUID,
                tx_char_uuid=BLEInterface.CHARACTERISTIC_TX_UUID,
                identity_char_uuid=BLEInterface.CHARACTERISTIC_IDENTITY_UUID
            )
            RNS.log(f"{self} driver started successfully", RNS.LOG_INFO)
        except Exception as e:
            RNS.log(f"{self} failed to start driver: {e}", RNS.LOG_ERROR)
            return


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
        loaded, then sets it on the driver and starts advertising.
        """
        if self.enable_peripheral:
            RNS.log(f"{self} Launching driver advertising startup thread (will wait for Transport.identity)", RNS.LOG_DEBUG)
            startup_thread = threading.Thread(target=self._start_advertising_when_identity_ready, daemon=True, name="BLE-Advertising-Startup")
            startup_thread.start()

    def _start_advertising_when_identity_ready(self):
        """
        Background thread that waits for Transport.identity, sets it on driver,
        then starts advertising. Times out after 60 seconds if identity doesn't load.
        """
        import RNS.Transport as Transport

        attempt = 0
        start_time = time.time()
        timeout = 60.0  # 60 second timeout

        RNS.log(f"{self} Waiting for Transport.identity to be loaded...", RNS.LOG_DEBUG)

        # Poll until Transport.identity is available (with 60s timeout)
        while time.time() - start_time < timeout:
            attempt += 1

            try:
                if hasattr(Transport, 'identity') and Transport.identity:
                    identity_hash = Transport.identity.hash
                    if identity_hash and len(identity_hash) == 16:
                        elapsed = time.time() - start_time
                        RNS.log(f"{self} Transport.identity available after {elapsed:.1f}s", RNS.LOG_INFO)

                        # Generate identity-based device name if not configured
                        if self.device_name is None:
                            identity_str = identity_hash.hex()  # Full 16 bytes as 32 hex chars
                            self.device_name = f"RNS-{identity_str}"
                            RNS.log(f"{self} Auto-generated identity-based device name: {self.device_name}", RNS.LOG_INFO)

                        # Set identity on driver
                        self.driver.set_identity(identity_hash)

                        # Start advertising
                        try:
                            self.driver.start_advertising(self.device_name, identity_hash)
                            RNS.log(f"{self} Started advertising as {self.device_name}", RNS.LOG_INFO)
                        except Exception as e:
                            RNS.log(f"{self} Failed to start advertising: {e}", RNS.LOG_ERROR)

                        return

            except Exception as e:
                RNS.log(f"{self} Error waiting for identity: {e}", RNS.LOG_DEBUG)

            time.sleep(0.5)

        RNS.log(f"{self} Timeout waiting for Transport.identity after {timeout}s", RNS.LOG_ERROR)


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

    def _start_cleanup_timer(self):
        """
        Start the periodic cleanup timer.

        CRITICAL #2: This timer prevents memory leaks from incomplete reassembly buffers
        caused by peer disconnections or corrupted partial transmissions.
        """
        if self.cleanup_timer:
            self.cleanup_timer.cancel()

        self.cleanup_timer = threading.Timer(30.0, self._periodic_cleanup_task)
        self.cleanup_timer.daemon = True
        self.cleanup_timer.start()

    def _periodic_cleanup_task(self):
        """
        Periodically clean up stale reassembly buffers (CRITICAL #2: prevent memory leak)

        This task runs every 30 seconds to remove incomplete packet reassembly buffers
        that have timed out. Without this, failed transmissions would leave buffers in
        memory indefinitely, leading to memory exhaustion on long-running instances
        (especially critical on Pi Zero with only 512MB RAM).
        """
        if not self.online:
            return  # Don't reschedule if interface is offline

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

        # Reschedule for next cleanup cycle
        self._start_cleanup_timer()

    def _device_discovered_callback(self, device: BLEDevice):
        """
        Driver callback: Handle discovered BLE device.

        This callback is invoked by the driver when a device is discovered during scanning.
        We use peer scoring and connection logic to decide whether to connect.
        """
        # Update or create discovered peer entry
        if device.address not in self.discovered_peers:
            self.discovered_peers[device.address] = DiscoveredPeer(
                address=device.address,
                name=device.name,
                rssi=device.rssi
            )
        else:
            self.discovered_peers[device.address].update_rssi(device.rssi)

        # Prune discovery cache if needed (HIGH #4)
        if len(self.discovered_peers) > self.max_discovered_peers:
            # Remove oldest entries by last_seen timestamp
            sorted_peers = sorted(
                self.discovered_peers.items(),
                key=lambda x: x[1].last_seen
            )
            to_remove = sorted_peers[:-self.max_discovered_peers]
            for addr, _ in to_remove:
                del self.discovered_peers[addr]

        # Decide whether to connect based on peer scoring
        peers_to_connect = self._select_peers_to_connect()
        if device.address in [p.address for p in peers_to_connect]:
            # Initiate connection via driver
            try:
                self.driver.connect(device.address)
            except Exception as e:
                RNS.log(f"{self} failed to initiate connection to {device.name}: {e}", RNS.LOG_ERROR)

    def _device_connected_callback(self, address: str):
        """
        Driver callback: Handle successful device connection.

        Called when driver has established a connection. We read the identity
        characteristic and prepare to receive data.
        """
        # Check connection role to determine identity exchange method
        role = self.driver.get_peer_role(address)

        if role == "central":
            # We are the central, we must read the peer's identity
            RNS.log(f"{self} connected to {address} as CENTRAL, reading identity...", RNS.LOG_INFO)
            try:
                identity_bytes = self.driver.read_characteristic(
                    address,
                    BLEInterface.CHARACTERISTIC_IDENTITY_UUID
                )

                if identity_bytes and len(identity_bytes) == 16:
                    peer_identity = bytes(identity_bytes)
                    identity_hash = self._compute_identity_hash(peer_identity)

                    # Store identity mappings
                    self.address_to_identity[address] = peer_identity
                    self.identity_to_address[identity_hash] = address

                    RNS.log(f"{self} received peer identity from {address}: {identity_hash}", RNS.LOG_INFO)
                    self._record_connection_success(address)
                else:
                    RNS.log(f"{self} invalid identity from {address}, disconnecting", RNS.LOG_WARNING)
                    self.driver.disconnect(address)
                    self._record_connection_failure(address)

            except Exception as e:
                RNS.log(f"{self} failed to read identity from {address}: {e}", RNS.LOG_ERROR)
                self.driver.disconnect(address)
                self._record_connection_failure(address)

        elif role == "peripheral":
            # We are the peripheral, we must wait for the central to send its identity
            RNS.log(f"{self} connected to {address} as PERIPHERAL, waiting for identity handshake...", RNS.LOG_INFO)
            # The identity will be received in `handle_peripheral_data` or `_data_received_callback`
            # No action is needed here.
            pass

        else:
            RNS.log(f"{self} connected to {address}, but role is unknown. Disconnecting.", RNS.LOG_WARNING)
            self.driver.disconnect(address)

    def _mtu_negotiated_callback(self, address: str, mtu: int):
        """
        Driver callback: Handle MTU negotiation completion.

        Creates or updates the fragmenter for this peer with the negotiated MTU.
        """
        RNS.log(f"{self} MTU negotiated with {address}: {mtu} bytes", RNS.LOG_INFO)

        # Get peer identity
        peer_identity = self.address_to_identity.get(address)
        if not peer_identity:
            RNS.log(f"{self} no identity for {address}, cannot create fragmenter", RNS.LOG_WARNING)
            return

        # Create or update fragmenter
        frag_key = self._get_fragmenter_key(peer_identity, address)

        with self.frag_lock:
            # Create fragmenter with MTU
            self.fragmenters[frag_key] = BLEFragmenter(mtu=mtu)

            # Create reassembler if not exists
            if frag_key not in self.reassemblers:
                self.reassemblers[frag_key] = BLEReassembler()

        # Spawn peer interface if not exists
        identity_hash = self._compute_identity_hash(peer_identity)
        if identity_hash not in self.spawned_interfaces:
            # Get peer name from discovered peers
            peer_name = None
            if address in self.discovered_peers:
                peer_name = self.discovered_peers[address].name
            else:
                peer_name = f"BLE-{address[-8:]}"

            # Determine connection type based on MAC sorting
            connection_type = "central"
            if self.driver.get_local_address():
                local_mac = self.driver.get_local_address().lower()
                peer_mac = address.lower()
                if local_mac > peer_mac:
                    connection_type = "peripheral"

            self._spawn_peer_interface(
                address=address,
                name=peer_name,
                peer_identity=peer_identity,
                mtu=mtu,
                connection_type=connection_type
            )

    def _data_received_callback(self, address: str, data: bytes):
        """
        Driver callback: Handle received data from peer.

        Passes data to reassembly and routing logic.
        """
        self._handle_ble_data(address, data)

    def _device_disconnected_callback(self, address: str):
        """
        Driver callback: Handle device disconnection.

        Cleans up peer state, interfaces, and fragmentation buffers.
        """
        RNS.log(f"{self} disconnected from {address}", RNS.LOG_INFO)

        # Clean up peer connection state
        with self.peer_lock:
            if address in self.peers:
                del self.peers[address]

        # Detach interface
        peer_identity = self.address_to_identity.get(address)
        if peer_identity:
            identity_hash = self._compute_identity_hash(peer_identity)
            if identity_hash in self.spawned_interfaces:
                peer_if = self.spawned_interfaces[identity_hash]
                peer_if.detach()
                del self.spawned_interfaces[identity_hash]
                RNS.log(f"{self} detached interface for {address}", RNS.LOG_DEBUG)

        # Clean up fragmenter/reassembler
        if peer_identity:
            frag_key = self._get_fragmenter_key(peer_identity, address)
            with self.frag_lock:
                if frag_key in self.fragmenters:
                    del self.fragmenters[frag_key]
                if frag_key in self.reassemblers:
                    del self.reassemblers[frag_key]

    def _error_callback(self, severity: str, message: str, exc: Exception = None):
        """
        Driver callback: Handle driver errors.

        Logs errors with appropriate severity level.
        """
        if severity == "critical":
            log_level = RNS.LOG_CRITICAL
        elif severity == "error":
            log_level = RNS.LOG_ERROR
        elif severity == "warning":
            log_level = RNS.LOG_WARNING
        else:
            log_level = RNS.LOG_DEBUG

        if exc:
            RNS.log(f"{self} driver {severity}: {message} - {type(exc).__name__}: {exc}", log_level)
        else:
            RNS.log(f"{self} driver {severity}: {message}", log_level)

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

            # Protocol v2.2: Skip if interface exists for this identity (any connection type)
            # This prevents dual connections (central + peripheral to same peer)
            peer_identity = self.address_to_identity.get(address)
            if peer_identity:
                identity_hash = self._compute_identity_hash(peer_identity)
                if identity_hash in self.spawned_interfaces:
                    RNS.log(f"{self} [v2.2] skipping {peer.name} - interface exists for identity {identity_hash[:8]}",
                            RNS.LOG_DEBUG)
                    continue

            # Protocol v2.2: MAC address sorting - deterministic connection direction
            # Lower MAC initiates (central), higher MAC only accepts (peripheral)
            # This prevents simultaneous connection attempts from both sides
            if self.local_address is not None:
                try:
                    # Normalize addresses (remove colons)
                    my_mac = self.local_address.replace(":", "")
                    peer_mac = address.replace(":", "")

                    my_mac_int = int(my_mac, 16)
                    peer_mac_int = int(peer_mac, 16)

                    if my_mac_int > peer_mac_int:
                        # Our MAC is higher - let them connect to us (we stay peripheral only)
                        RNS.log(f"{self} [v2.2] skipping {peer.name} (MAC {address[:17]}) - "
                                f"connection direction: they initiate (lower MAC connects to higher)",
                                RNS.LOG_DEBUG)
                        continue
                except (ValueError, AttributeError) as e:
                    # MAC parsing failed - fall through to normal connection logic
                    RNS.log(f"{self} MAC sorting failed for {peer.name}: {e}", RNS.LOG_DEBUG)

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

    def _get_fragmenter_key(self, peer_identity, peer_address):
        """
        Compute fragmenter/reassembler dictionary key using identity hash.

        Args:
            peer_identity: 16-byte peer identity
            peer_address: BLE MAC address (unused, kept for compatibility)

        Returns:
            str: Identity hash (16 hex chars)
        """
        return RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]

    def _compute_identity_hash(self, peer_identity):
        """
        Compute 16-character hex identity hash for interface tracking.

        Args:
            peer_identity: 16-byte peer identity

        Returns:
            str: Identity hash (16 hex chars)
        """
        return RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]

    def _spawn_peer_interface(self, address, name, peer_identity, client=None, mtu=None, connection_type="central"):
        """
        Create a peer interface for a BLE connection.

        Args:
            address: BLE address of peer
            name: Name of peer device
            peer_identity: 16-byte peer identity
            client: BleakClient instance (for central connections)
            mtu: Negotiated MTU (for central connections)
            connection_type: "central" (we connected to them) or "peripheral" (they connected to us)

        Returns:
            BLEPeerInterface: The spawned interface
        """
        # Compute lookup key using identity hash
        identity_hash = self._compute_identity_hash(peer_identity)

        # Check if interface already exists (MAC sorting should prevent this)
        if identity_hash in self.spawned_interfaces:
            RNS.log(f"{self} interface already exists for {name} ({identity_hash[:8]}), reusing", RNS.LOG_WARNING)
            return self.spawned_interfaces[identity_hash]

        # Create new peer interface
        peer_if = BLEPeerInterface(self, address, name, peer_identity)
        peer_if.OUT = self.OUT
        peer_if.IN = self.IN
        peer_if.parent_interface = self
        peer_if.bitrate = self.bitrate
        peer_if.HW_MTU = self.HW_MTU
        peer_if.online = True

        # Register with transport
        RNS.Transport.interfaces.append(peer_if)

        # Store in tracking dict
        self.spawned_interfaces[identity_hash] = peer_if

        RNS.log(f"{self} created peer interface for {name} ({identity_hash[:8]}), type={connection_type}", RNS.LOG_INFO)

        return peer_if

    def _handle_ble_data(self, peer_address, data):
        """
        Handle incoming BLE data from a peer (may be fragment).

        Args:
            peer_address: Address of peer that sent data
            data: Raw bytes received (might be fragment)
        """
        RNS.log(f"{self} received {len(data)} bytes from peer {peer_address}", RNS.LOG_EXTREME)

        # Look up peer identity to compute fragmenter key
        peer_identity = self.address_to_identity.get(peer_address)
        if not peer_identity:
            RNS.log(f"{self} no identity for peer {peer_address}, dropping data", RNS.LOG_WARNING)
            return

        # Compute identity-based fragmenter key (matches peripheral data handler)
        frag_key = self._get_fragmenter_key(peer_identity, peer_address)

        # Attempt reassembly
        complete_packet = None
        peer_name = None

        # HIGH #2: Lock ordering - get reassembler reference with frag_lock, release before processing
        # This prevents holding frag_lock during reassembly which could block other threads
        with self.frag_lock:
            if frag_key not in self.reassemblers:
                RNS.log(f"{self} no reassembler for {peer_address} (key: {frag_key[:16]}), dropping data", RNS.LOG_WARNING)
                return
            reassembler = self.reassemblers[frag_key]

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
                # Get peer name from interface lookup
                peer_identity = self.address_to_identity.get(peer_address, None)

                peer_name = peer_address[-8:]  # Default to address
                if peer_identity:
                    identity_hash = self._compute_identity_hash(peer_identity)
                    peer_if = self.spawned_interfaces.get(identity_hash, None)
                    if peer_if:
                        peer_name = peer_if.peer_name

                RNS.log(f"{self} reassembled packet from {peer_name}: "
                        f"total_packets={stats['packets_reassembled']}, "
                        f"total_fragments={stats['fragments_received']}, "
                        f"pending={stats['pending_packets']}, "
                        f"timeouts={stats['packets_timeout']}", RNS.LOG_DEBUG)

        except Exception as e:
            RNS.log(f"{self} error reassembling fragment from {peer_address}: {type(e).__name__}: {e}", RNS.LOG_ERROR)
            return

        # If we have a complete packet, route to peer interface
        if complete_packet:
            peer_identity = self.address_to_identity.get(peer_address, None)

            if not peer_identity:
                RNS.log(f"{self} no identity for peer {peer_address}, packet dropped", RNS.LOG_WARNING)
                return

            identity_hash = self._compute_identity_hash(peer_identity)
            peer_if = self.spawned_interfaces.get(identity_hash, None)

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

        # Check if we have peer identity
        peer_identity = self.address_to_identity.get(sender_address)

        # Identity handshake detection: If no identity and exactly 16 bytes, treat as handshake
        # Protocol: Central sends its 16-byte identity hash as first packet after connection
        if not peer_identity and len(data) == 16:
            try:
                # Store central's identity
                central_identity = bytes(data)
                central_identity_hash = RNS.Identity.full_hash(central_identity)[:16].hex()[:16]

                self.address_to_identity[sender_address] = central_identity
                self.identity_to_address[central_identity_hash] = sender_address

                RNS.log(f"{self} received identity handshake from central {sender_address}: {central_identity_hash}", RNS.LOG_INFO)
                RNS.log(f"{self} stored identity mapping for {sender_address}", RNS.LOG_DEBUG)

                # Create peer interface and fragmenter/reassembler now that we have identity
                self._spawn_peer_interface(
                    address=sender_address,
                    name=f"Central-{sender_address[-8:]}",
                    peer_identity=central_identity,
                    client=None,  # No client for peripheral connections
                    mtu=None,  # MTU managed by GATT server
                    connection_type="peripheral"
                )

                # Create fragmenter/reassembler for this peer
                frag_key = self._get_fragmenter_key(central_identity, sender_address)
                with self.frag_lock:
                    # Use default MTU for peripheral connections (GATT server manages MTU)
                    # The actual MTU will be determined by the central device
                    mtu = 23  # BLE 4.0 minimum MTU
                    self.fragmenters[frag_key] = BLEFragmenter(mtu=mtu)
                    self.reassemblers[frag_key] = BLEReassembler(timeout=self.connection_timeout)
                RNS.log(f"{self} created fragmenter/reassembler for central (key: {frag_key[:16]})", RNS.LOG_DEBUG)

                return  # Handshake processed, done
            except Exception as e:
                RNS.log(f"{self} failed to process identity handshake from {sender_address}: {type(e).__name__}: {e}", RNS.LOG_ERROR)
                return

        # If still no identity after handshake check, drop the data
        if not peer_identity:
            RNS.log(f"{self} no identity for central {sender_address}, dropping data", RNS.LOG_WARNING)
            return

        # Get fragmenter key
        frag_key = self._get_fragmenter_key(peer_identity, sender_address)

        # Attempt reassembly
        complete_packet = None
        with self.frag_lock:
            if frag_key not in self.reassemblers:
                RNS.log(f"{self} no reassembler for {sender_address}, dropping data", RNS.LOG_WARNING)
                return

            reassembler = self.reassemblers[frag_key]

        try:
            # Ensure data is bytes (bluezero may pass different types)
            data_bytes = bytes(data) if not isinstance(data, bytes) else data
            complete_packet = reassembler.receive_fragment(data_bytes, sender_address)

            # Periodic cleanup
            if complete_packet:
                cleaned = reassembler.cleanup_stale_buffers()
                if cleaned > 0:
                    RNS.log(f"{self} cleaned {cleaned} stale reassembly buffers for {sender_address}", RNS.LOG_DEBUG)

        except Exception as e:
            RNS.log(f"{self} error reassembling fragment from {sender_address}: {type(e).__name__}: {e}", RNS.LOG_ERROR)
            return

        # Route complete packet to interface
        if complete_packet:
            identity_hash = self._compute_identity_hash(peer_identity)
            peer_if = self.spawned_interfaces.get(identity_hash)

            if peer_if:
                peer_if.process_incoming(complete_packet)
            else:
                RNS.log(f"{self} no interface for {sender_address}, packet dropped", RNS.LOG_WARNING)

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

        # Register with transport
        RNS.Transport.interfaces.append(peer_if)

        # Note: No tunnel registration needed - direct peer connections use
        # RNS.Transport.interfaces[] only (same pattern as I2PInterface)

        self.spawned_interfaces[conn_id] = peer_if

        # Create fragmenter using negotiated MTU from GATT server (if available)
        # Fragmenters are keyed by ADDRESS (shared between central and peripheral connections)
        # Note: MTU will be set via _mtu_negotiated_callback when driver reports it
        with self.frag_lock:
            if address not in self.fragmenters:
                # Use default MTU until negotiation completes
                mtu = 185  # Default fallback
                RNS.log(f"{self} creating fragmenter with default MTU {mtu}, will update when negotiated", RNS.LOG_DEBUG)
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

        # Look up peer identity
        # Identity should be available via:
        #   1. Discovery: If we previously scanned and discovered this central
        #   2. Handshake: Central will send 16-byte identity as first write to RX characteristic
        # At this point (connection established), we may not have identity yet - it arrives via handshake
        peer_identity = self.address_to_identity.get(address, None)

        if not peer_identity:
            RNS.log(f"{self} peer identity not yet available for {address} (will be provided via handshake)", RNS.LOG_DEBUG)
            # Don't create interface yet - wait for identity handshake in handle_peripheral_data()
            return

        # Create peer interface with peripheral connection
        self._spawn_peer_interface(
            address=address,
            name=f"Central-{address[-8:]}",
            peer_identity=peer_identity,
            client=None,  # No client for peripheral connections
            mtu=None,  # MTU managed by GATT server
            connection_type="peripheral"
        )

    def handle_central_disconnected(self, address):
        """
        Handle a central device disconnecting from our GATT server.

        Args:
            address: BLE address of the central device
        """
        RNS.log(f"{self} central disconnected: {address}", RNS.LOG_INFO)

        # Look up peer identity
        peer_identity = self.address_to_identity.get(address, None)

        if not peer_identity:
            RNS.log(f"{self} no identity for disconnected central {address}", RNS.LOG_WARNING)
            return

        # Find and detach interface
        identity_hash = self._compute_identity_hash(peer_identity)
        if identity_hash in self.spawned_interfaces:
            peer_if = self.spawned_interfaces[identity_hash]
            peer_if.detach()
            del self.spawned_interfaces[identity_hash]
            RNS.log(f"{self} detached interface for {address}", RNS.LOG_DEBUG)

            # Clean up fragmenter/reassembler
            frag_key = self._get_fragmenter_key(peer_identity, address)
            with self.frag_lock:
                if frag_key in self.reassemblers:
                    del self.reassemblers[frag_key]
                    RNS.log(f"{self} cleaned up reassembler for {address}", RNS.LOG_DEBUG)
                if frag_key in self.fragmenters:
                    del self.fragmenters[frag_key]
                    RNS.log(f"{self} cleaned up fragmenter for {address}", RNS.LOG_DEBUG)

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

        # Cancel periodic cleanup timer
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
            self.cleanup_timer = None

        # Detach spawned interfaces
        for peer_if in list(self.spawned_interfaces.values()):
            peer_if.detach()
        self.spawned_interfaces.clear()

        # Clear fragmentation state
        with self.frag_lock:
            self.fragmenters.clear()
            self.reassemblers.clear()

        # Stop the driver (handles graceful disconnection and cleanup)
        try:
            self.driver.stop()
            RNS.log(f"{self} driver stopped", RNS.LOG_DEBUG)
        except Exception as e:
            RNS.log(f"{self} error stopping driver: {e}", RNS.LOG_ERROR)

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

        Args:
            parent: Parent BLEInterface
            peer_address: BLE address of peer
            peer_name: Name of peer device
            peer_identity: 16-byte peer identity from GATT characteristic (optional, can be set later)

        Note: Connection type (central vs peripheral) and MTU are now managed by the driver.
        """
        super().__init__()

        self.parent_interface = parent
        self.peer_address = peer_address
        self.peer_name = peer_name
        self.peer_identity = peer_identity  # 16-byte identity for stable tracking
        self.online = True

        # Copy settings from parent
        self.HW_MTU = parent.HW_MTU
        self.bitrate = parent.bitrate

        # Set interface mode (required by Transport for routing decisions)
        self.mode = Interface.MODE_FULL  # Full mode: can send and receive

        # Announce rate limiting (required by Transport.inbound announce processing)
        self.announce_rate_target = None  # No announce rate limiting for BLE peer interfaces

        RNS.log(f"BLEPeerInterface initialized for {peer_name} ({peer_address}), identity={'set' if peer_identity else 'pending'}", RNS.LOG_DEBUG)

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

            # Pass to Reticulum transport
            self.parent_interface.owner.inbound(data, self)

    def process_outgoing(self, data):
        """
        Process outgoing data to send to this peer (with fragmentation).

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

        # Send fragments via driver (driver handles role-aware routing)
        for i, fragment in enumerate(fragments):
            try:
                self.parent_interface.driver.send(self.peer_address, fragment)

                self.txb += len(fragment)
                self.parent_interface.txb += len(fragment)

            except Exception as e:
                RNS.log(f"Failed to send fragment {i+1}/{len(fragments)} to {self.peer_name}: {e}", RNS.LOG_ERROR)
                return

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
        return f"BLEPeerInterface[{self.peer_name}]"


# Register interface for Reticulum
interface_class = BLEInterface
