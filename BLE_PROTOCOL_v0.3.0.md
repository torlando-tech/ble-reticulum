# BLE-Reticulum Protocol Specification v0.3.0

**Version**: 0.3.0
**Date**: December 2025
**Status**: Draft
**Backwards Compatible With**: v2.2

## 1. Overview

This document specifies the v0.3.0 extension to the BLE-Reticulum protocol. This version adds **capability advertisement** to support devices that can only operate in peripheral mode (e.g., ESP32-S3).

### 1.1 Problem Statement

The v2.2 protocol uses MAC address sorting to determine connection direction: the device with the numerically lower MAC address initiates the connection (acts as BLE central). However, some hardware platforms (notably ESP32-S3) cannot reliably operate as BLE central due to stack limitations.

When such a device has a lower MAC address than a peer, neither device initiates a connection:
- The peripheral-only device cannot initiate (hardware limitation)
- The peer waits for the lower-MAC device to initiate (per v2.2 protocol)

### 1.2 Solution

v0.3.0 introduces **capability flags** in the advertising packet via BLE manufacturer-specific data. Devices advertise their role capabilities, allowing the connection direction logic to be overridden when one device is peripheral-only.

## 2. Manufacturer-Specific Data Format

### 2.1 Advertising Data Structure

v0.3.0 devices include manufacturer-specific data in their advertising packet:

```
AD Type: 0xFF (Manufacturer Specific Data)
Length:  5 bytes (1 type + 4 data)

Data Format (4 bytes):
┌─────────┬─────────┬─────────┬─────────┐
│ Byte 0  │ Byte 1  │ Byte 2  │ Byte 3  │
├─────────┼─────────┼─────────┼─────────┤
│ CID Low │ CID High│ Version │  Flags  │
└─────────┴─────────┴─────────┴─────────┘

CID (Bytes 0-1): Company ID, little-endian
                 0xFFFF = Reserved for testing (Bluetooth SIG)

Version (Byte 2): Protocol version
                  0x03 = v0.3.0

Flags (Byte 3):   Capability flags
                  Bit 0: PERIPHERAL_ONLY (1 = cannot act as central)
                  Bit 1: Reserved (CENTRAL_ONLY, future use)
                  Bits 2-7: Reserved (must be 0)
```

### 2.2 Example Values

| Device Type | CID | Version | Flags | Raw Bytes |
|-------------|-----|---------|-------|-----------|
| Dual-mode (full capability) | 0xFFFF | 0x03 | 0x00 | `FF FF 03 00` |
| Peripheral-only (ESP32-S3) | 0xFFFF | 0x03 | 0x01 | `FF FF 03 01` |

### 2.3 Advertising Packet Layout

The v0.3.0 advertising packet extends v2.2:

```
Main Advertising Packet (31 bytes max):
├── Flags (3 bytes)
├── Complete 128-bit Service UUID (18 bytes)
│   └── 37145b00-442d-4a94-917f-8f42c5da28e3
├── Manufacturer Data (5 bytes)        ← NEW in v0.3.0
│   ├── AD Type 0xFF (1 byte)
│   └── Data (4 bytes): CID + Version + Flags
└── Remaining: 5 bytes available

Scan Response Packet (31 bytes max):
└── Device Name: "RNS-{identity}" (variable)
```

## 3. Connection Direction Logic

### 3.1 Decision Algorithm

```
FUNCTION shouldInitiateConnection(local_device, peer_device):

    local_peripheral_only = local_device.flags & PERIPHERAL_ONLY
    peer_peripheral_only = peer_device.flags & PERIPHERAL_ONLY

    # Case 1: Peer is peripheral-only, we are not
    IF peer_peripheral_only AND NOT local_peripheral_only:
        RETURN TRUE   # We MUST initiate (peer cannot)

    # Case 2: We are peripheral-only, peer is not
    IF local_peripheral_only AND NOT peer_peripheral_only:
        RETURN FALSE  # Peer MUST initiate (we cannot)

    # Case 3: Both are peripheral-only (deadlock)
    IF peer_peripheral_only AND local_peripheral_only:
        LOG_WARNING("Both devices peripheral-only, connection impossible")
        RETURN FALSE  # Neither can initiate

    # Case 4: Both have full capability - use v2.2 MAC sorting
    RETURN local_device.mac < peer_device.mac
```

### 3.2 Capability Detection

When a device does not advertise manufacturer data (v2.2 device):
- Assume `has_capability_data = false`
- Assume `capability_flags = 0x00` (full capability)
- Fall back to v2.2 MAC sorting

When manufacturer data is present:
- Verify Company ID = 0xFFFF
- Verify Version >= 0x03
- Extract capability flags from byte 3

## 4. Backwards Compatibility

### 4.1 Compatibility Matrix

