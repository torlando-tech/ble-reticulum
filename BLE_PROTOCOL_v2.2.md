# BLE Reticulum Protocol v2.2 Specification

**Version:** 2.2
**Date:** November 2025
**Status:** Stable

---

## Table of Contents

1. [Overview](#overview)
2. [Protocol Evolution](#protocol-evolution)
3. [BLE Advertisement](#ble-advertisement)
4. [GATT Service Structure](#gatt-service-structure)
5. [Connection Direction (MAC Sorting)](#connection-direction-mac-sorting)
6. [Identity Handshake Protocol](#identity-handshake-protocol)
7. [Identity-Based Keying](#identity-based-keying)
8. [Fragmentation & Reassembly](#fragmentation--reassembly)
9. [Connection Flow](#connection-flow)
10. [Error Handling & Edge Cases](#error-handling--edge-cases)
11. [Backwards Compatibility](#backwards-compatibility)
12. [Troubleshooting Guide](#troubleshooting-guide)

---

## Overview

The BLE Reticulum Protocol enables mesh networking over Bluetooth Low Energy (BLE) for the [Reticulum Network Stack](https://reticulum.network). This specification defines Protocol v2.2, which provides:

- **Bidirectional communication** via BLE GATT characteristics
- **Identity-based peer management** (survives MAC address rotation)
- **Deterministic connection direction** (prevents simultaneous connection attempts)
- **Automatic fragmentation/reassembly** for MTU handling
- **Zero-configuration discovery** via BLE advertisement

### Design Goals

1. **MAC Rotation Immunity:** Devices identified by cryptographic identity hash, not MAC address
2. **Asymmetric Connection Model:** One device acts as central, one as peripheral (prevents conflicts)
3. **Efficient Discovery:** Identity embedded in device name (bypasses bluezero service UUID bug)
4. **Graceful Degradation:** Works even if handshake or discovery partially fails

---

## Protocol Evolution

### v1.0 (Initial Release)
- Basic BLE GATT server/client
- Address-based peer tracking
- Generic device names (e.g., "RNS-Device")
- No MAC rotation support

### v2.0 (Identity Characteristic)
- Added Identity characteristic (16-byte peer identity)
- Centrals read peripheral identities via GATT characteristic
- Address-based fragmenter keys

### v2.1 (Identity-Based Naming)
- Device names encode identity: `RNS-{32-hex-identity-hash}`
- Bypasses bluezero service UUID bug (name-based discovery fallback)
- Identity mappings stored during discovery

### v2.2 (Current - Identity Handshake)
- **Identity handshake:** Centrals send 16-byte identity to peripherals
- **Identity-based keying:** Fragmenters/reassemblers keyed by identity hash
- **Bidirectional identity exchange:** Both sides learn peer identities without requiring bidirectional discovery
- **MAC sorting:** Deterministic connection direction based on MAC address comparison

---

## BLE Advertisement

### Service UUID

```
37145b00-442d-4a94-917f-8f42c5da28e3
```

All Reticulum BLE devices advertise this service UUID to enable discovery.

### Device Naming Convention

**Format:**
```
RNS-{32-hex-characters}
```

**Example:**
```
RNS-680069b61fa51cde5a751ed2396ce46d
```

Where `680069b61fa51cde5a751ed2396ce46d` is the first 16 bytes of the device's Reticulum identity hash, encoded as hexadecimal.

### Why Embed Identity in Name?

The bluezero GATT server library (used for peripheral mode) has a known bug where service UUIDs are not properly exposed in BLE advertisements when queried via Bleak scanners. Clients see `service_uuids=[]` even though the service is registered.

**Workaround:**
By embedding the identity in the device name, scanners can:
1. Match by service UUID (preferred, when it works)
2. Fall back to name pattern matching: `^RNS-[0-9a-f]{32}$`
3. Extract identity directly from the name, bypassing GATT characteristic reads

### Advertisement Interval

- **Default:** 100-200ms (BlueZ defaults)
- **Controlled by:** BlueZ daemon (not configurable via bluezero)
- **Discovery time:** 0.5-2.0 seconds depending on power mode

---

## GATT Service Structure

### Primary Service

**UUID:** `37145b00-442d-4a94-917f-8f42c5da28e3`
**Type:** Primary

### Characteristics

#### 1. RX Characteristic (Central → Peripheral)

**UUID:** `37145b00-442d-4a94-917f-8f42c5da28e5`
**Properties:** `WRITE`, `WRITE_WITHOUT_RESPONSE`
**Purpose:** Centrals write data to peripheral
**First Packet:** Identity handshake (16 bytes)

#### 2. TX Characteristic (Peripheral → Central)

**UUID:** `37145b00-442d-4a94-917f-8f42c5da28e4`
**Properties:** `READ`, `NOTIFY`
**Purpose:** Peripherals send data to central via notifications
**Notification Enabled:** Central subscribes via CCCD (Client Characteristic Configuration Descriptor)

#### 3. Identity Characteristic (Protocol v2+)

**UUID:** `37145b00-442d-4a94-917f-8f42c5da28e6`
**Properties:** `READ`
**Value:** 16 bytes (peer's identity hash)
**Purpose:** Centrals read peripheral identity during connection
**Note:** v2.2+ also uses handshake for peripheral → central identity exchange

---

## Connection Direction (MAC Sorting)

To prevent both devices from simultaneously trying to connect to each other (which causes conflicts and connection failures), Protocol v2.2 implements **deterministic connection direction** based on MAC address comparison.

### Algorithm

```python
# Normalize MAC addresses (remove colons)
my_mac_int = int(my_mac.replace(":", ""), 16)
peer_mac_int = int(peer_mac.replace(":", ""), 16)

if my_mac_int < peer_mac_int:
    # My MAC is lower: I initiate connection (act as central)
    connect_to_peer()
elif my_mac_int > peer_mac_int:
    # My MAC is higher: Wait for peer to connect (act as peripheral)
    skip_connection()
else:
    # Same MAC (should never happen)
    raise Exception("MAC address collision")
```

### Example

**Pi1 MAC:** `B8:27:EB:A8:A7:22` = `0xB827EBA8A722`
**Pi2 MAC:** `B8:27:EB:10:28:CD` = `0xB827EB1028CD`

**Comparison:**
```
0xB827EBA8A722 (Pi1) > 0xB827EB1028CD (Pi2)
```

**Result:**
- Pi2 (lower MAC) connects to Pi1 as **central**
- Pi1 (higher MAC) accepts connection as **peripheral**

### Benefits

1. **No simultaneous connections:** Only one device initiates
2. **Deterministic:** Same result every time based on MACs
3. **No coordination required:** Each device independently decides its role
4. **Prevents connection storms:** No retries from both sides

### Discovery Implications

Since only the lower-MAC device scans and connects:
- Lower-MAC device **must** discover higher-MAC device via scanning
- Higher-MAC device **may never scan** for lower-MAC device
- **Problem:** Higher-MAC device (peripheral) doesn't know lower-MAC device's identity
- **Solution:** Identity handshake protocol (see next section)

---

## Identity Handshake Protocol

### The Problem

In the MAC-sorted connection model:
- **Central** (lower MAC) discovers peripheral via scanning → gets identity from device name
- **Peripheral** (higher MAC) never scans for central → doesn't know central's identity

In BLE's asymmetric model:
- Centrals can read characteristics from peripherals (✓)
- Peripherals **cannot** read characteristics from centrals (✗)

**Result:** Without intervention, peripherals have no way to learn central identities.

### The Solution: Identity Handshake

When a central connects to a peripheral, it **immediately sends its 16-byte identity hash as the first packet** written to the RX characteristic.

### Handshake Flow

```
Central                                  Peripheral
   |                                         |
   | 1. Discover via scanning                |
   |    (get peripheral's identity           |
   |     from device name)                   |
   |                                         |
   | 2. Connect (BLE link established)       |
   |---------------------------------------> |
   |                                         |
   | 3. Read Identity characteristic         |
   |    (confirms peripheral identity)       |
   |<--------------------------------------- |
   |                                         |
   | 4. Subscribe to TX notifications        |
   |---------------------------------------> |
   |                                         |
   | 5. HANDSHAKE: Write 16 bytes to RX      |
   |    (send our identity)                  |
   |=======================================> |
   |                                         | 6. Receive 16-byte write
   |                                         |    - Detect handshake
   |                                         |    - Store identity mapping
   |                                         |    - Create peer interface
   |                                         |    - Create fragmenters
   |                                         |
   | 7. Send normal data                     |
   |---------------------------------------> |
   |                                         | 8. Reassemble and process
   |                                         |
```

### Handshake Packet Format

**Size:** Exactly 16 bytes
**Content:** Central's identity hash (first 16 bytes of `RNS.Identity.hash`)
**Characteristic:** RX characteristic (`37145b00-442d-4a94-917f-8f42c5da28e5`)
**Write Type:** `write_with_response` (GATT Write Request)

### Handshake Detection (Peripheral Side)

```python
def handle_peripheral_data(self, data, sender_address):
    # Check if we have peer identity
    peer_identity = self.address_to_identity.get(sender_address)

    # Identity handshake detection
    if not peer_identity and len(data) == 16:
        # This is the handshake!
        central_identity = bytes(data)
        central_identity_hash = RNS.Identity.full_hash(central_identity)[:16].hex()[:16]

        # Store identity mappings
        self.address_to_identity[sender_address] = central_identity
        self.identity_to_address[central_identity_hash] = sender_address

        # Create peer interface and fragmenters
        self._spawn_peer_interface(...)
        self._create_fragmenters(...)

        return  # Handshake processed

    # Normal data processing
    ...
```

### Edge Cases

**Q: What if the first real data packet is also 16 bytes?**
A: If `peer_identity` already exists, the handshake detection is skipped. Only 16-byte packets **without an existing identity** are treated as handshakes.

**Q: What if handshake fails?**
A: The peripheral logs a warning and drops subsequent data until the identity is learned via another method (e.g., next scan cycle). Connection continues but data is dropped.

**Q: What if handshake arrives twice?**
A: Identity mapping is updated (idempotent operation). No error.

---

## Identity-Based Keying

### Why Not Use MAC Addresses as Keys?

BLE devices can **rotate MAC addresses** for privacy reasons. If fragmenters/reassemblers are keyed by MAC address, they become orphaned when the MAC changes.

### Solution: Identity-Based Keys

All peer-specific data structures (fragmenters, reassemblers, interfaces) are keyed by a **16-character hex string derived from the peer's identity hash**.

### Key Computation

```python
def _get_fragmenter_key(self, peer_identity, peer_address):
    """
    Compute fragmenter/reassembler dictionary key using identity hash.

    Args:
        peer_identity: 16-byte identity hash
        peer_address: BLE MAC address (unused in v2.2, kept for compatibility)

    Returns:
        16-character hex string (e.g., "680069b61fa51cde")
    """
    return RNS.Identity.full_hash(peer_identity)[:16].hex()[:16]
```

**Example:**
```python
peer_identity = bytes.fromhex("680069b61fa51cde5a751ed2396ce46d")
frag_key = _get_fragmenter_key(peer_identity, "B8:27:EB:10:28:CD")
# Result: "680069b61fa51cde"
```

### Identity Mapping Tables

Two dictionaries maintain bidirectional identity ↔ address mappings:

```python
# MAC address → 16-byte identity
self.address_to_identity = {
    "B8:27:EB:10:28:CD": b'\x68\x00\x69\xb6\x1f\xa5\x1c\xde...',
}

# 16-char identity hash → MAC address
self.identity_to_address = {
    "680069b61fa51cde": "B8:27:EB:10:28:CD",
}
```

### Dictionary Structures

```python
# Fragmenters (keyed by identity hash)
self.fragmenters = {
    "680069b61fa51cde": BLEFragmenter(mtu=517),
    "a1b2c3d4e5f6g7h8": BLEFragmenter(mtu=23),
}

# Reassemblers (keyed by identity hash)
self.reassemblers = {
    "680069b61fa51cde": BLEReassembler(timeout=30.0),
    "a1b2c3d4e5f6g7h8": BLEReassembler(timeout=30.0),
}

# Peer interfaces (keyed by identity hash)
self.spawned_interfaces = {
    "680069b61fa51cde": BLEPeerInterface(...),
}
```

### Benefits

1. **MAC rotation immunity:** Key remains valid even if peer's MAC changes
2. **Unique identity:** No collisions (cryptographic identity hash)
3. **Lookup efficiency:** O(1) dictionary lookups
4. **Unified keying:** Same key for fragmenters, reassemblers, and interfaces

---

## Fragmentation & Reassembly

### Why Fragment?

BLE has a maximum transmission unit (MTU) that limits packet size:
- **Minimum MTU:** 23 bytes (BLE 4.0 spec)
- **Common MTU:** 185 bytes (BLE 4.2+)
- **Maximum MTU:** 517 bytes (BLE 5.0+)

Reticulum packets can be much larger (up to several KB), requiring fragmentation.

### MTU Negotiation

```python
# Central side: Read negotiated MTU after connection
mtu = client.mtu_size  # e.g., 517

# Peripheral side: MTU is managed by GATT server
# (BlueZ negotiates automatically during connection)
```

**Payload Size:**
Each BLE packet has a 3-byte ATT header + 2-byte handle, leaving:
```
payload_size = mtu - 5
```

For MTU=23:
```
payload_size = 23 - 5 = 18 bytes
```

### Fragmentation

**BLEFragmenter** splits packets into MTU-sized chunks:

```python
class BLEFragmenter:
    def fragment(self, data, mtu):
        """
        Fragment data into BLE packets.

        Format: [sequence_byte][payload_bytes]
        - sequence_byte: 0x00 to 0xFF (increments, wraps at 256)
        - payload_bytes: (mtu - 3 - 1) bytes of data

        Returns: List of fragments
        """
        payload_size = mtu - 3 - 1  # ATT header + sequence byte
        fragments = []

        for i in range(0, len(data), payload_size):
            sequence = (self.sequence_counter % 256).to_bytes(1, 'big')
            payload = data[i:i+payload_size]
            fragment = sequence + payload
            fragments.append(fragment)
            self.sequence_counter += 1

        return fragments
```

**Example:**
```
Data: 233 bytes
MTU: 23 bytes
Payload size: 18 bytes

Fragments:
  [0x00][18 bytes of data]  (fragment 1)
  [0x01][18 bytes of data]  (fragment 2)
  ...
  [0x0C][17 bytes of data]  (fragment 13, last)

Total: 13 fragments
```

### Reassembly

**BLEReassembler** collects fragments and reconstructs the original packet:

```python
class BLEReassembler:
    def receive_fragment(self, fragment, sender):
        """
        Process a fragment and return complete packet if reassembly finishes.

        Returns:
            bytes if packet complete, None otherwise
        """
        sequence = fragment[0]
        payload = fragment[1:]

        # Detect new packet (sequence reset to 0x00)
        if sequence == 0x00:
            self.current_packet = bytearray()

        # Append fragment
        self.current_packet.extend(payload)

        # Check if packet complete (implementation-specific heuristic)
        if self._is_packet_complete():
            complete = bytes(self.current_packet)
            self.current_packet = None
            return complete

        return None
```

**Timeout Handling:**
If fragments stop arriving before packet completion, reassembler times out after 30 seconds and discards partial packet.

---

## Connection Flow

### Full Connection Sequence

```
Device A (Lower MAC)                     Device B (Higher MAC)
   |                                         |
   | 1. Start scanning (0.5-2s)              | 1. Start advertising
   |                                         |    - Service UUID
   |                                         |    - Device name: RNS-{identity}
   |                                         |
   | 2. Discover Device B                    |
   |    - Match by service UUID or name      |
   |    - Extract identity from name         |
   |    - Store in address_to_identity       |
   |                                         |
   | 3. MAC sorting check                    |
   |    my_mac < peer_mac → I connect        |
   |                                         |
   | 4. BLE connection (central role)        |
   |=======================================> | 4. Accept connection (peripheral role)
   |                                         |
   | 5. Service discovery                    |
   |    - Find Reticulum service             |
   |    - Get characteristics                |
   |                                         |
   | 6. Read Identity characteristic         |
   |    (confirm peer identity)              |
   |<--------------------------------------- |
   |                                         |
   | 7. Subscribe to TX notifications        |
   |---------------------------------------> |
   |                                         |
   | 8. IDENTITY HANDSHAKE                   |
   |    Write 16 bytes to RX char            |
   |=======================================> | 9. Receive handshake
   |                                         |    - Detect 16-byte write
   |                                         |    - Store A's identity
   |                                         |    - Create peer interface
   |                                         |    - Create fragmenters/reassemblers
   |                                         |
   | 10. Create fragmenter/reassembler       |
   |     (already has B's identity)          |
   |                                         |
   | 11. CONNECTION ESTABLISHED              |
   |     Both sides have identities          |
   |                                         |
   | 12. Bidirectional data flow             |
   |<--------------------------------------> |
   |                                         |
```

### Discovery Phase (Device A)

1. **Scan for BLE devices** (0.5-2.0 seconds depending on power mode)
2. **Match peers:**
   - Primary: Check `service_uuids` for Reticulum UUID
   - Fallback: Check device name matches `^RNS-[0-9a-f]{32}$`
3. **Extract identity:**
   - Parse 32 hex chars from device name
   - Convert to 16-byte identity
   - Store in `address_to_identity[peer_address] = identity`
4. **Score peers** by RSSI, history, recency
5. **Select best peer** for connection

### Connection Phase (Device A → Device B)

1. **MAC sorting check:**
   - If `my_mac > peer_mac`: Skip (wait for peer to connect)
   - If `my_mac < peer_mac`: Proceed
2. **Connect via Bleak:**
   ```python
   client = BleakClient(peer_address)
   await client.connect()
   ```
3. **Service discovery:**
   ```python
   services = await client.get_services()
   reticulum_service = find_service(services, RETICULUM_UUID)
   ```
4. **Read identity characteristic:**
   ```python
   identity_char = find_characteristic(IDENTITY_UUID)
   peer_identity = await client.read_gatt_char(identity_char)
   ```
5. **Subscribe to notifications:**
   ```python
   await client.start_notify(TX_CHAR_UUID, notification_callback)
   ```
6. **Send identity handshake:**
   ```python
   await client.write_gatt_char(RX_CHAR_UUID, our_identity)
   ```
7. **Create peer infrastructure:**
   - Fragmenter (for sending)
   - Reassembler (for receiving)
   - Peer interface (for RNS integration)

### Acceptance Phase (Device B)

1. **Advertising:** bluezero peripheral continuously advertises
2. **Connection accepted:** BlueZ handles BLE link establishment
3. **Handshake received:**
   - 16-byte write to RX characteristic
   - Detected by `handle_peripheral_data()`
   - Identity extracted and stored
4. **Create peer infrastructure:**
   - Fragmenter (for sending via TX notifications)
   - Reassembler (for receiving via RX writes)
   - Peer interface

---

## Error Handling & Edge Cases

### Service Discovery Failures

**Problem:** Central connects but doesn't find Reticulum service UUID.

**Causes:**
- bluezero D-Bus registration delay
- BlueZ version incompatibility
- GATT server not fully initialized

**Mitigation:**
1. Wait 1.5 seconds after connection before discovery (`service_discovery_delay`)
2. Log all discovered service UUIDs for debugging
3. Fail gracefully: disconnect, record failure, retry later

**Code:**
```python
if not reticulum_service:
    RNS.log(f"cannot proceed without Reticulum service, disconnecting", RNS.LOG_ERROR)
    await client.disconnect()
    self._record_connection_failure(peer.address)
    return
```

### Missing Identity Mappings

**Problem:** Data arrives from peer without identity in `address_to_identity`.

**Causes:**
- Handshake failed or not sent
- Race condition (data sent before handshake processed)
- Discovery didn't extract identity from name

**Mitigation:**
1. Central side: Always read identity characteristic before sending data
2. Peripheral side: Wait for handshake before processing data
3. Log warnings when identity missing
4. Drop data gracefully (no crashes)

**Code:**
```python
if not peer_identity:
    RNS.log(f"no identity for peer {peer_address}, dropping data", RNS.LOG_WARNING)
    return
```

### Handshake Failures

**Problem:** Central's handshake write fails.

**Causes:**
- GATT server not ready
- Connection dropped during handshake
- BlueZ permission issues

**Mitigation:**
- Handshake failure is **non-critical**
- Peripheral can learn identity on next scan cycle
- Log warning but continue connection
- Retry handshake on next connection

**Code:**
```python
try:
    await client.write_gatt_char(RX_CHAR_UUID, our_identity, response=True)
    RNS.log(f"sent identity handshake", RNS.LOG_INFO)
except Exception as e:
    RNS.log(f"failed to send identity handshake: {e}", RNS.LOG_WARNING)
    # Continue anyway - peripheral can learn on next scan
```

### Notification Setup Failures

**Problem:** `start_notify()` raises `EOFError` or `KeyError`.

**Causes:**
- GATT services not fully discovered
- BlueZ D-Bus timing issues
- Characteristics not registered yet

**Mitigation:**
- Retry up to 3 times with exponential backoff (0.2s, 0.5s, 1.0s)
- If all retries fail: disconnect, record failure, retry connection later

**Code:**
```python
max_retries = 3
retry_delays = [0.2, 0.5, 1.0]

for attempt in range(max_retries):
    try:
        await client.start_notify(TX_CHAR_UUID, callback)
        break  # Success
    except (EOFError, KeyError):
        if attempt < max_retries - 1:
            await asyncio.sleep(retry_delays[attempt])
            continue
        else:
            # All retries failed
            await client.disconnect()
            return
```

### MAC Address Collision

**Problem:** Two devices have the same MAC address.

**Likelihood:** Virtually impossible (48-bit address space)

**Handling:**
```python
if my_mac_int == peer_mac_int:
    RNS.log(f"MAC collision detected: {peer_address}", RNS.LOG_ERROR)
    # Fall through to normal connection logic (both devices may connect)
```

### Reassembler Lookup Failures

**Problem:** Fragment arrives but no reassembler found.

**Causes:**
- Identity handshake not processed yet
- Fragmenter/reassembler creation failed
- Memory cleared (device rebooted)

**Mitigation:**
- Log warning with fragmenter key for debugging
- Drop fragment gracefully
- Peer will retransmit if needed (RNS protocol handles this)

**Code:**
```python
if frag_key not in self.reassemblers:
    RNS.log(f"no reassembler for {peer_address} (key: {frag_key[:16]})", RNS.LOG_WARNING)
    return
```

---

## Backwards Compatibility

### v2.2 ↔ v2.1 Compatibility

**v2.2 Central → v2.1 Peripheral:**
- Central sends handshake (16 bytes)
- v2.1 peripheral doesn't expect handshake → treats as normal data
- v2.1 peripheral attempts reassembly, fails (not valid fragment format)
- Data is dropped, but connection continues
- Central can still send normal packets after handshake

**v2.1 Central → v2.2 Peripheral:**
- Central doesn't send handshake
- v2.2 peripheral waits for handshake
- No handshake arrives → peripheral drops all data (no identity)
- **Degraded mode:** Peripheral must discover central via scanning to get identity
- If peripheral discovers central: identity is added, data flow resumes

**Recommendation:** Upgrade all devices to v2.2 for full bidirectional communication.

### v2.2 ↔ v2.0 Compatibility

**v2.0 Devices:**
- Don't use identity-based device names (generic names like "RNS-Device")
- Don't have identity characteristic
- Use address-based keying

**Compatibility:**
- v2.2 can discover v2.0 devices by service UUID
- v2.2 cannot extract identity from generic device name
- Connection may succeed but identity features are disabled
- Falls back to address-based tracking (breaks on MAC rotation)

**Recommendation:** Upgrade v2.0 devices to v2.2.

### v2.2 ↔ v1.0 Compatibility

**v1.0 Devices:**
- Basic GATT server/client only
- No identity support at all

**Compatibility:**
- Not compatible
- v2.2 requires identity for peer tracking
- Connection attempts will fail

**Recommendation:** Upgrade v1.0 devices to v2.2.

---

## Troubleshooting Guide

### Problem: Devices discover each other but don't connect

**Symptoms:**
- Logs show "found matching peer via service UUID"
- Logs show "skipping {peer} - connection direction: they initiate"
- No connection established

**Cause:** Both devices have lower/higher MAC comparison wrong, or one device's MAC isn't being read correctly.

**Debug:**
1. Check both device MACs:
   ```bash
   bluetoothctl show
   ```
2. Compare MACs manually:
   ```python
   int("B8:27:EB:A8:A7:22".replace(":", ""), 16)
   int("B8:27:EB:10:28:CD".replace(":", ""), 16)
   ```
3. Verify logs show correct MAC sorting decision

**Fix:** Ensure local adapter address is correctly detected on both devices.

---

### Problem: Connection established but no data flows

**Symptoms:**
- Logs show "connected to {peer}"
- Logs show "sent notification: X bytes"
- No "received X bytes" logs on other side

**Cause 1:** Notification handler not set up correctly (central side).

**Debug:**
1. Check for "✓ notification setup SUCCEEDED" log
2. Enable EXTREME logging to see if callback is invoked
3. Check for "no identity for peer" warnings

**Fix:**
- Verify identity handshake completed
- Check `address_to_identity` mapping exists
- Ensure fragmenter key computation matches

**Cause 2:** BlueZ cache contains stale data.

**Fix:**
```bash
sudo systemctl stop bluetooth
sudo rm -rf /var/lib/bluetooth/*/cache/*
sudo systemctl restart bluetooth
```

---

### Problem: "Reticulum service not found" error

**Symptoms:**
- Logs show "service discovery completed: 1 services"
- Logs show "Discovered service UUID: 00001800-..." (Generic Access)
- Logs show "Reticulum service not found"

**Cause:** bluezero GATT server not fully registered in BlueZ D-Bus.

**Debug:**
1. Check peripheral logs for "✓ GATT server started and advertising"
2. On central, increase `service_discovery_delay`:
   ```ini
   [BLE Interface]
   service_discovery_delay = 2.5
   ```
3. Use `busctl` to inspect BlueZ D-Bus:
   ```bash
   busctl tree org.bluez
   busctl introspect org.bluez /org/bluez/hci0/dev_XX_XX_XX_XX_XX_XX/service0001
   ```

**Fix:**
- Restart peripheral's RNS daemon
- Increase service discovery delay
- Upgrade bluezero library

---

### Problem: "no identity for central, dropping data"

**Symptoms:**
- Peripheral receives data from central
- Logs show "no identity for central {address}"
- All data is dropped

**Cause:** Identity handshake failed or not sent.

**Debug:**
1. Check central logs for "sent identity handshake"
2. Check peripheral logs for "received identity handshake"
3. Enable EXTREME logging to see all 16-byte writes

**Fix:**
- Ensure central is running v2.2 (older versions don't send handshake)
- Check for exceptions during handshake send
- Restart both devices to retry handshake

---

### Problem: Fragments not reassembling

**Symptoms:**
- Logs show "received 23 bytes from peer" (many times)
- No "reassembled packet" logs
- No "packets_reassembled" statistics

**Cause:** Reassembler not found for peer (key mismatch).

**Debug:**
1. Check for "no reassembler for {address}" warnings
2. Compare fragmenter keys on both sides
3. Verify identity mappings match

**Fix:**
- Ensure identity handshake completed successfully
- Check `_get_fragmenter_key()` uses identity, not address
- Restart connection to recreate fragmenters/reassemblers

---

### Problem: BlueZ cache causing discovery failures

**Symptoms:**
- Device visible in `bluetoothctl scan on`
- Not visible in RNS BLE interface scans
- Logs show 0 matching devices

**Cause:** BlueZ cached old advertisement data with wrong name/service UUID.

**Fix:**
```bash
# Clear all BlueZ cache
sudo systemctl stop bluetooth
sudo rm -rf /var/lib/bluetooth/*
sudo systemctl start bluetooth
bluetoothctl power on
```

**Prevention:** Change device identity rarely (triggers name change, requires cache clear on all peers).

---

## Appendix: UUID Reference

### Service UUID
```
37145b00-442d-4a94-917f-8f42c5da28e3
```

### Characteristic UUIDs

| Characteristic | UUID | Properties |
|---|---|---|
| RX (Write) | `37145b00-442d-4a94-917f-8f42c5da28e5` | WRITE, WRITE_WITHOUT_RESPONSE |
| TX (Notify) | `37145b00-442d-4a94-917f-8f42c5da28e4` | READ, NOTIFY |
| Identity (Read) | `37145b00-442d-4a94-917f-8f42c5da28e6` | READ |

---

## Appendix: Sequence Diagrams

### Discovery and Connection

```
 Pi2 (Lower MAC)                          Pi1 (Higher MAC)
 B8:27:EB:10:28:CD                        B8:27:EB:A8:A7:22
       |                                         |
       | [SCAN] Scan for BLE devices             | [ADVERTISE] Broadcasting:
       |        (scan_time=0.5s)                 |   Service: 37145b00-...
       |                                         |   Name: RNS-680069b6...
       |<========================================|
       |                                         |
       | [DISCOVER] Found peer via service UUID  |
       |   - Name: RNS-680069b61fa51cde5a751ed23|
       |   - RSSI: -36 dBm                       |
       |   - Identity: 680069b61fa51cde...       |
       |                                         |
       | [MAC SORT] 0xB827EB1028CD < 0xB827EBA8A722
       |   → I connect (central role)            |
       |                                         |
       | [CONNECT] BLE connection request        |
       |=======================================> | [ACCEPT] Connection accepted
       |                                         |   (peripheral role)
       |                                         |
       | [GATT] Service discovery                |
       |---------------------------------------> |
       |<--------------------------------------- | Services: Reticulum service
       |                                         |
       | [GATT] Read Identity characteristic     |
       |---------------------------------------> |
       |<--------------------------------------- | Value: 680069b61fa51cde...
       |                                         |
       | [GATT] Subscribe to TX notifications    |
       |---------------------------------------> |
       |                                         | [OK] CCCD updated
       |                                         |
       | [HANDSHAKE] Write 16 bytes to RX        |
       |   Data: <Pi2's 16-byte identity>        |
       |=======================================> | [HANDSHAKE] Detect 16-byte write
       |                                         |   - Extract Pi2's identity
       |                                         |   - Store: address_to_identity
       |                                         |   - Create peer interface
       |                                         |   - Create fragmenters
       |                                         |
       | [READY] Both sides have identities      | [READY]
       |                                         |
       | [DATA] Send announce (233 bytes)        |
       |   → Fragment into 13 packets            |
       |---------------------------------------> | [DATA] Receive fragments
       |                                         |   → Reassemble to 233 bytes
       |                                         |   → Process announce
       |                                         |
       | [DATA] Receive announce (233 bytes)     | [DATA] Send announce (233 bytes)
       |   ← Reassemble from 13 notifications    |   ← Fragment into 13 packets
       |<--------------------------------------- |
       |   → Process announce                    |
       |                                         |
```

---

## Summary

BLE Protocol v2.2 provides robust, bidirectional mesh networking over Bluetooth Low Energy with the following key features:

✅ **Identity-based peer management** (survives MAC rotation)
✅ **Deterministic connection direction** (prevents conflicts)
✅ **Identity handshake** (enables asymmetric discovery)
✅ **Automatic fragmentation/reassembly** (handles MTU limits)
✅ **Graceful error handling** (logs warnings, continues operation)
✅ **Zero-configuration discovery** (identity in device name)

This protocol enables reliable Reticulum mesh networking over BLE with minimal user configuration.

---

**End of BLE Protocol v2.2 Specification**
