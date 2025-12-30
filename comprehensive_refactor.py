#!/usr/bin/env python3
"""
Comprehensive refactoring script for BLEInterface.py to use driver abstraction.

This script:
1. Removes platform-specific imports (bleak, bluezero, dbus_fast, monkey patch)
2. Adds driver abstraction imports
3. Refactors __init__ to create and configure driver
4. Removes async methods moved to driver
5. Adds driver callback implementations
6. Updates BLE operations to use driver calls
"""

import re

def read_file(path):
    with open(path, 'r') as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)

def remove_imports_and_add_driver_imports(content):
    """Remove bleak/bluezero/monkey patch, add driver imports."""

    # Find the section to replace (from "# Check for bleak" to end of monkey patch)
    pattern = r'# Check for bleak dependency.*?(?=class DiscoveredPeer)'

    replacement = '''# Import driver abstraction
try:
    from bluetooth_driver import BLEDriverInterface, BLEDevice
except ImportError:
    try:
        from RNS.Interfaces.bluetooth_driver import BLEDriverInterface, BLEDevice
    except ImportError:
        # Fallback to root directory
        import sys
        import os
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
        from bluetooth_driver import BLEDriverInterface, BLEDevice

# Import platform-specific driver
try:
    from linux_bluetooth_driver import LinuxBluetoothDriver
except ImportError:
    try:
        from RNS.Interfaces.linux_bluetooth_driver import LinuxBluetoothDriver
    except ImportError:
        # Fallback to root directory
        import sys
        import os
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
        from linux_bluetooth_driver import LinuxBluetoothDriver

HAS_DRIVER = True

'''

    content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    return content

def remove_method(content, method_name):
    """Remove a method definition entirely."""
    # Pattern to match method definition and its body
    # Match from "def method_name" or "async def method_name" until the next method/class definition
    pattern = rf'^(    )(async )?def {method_name}\(.*?\n((?:(?!\1(?:def|async def|class)\b).*\n)*)'
    content = re.sub(pattern, '', content, flags=re.MULTILINE)
    return content

def refactor_init_method(content):
    """Refactor __init__ to use driver abstraction."""

    # Replace HAS_BLEAK check with HAS_DRIVER
    content = content.replace(
        'if not HAS_BLEAK:\n            raise ImportError(\n                "BLEInterface requires the \'bleak\' library. "\n                "Install with: pip install bleak==1.1.1"\n            )',
        'if not HAS_DRIVER:\n            raise ImportError(\n                "BLEInterface requires the driver abstraction. "\n                "Ensure bluetooth_driver.py and linux_bluetooth_driver.py are available."\n            )'
    )

    # Remove GATT server creation section (lines starting with "# GATT server for peripheral mode" until "# Fragmentation")
    pattern = r'        # GATT server for peripheral mode.*?(?=        # Fragmentation)'
    content = re.sub(pattern, '', content, flags=re.DOTALL)

    # Remove async loop setup (lines starting with "# Async event loop" until "# Discovery state")
    pattern = r'        # Async event loop.*?(?=        # Discovery state)'
    content = re.sub(pattern, '', content, flags=re.DOTALL)

    # Remove BlueZ version detection
    content = content.replace(
        '        # BlueZ version and capabilities (for LE-specific connection support)\n        self.bluez_version = self._detect_bluez_version()\n        self.has_connect_device = False  # Set to True if ConnectDevice() available\n',
        ''
    )

    # Add driver creation after fragmentation section
    driver_init = '''
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
'''

    # Insert after "# Discovery state with prioritization" line
    content = content.replace(
        '        # Discovery state with prioritization\n',
        '        # Discovery state with prioritization\n' + driver_init + '\n'
    )

    return content

def add_driver_callbacks(content):
    """Add driver callback implementations after _periodic_cleanup method."""

    callbacks = '''
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
        RNS.log(f"{self} connected to {address}, reading identity...", RNS.LOG_INFO)

        # Read identity characteristic
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

                # Record successful connection
                self._record_connection_success(address)

            else:
                RNS.log(f"{self} invalid identity from {address}, disconnecting", RNS.LOG_WARNING)
                self.driver.disconnect(address)
                self._record_connection_failure(address)

        except Exception as e:
            RNS.log(f"{self} failed to read identity from {address}: {e}", RNS.LOG_ERROR)
            self.driver.disconnect(address)
            self._record_connection_failure(address)

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
'''

    # Insert callbacks after _periodic_cleanup method
    # Find the end of _periodic_cleanup (next method definition)
    pattern = r'(    async def _periodic_cleanup\(self\):.*?(?=\n    def ))'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        insert_pos = match.end()
        content = content[:insert_pos] + '\n' + callbacks + content[insert_pos:]

    return content

def refactor_start_method(content):
    """Refactor start() method to use driver."""

    # Replace loop thread creation with driver start
    old_start = r'        # Create and start async event loop in separate thread\s+self\.loop_thread = threading\.Thread\(target=self\._run_async_loop, daemon=True\)\s+self\.loop_thread\.start\(\)\s+# Wait for loop to initialize.*?return'

    new_start = '''        # Start the BLE driver
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
            return'''

    content = re.sub(old_start, new_start, content, flags=re.DOTALL)

    # Remove discovery and cleanup task scheduling
    content = content.replace(
        '        # Schedule discovery to start (if central mode enabled)\n        if self.enable_central:\n            asyncio.run_coroutine_threadsafe(self._start_discovery(), self.loop)\n        else:\n            RNS.log(f"{self} central mode disabled, skipping peer discovery", RNS.LOG_INFO)\n\n        # Start periodic cleanup task (CRITICAL #2: prevent unbounded reassembly buffer growth)\n        asyncio.run_coroutine_threadsafe(self._periodic_cleanup(), self.loop)\n',
        ''
    )

    return content

def refactor_final_init(content):
    """Refactor final_init() to set identity on driver and start advertising."""

    old_final_init = r'    def final_init\(self\):.*?(?=\n    def _start_gatt_when_identity_ready)'

    new_final_init = '''    def final_init(self):
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
'''

    content = re.sub(old_final_init, new_final_init, content, flags=re.DOTALL)

    return content

def main():
    input_file = 'src/RNS/Interfaces/BLEInterface.py'

    print("Reading file...")
    content = read_file(input_file)

    print("Step 1: Removing imports and adding driver imports...")
    content = remove_imports_and_add_driver_imports(content)

    print("Step 2: Removing async methods moved to driver...")
    methods_to_remove = [
        '_run_async_loop',
        '_detect_bluez_version',
        '_log_bluez_config',
        '_connect_via_dbus_le',
        '_get_local_adapter_address',
        '_start_discovery',
        '_start_server',
        '_discover_peers',
        '_connect_to_peer'
    ]
    for method in methods_to_remove:
        print(f"  Removing {method}...")
        content = remove_method(content, method)

    print("Step 3: Refactoring __init__ method...")
    content = refactor_init_method(content)

    print("Step 4: Refactoring start() method...")
    content = refactor_start_method(content)

    print("Step 5: Refactoring final_init() method...")
    content = refactor_final_init(content)

    print("Step 6: Adding driver callbacks...")
    content = add_driver_callbacks(content)

    print("Writing refactored file...")
    write_file(input_file, content)

    print("Done! Refactoring complete.")
    print("\nManual review needed for:")
    print("  - BLEPeerInterface._send_via_central() and _send_via_peripheral()")
    print("  - Any remaining bleak/bluezero references")
    print("  - Local address retrieval (now driver.get_local_address())")

if __name__ == '__main__':
    main()
