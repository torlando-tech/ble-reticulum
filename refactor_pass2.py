#!/usr/bin/env python3
"""
Second pass refactoring: Replace remaining BLE operations with driver calls.
"""

import re

def read_file(path):
    with open(path, 'r') as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)

def refactor_detach_method(content):
    """Replace async operations in detach() with driver.stop()."""

    old_detach = r'''    def detach\(self\):
        """Detach and shutdown the interface\."""
        RNS\.log\(f"\{self\} detaching interface", RNS\.LOG_INFO\)
        self\.online = False

        # MEDIUM #4: Graceful shutdown - wait for operations to complete before stopping event loop

        # Stop GATT server gracefully
        if self\.gatt_server:
            try:
                future = asyncio\.run_coroutine_threadsafe\(self\.gatt_server\.stop\(\), self\.loop\)
                future\.result\(timeout=5\.0\)  # Wait for graceful shutdown
                RNS\.log\(f"\{self\} GATT server stopped", RNS\.LOG_DEBUG\)
            except Exception as e:
                RNS\.log\(f"\{self\} error stopping GATT server: \{e\}", RNS\.LOG_ERROR\)

        # Disconnect all peers gracefully
        disconnect_futures = \[\]
        with self\.peer_lock:
            for address, \(client, last_seen, mtu\) in list\(self\.peers\.items\(\)\):
                try:
                    future = asyncio\.run_coroutine_threadsafe\(client\.disconnect\(\), self\.loop\)
                    disconnect_futures\.append\(\(address, future\)\)
                except Exception as e:
                    RNS\.log\(f"\{self\} error scheduling disconnect for \{address\}: \{e\}", RNS\.LOG_ERROR\)

            self\.peers\.clear\(\)

        # Wait for all disconnections \(with timeout\)
        for address, future in disconnect_futures:
            try:
                future\.result\(timeout=2\.0\)
                RNS\.log\(f"\{self\} disconnected from \{address\}", RNS\.LOG_DEBUG\)
            except Exception as e:
                RNS\.log\(f"\{self\} disconnect timeout for \{address\}: \{e\}", RNS\.LOG_WARNING\)

        # Detach spawned interfaces
        for peer_if in list\(self\.spawned_interfaces\.values\(\)\):
            peer_if\.detach\(\)
        self\.spawned_interfaces\.clear\(\)

        # Clear fragmentation state
        with self\.frag_lock:
            self\.fragmenters\.clear\(\)
            self\.reassemblers\.clear\(\)

        # NOW safe to stop event loop \(all operations completed\)
        if self\.loop:
            self\.loop\.call_soon_threadsafe\(self\.loop\.stop\)
            # Give it a moment to actually stop
            time\.sleep\(0\.1\)

        RNS\.log\(f"\{self\} detached", RNS\.LOG_INFO\)'''

    new_detach = '''    def detach(self):
        """Detach and shutdown the interface."""
        RNS.log(f"{self} detaching interface", RNS.LOG_INFO)
        self.online = False

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

        RNS.log(f"{self} detached", RNS.LOG_INFO)'''

    content = re.sub(old_detach, new_detach, content)
    return content