| Our Device | Peer Device | Connection Decision | Result |
|------------|-------------|---------------------|--------|
| v0.3.0 dual | v0.3.0 dual | MAC sorting | Works |
| v0.3.0 dual | v0.3.0 P-only | We initiate | Works |
| v0.3.0 P-only | v0.3.0 dual | Peer initiates | Works |
| v0.3.0 dual | v0.2.x | MAC sorting | Works |
| v0.3.0 P-only | v0.2.x (lower MAC) | v0.2.x initiates | Works |
| v0.3.0 P-only | v0.2.x (higher MAC) | Neither initiates | **Fails** |
| v0.3.0 P-only | v0.3.0 P-only | Neither initiates | **Fails** |

### 4.2 Known Limitations

1. **v0.3.0 peripheral-only ↔ v0.2.x with higher MAC**: No connection possible. The v0.2.x device uses MAC sorting and waits for the lower-MAC device (the v0.3.0 P-only) to initiate.

   **Mitigation**: Upgrade the v0.2.x device to v0.3.0.

2. **Two peripheral-only devices**: Connection impossible as neither can initiate.

   **Mitigation**: Ensure at least one device in the mesh has full capability.

## 5. GATT Service (Unchanged from v2.2)

The GATT service structure remains unchanged:

```
Reticulum Service: 37145b00-442d-4a94-917f-8f42c5da28e3
├── RX Characteristic: 37145b00-442d-4a94-917f-8f42c5da28e5
│   └── Properties: WRITE, WRITE_WITHOUT_RESPONSE
├── TX Characteristic: 37145b00-442d-4a94-917f-8f42c5da28e4
│   └── Properties: READ, NOTIFY
│   └── CCCD: 00002902-0000-1000-8000-00805f9b34fb
└── Identity Characteristic: 37145b00-442d-4a94-917f-8f42c5da28e6
    └── Properties: READ
    └── Value: 16-byte identity hash
```

## 6. Implementation Notes

### 6.1 NimBLE (ESP32)

```cpp
// Setting manufacturer data
uint8_t mfr_data[4] = {0xFF, 0xFF, 0x03, peripheral_only ? 0x01 : 0x00};
advertising->setManufacturerData(mfr_data, sizeof(mfr_data));

// Parsing manufacturer data
if (device->haveManufacturerData()) {
    std::string data = device->getManufacturerData();
    if (data.size() >= 4) {
        uint16_t cid = (uint8_t)data[0] | ((uint8_t)data[1] << 8);
        if (cid == 0xFFFF && data[2] >= 0x03) {
            capability_flags = data[3];
        }
    }
}
```

### 6.2 Android

```kotlin
// Setting manufacturer data (note: Android API excludes CID in the byte array)
val mfrData = byteArrayOf(0x03.toByte(), flags.toByte())
advertiseData.addManufacturerData(0xFFFF, mfrData)

// Parsing manufacturer data
val mfrData = scanRecord.getManufacturerSpecificData(0xFFFF)
if (mfrData != null && mfrData.size >= 2 && mfrData[0] >= 0x03.toByte()) {
    capabilityFlags = mfrData[1].toInt() and 0xFF
}
```

### 6.3 Python (BlueZ/Bleak)

```python
# Setting manufacturer data in advertisement
# BlueZ uses D-Bus ManufacturerData property
manufacturer_data = {0xFFFF: bytes([0x03, 0x01 if peripheral_only else 0x00])}

# Parsing manufacturer data from scan
mfr_data = device.metadata.get("manufacturer_data", {}).get(0xFFFF)
if mfr_data and len(mfr_data) >= 2 and mfr_data[0] >= 0x03:
    capability_flags = mfr_data[1]
```

## 7. Future Extensions

Reserved capability flag bits for potential future use:

| Bit | Name | Description |
|-----|------|-------------|
| 0 | PERIPHERAL_ONLY | Cannot act as BLE central |
| 1 | CENTRAL_ONLY | Cannot act as BLE peripheral |
| 2 | HIGH_BANDWIDTH | Supports extended MTU/PHY |
| 3 | RELAY_CAPABLE | Can relay packets in mesh |
| 4-7 | Reserved | Must be 0 |

## 8. Version History

| Version | Date | Changes |
|---------|------|---------|
| v2.0 | Oct 2025 | Identity characteristic for peer identification |
| v2.1 | Oct 2025 | (Deprecated) Identity in device name |
| v2.2 | Nov 2025 | Identity handshake protocol, identity-based keying |
| v0.3.0 | Dec 2025 | Capability advertisement for peripheral-only devices |

## 9. References

- [BLE-Reticulum Protocol v2.2](BLE_PROTOCOL_v2.2.md) - Full protocol specification
- [Bluetooth Core Specification](https://www.bluetooth.com/specifications/specs/core-specification/) - BLE advertising data format
- [Bluetooth Assigned Numbers](https://www.bluetooth.com/specifications/assigned-numbers/) - Company IDs