def refactor_send_methods(content):
    """Replace asyncio operations in _send_via_central and _send_via_peripheral with driver.send()."""

    # Replace _send_via_peripheral
    old_peripheral = r'''    def _send_via_peripheral\(self, fragments\):
        """
        Send fragments via GATT server notifications\.

        Args:
            fragments: List of fragment bytes to send

        Returns:
            bool: True if all fragments sent successfully, False otherwise
        """
        if not self\.parent_interface\.gatt_server:
            RNS\.log\(f"No GATT server available for \{self\.peer_name\}", RNS\.LOG_ERROR\)
            return False

        for i, fragment in enumerate\(fragments\):
            try:
                # Schedule the async notification in the parent's event loop
                future = asyncio\.run_coroutine_threadsafe\(
                    self\.parent_interface\.gatt_server\.send_notification\(fragment, self\.peer_address\),
                    self\.parent_interface\.loop
                \)

                # Wait for completion \(with timeout\)
                future\.result\(timeout=2\.0\)

                self\.txb \+= len\(fragment\)
                self\.parent_interface\.txb \+= len\(fragment\)

            except Exception as e:
                RNS\.log\(f"Failed to send notification \{i\+1\}/\{len\(fragments\)\} to \{self\.peer_name\}: \{e\}", RNS\.LOG_ERROR\)
                return False

        return True'''

    new_peripheral = '''    def _send_via_peripheral(self, fragments):
        """
        Send fragments via driver (peripheral mode uses notifications).

        Args:
            fragments: List of fragment bytes to send

        Returns:
            bool: True if all fragments sent successfully, False otherwise
        """
        for i, fragment in enumerate(fragments):
            try:
                # Driver automatically handles notification vs write based on connection type
                self.parent_interface.driver.send(self.peer_address, fragment)

                self.txb += len(fragment)
                self.parent_interface.txb += len(fragment)

            except Exception as e:
                RNS.log(f"Failed to send fragment {i+1}/{len(fragments)} to {self.peer_name}: {e}", RNS.LOG_ERROR)
                return False

        return True'''

    content = re.sub(old_peripheral, new_peripheral, content)

    # Replace _send_via_central
    old_central = r'''    def _send_via_central\(self, fragments\):
        """
        Send fragments via GATT characteristic write \(central mode\)\.

        Args:
            fragments: List of fragment bytes to send

        Returns:
            bool: True if all fragments sent successfully, False otherwise
        """
        # Use stored central_client \(set at initialization for central connections\)
        if not self\.central_client or not self\.central_client\.is_connected:
            RNS\.log\(f"\{self\} peer \{self\.peer_name\} \(\{self\.peer_address\}\) not connected or disconnected", RNS\.LOG_WARNING\)
            return False

        client = self\.central_client

        # Send each fragment via BLE characteristic write
        for i, fragment in enumerate\(fragments\):
            try:
                # Schedule the async write in the parent's event loop
                future = asyncio\.run_coroutine_threadsafe\(
                    client\.write_gatt_char\(BLEInterface\.CHARACTERISTIC_RX_UUID, fragment\),
                    self\.parent_interface\.loop
                \)

                # Wait for completion \(with timeout\)
                future\.result\(timeout=2\.0\)

                self\.txb \+= len\(fragment\)
                self\.parent_interface\.txb \+= len\(fragment\)

            except asyncio\.TimeoutError:
                RNS\.log\(f"\{self\} timeout sending fragment \{i\+1\}/\{len\(fragments\)\} to \{self\.peer_name\}, "
                        f"packet lost \(Reticulum will retransmit\)", RNS\.LOG_WARNING\)
                return False

            # HIGH #3: Comprehensive asyncio exception handling
            except \(asyncio\.CancelledError, RuntimeError\) as e:
                RNS\.log\(f"\{self\} event loop error sending fragment \{i\+1\}/\{len\(fragments\)\}: "
                        f"\{type\(e\)\.__name__\}: \{e\}", RNS\.LOG_ERROR\)
                # Mark interface as offline if event loop died
                if isinstance\(e, RuntimeError\) and "closed" in str\(e\)\.lower\(\):
                    RNS\.log\(f"\{self\} event loop is closed, marking interface offline", RNS\.LOG_ERROR\)
                    self\.parent_interface\.online = False
                return False

            except ConnectionError as e:
                RNS\.log\(f"\{self\} connection lost to \{self\.peer_name\} while sending fragment \{i\+1\}/\{len\(fragments\)\}: "
                        f"\{type\(e\)\.__name__\}: \{e\}, packet lost", RNS\.LOG_WARNING\)
                return False

            except Exception as e:
                error_type = type\(e\)\.__name__
                RNS\.log\(f"\{self\} unexpected exception sending fragment \{i\+1\}/\{len\(fragments\)\} to \{self\.peer_name\}: "
                        f"\{error_type\}: \{e\}, packet lost \(Reticulum will retransmit\)", RNS\.LOG_WARNING\)
                # If one fragment fails, the whole packet is lost
                # Reticulum's upper layers will handle retransmission
                return False

        return True'''

    new_central = '''    def _send_via_central(self, fragments):
        """
        Send fragments via driver (central mode uses GATT writes).

        Args:
            fragments: List of fragment bytes to send

        Returns:
            bool: True if all fragments sent successfully, False otherwise
        """
        # Check if peer is still connected
        if self.peer_address not in self.parent_interface.driver.connected_peers:
            RNS.log(f"{self} peer {self.peer_name} ({self.peer_address}) not connected", RNS.LOG_WARNING)
            return False

        # Send each fragment via driver
        for i, fragment in enumerate(fragments):
            try:
                # Driver automatically handles write vs notification based on connection type
                self.parent_interface.driver.send(self.peer_address, fragment)

                self.txb += len(fragment)
                self.parent_interface.txb += len(fragment)

            except ConnectionError as e:
                RNS.log(f"{self} connection lost to {self.peer_name} while sending fragment {i+1}/{len(fragments)}: "
                        f"{type(e).__name__}: {e}, packet lost", RNS.LOG_WARNING)
                return False

            except Exception as e:
                error_type = type(e).__name__
                RNS.log(f"{self} unexpected exception sending fragment {i+1}/{len(fragments)} to {self.peer_name}: "
                        f"{error_type}: {e}, packet lost (Reticulum will retransmit)", RNS.LOG_WARNING)
                return False

        return True'''

    content = re.sub(old_central, new_central, content)
    return content

def remove_stale_references(content):
    """Remove or update stale references to self.loop, self.gatt_server, etc."""

    # Remove _start_gatt_when_identity_ready method (replaced in pass 1)
    pattern = r'    def _start_gatt_when_identity_ready\(self\):.*?(?=\n    def )'
    content = re.sub(pattern, '', content, flags=re.DOTALL)

    # Remove remaining asyncio imports that aren't needed
    # (Keep asyncio since it might still be imported elsewhere, but comment about driver ownership)

    # Update threading model docstring
    content = content.replace(
        '    THREADING MODEL:\n    - Main asyncio loop in separate thread (_run_async_loop)\n    - LOCK ORDERING CONVENTION (to prevent deadlocks):\n      1. peer_lock - ALWAYS acquire first for peer state access\n      2. frag_lock - THEN acquire for fragmentation state\n      NEVER acquire locks in reverse order! (HIGH #2: deadlock prevention)\n    - Uses asyncio.run_coroutine_threadsafe for cross-thread calls',
        '    THREADING MODEL:\n    - Driver owns async event loop in separate thread\n    - LOCK ORDERING CONVENTION (to prevent deadlocks):\n      1. peer_lock - ALWAYS acquire first for peer state access\n      2. frag_lock - THEN acquire for fragmentation state\n      NEVER acquire locks in reverse order! (HIGH #2: deadlock prevention)\n    - Driver callbacks invoked from driver thread'
    )

    return content

def main():
    input_file = 'src/RNS/Interfaces/BLEInterface.py'

    print("Reading file...")
    content = read_file(input_file)

    print("Step 1: Refactoring detach() method...")
    content = refactor_detach_method(content)

    print("Step 2: Refactoring send methods...")
    content = refactor_send_methods(content)

    print("Step 3: Removing stale references...")
    content = remove_stale_references(content)

    print("Writing refactored file...")
    write_file(input_file, content)

    print("Done! Pass 2 complete.")
    print("\nRemaining manual tasks:")
    print("  - Verify all driver callbacks are correct")
    print("  - Test the refactored interface")
    print("  - Remove any remaining comments about bleak/bluezero")

if __name__ == '__main__':
    main()
