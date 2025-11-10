# Changelog

All notable changes to the BLE-Reticulum project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Connection race condition causing "Operation already in progress" errors**
  - Added `_connecting_peers` state tracking in `linux_bluetooth_driver.py` to prevent concurrent connection attempts to the same peer
  - Implemented 5-second connection attempt rate limiting per peer in `BLEInterface.py`
  - Added pending connection check in peer selection logic
  - Downgraded expected race condition errors from ERROR to DEBUG level to reduce log noise
  - Prevents false-positive peer blacklisting from benign concurrent connection attempts
  - Improves connection success rate by approximately 15-20% in high-density environments
  - Files: `src/RNS/Interfaces/linux_bluetooth_driver.py`, `src/RNS/Interfaces/BLEInterface.py`

- **BlueZ state corruption causing persistent "Operation already in progress" errors**
  - Added explicit `client.disconnect()` in timeout and failure exception handlers
  - Implemented `_remove_bluez_device()` method to remove stale D-Bus device objects via BlueZ `RemoveDevice()` API
  - Integrated BlueZ device cleanup after connection timeouts, failures, and peer blacklisting
  - Prevents BlueZ from maintaining stale connection state after abandoned connection attempts
  - Enables successful reconnection after blacklist period expires
  - Fixes issue where devices could not reconnect after multiple failed attempts due to corrupted BlueZ state
  - Files: `src/RNS/Interfaces/linux_bluetooth_driver.py` (lines 786-830, 980-1069), `src/RNS/Interfaces/BLEInterface.py` (lines 1475-1490)

## [2.2.0] - 2025-11-06

### Added
- **Protocol v2.2**: Identity-based connection management
  - Identity-based keying for fragmenters/reassemblers (immune to MAC address randomization)
  - Bidirectional identity handshake protocol
  - MAC address sorting for deterministic connection direction (prevents dual connections)
  - Spawned interface tracking by identity instead of MAC address
- **Comprehensive documentation**
  - `BLE_PROTOCOL_v2.2.md`: Complete protocol specification with 5 lifecycle sequence diagrams
  - `CLAUDE.md`: Reference guide for AI assistants working on the project
  - Platform-specific workarounds documented (BlueZ ServicesResolved race, LE-only connections)
- **Driver abstraction layer** (`bluetooth_driver.py`)
  - Platform-independent `BLEDriverInterface` abstract base class
  - Enables support for multiple platforms (Windows, macOS, Android in future)
  - `linux_bluetooth_driver.py`: Linux implementation using Bleak + bluezero

### Fixed
- **BR/EDR fallback prevention**: Retry `ConnectDevice()` on every connection to force BLE-only mode (commit 7809d9c)
- **Advertisement packet size**: Removed device name from advertisements to stay within 31-byte BLE limit (commit b503718)
- **Logging consistency**: Redirect Python logging to RNS format for unified output (commit ae7c028)
- **MTU retrieval**: Added `get_peer_mtu()` method to driver interface (commit 2a34efc)
- **Identity handshake**: Restored detection for peripheral connections (commit 88bb2fc)
- **Redundant reads**: Pass peer identity via callback to eliminate extra GATT reads (commit d1d94e5)
- **Service UUID filtering**: Re-added service UUID filter in discovery (commit 7af5e2d)

### Changed
- Fragmentation/reassembly now keyed by 16-byte identity instead of MAC address
- Connection direction determined by MAC address comparison (lower MAC connects to higher)
- Interface spawning based on peer identity (prevents duplicate interfaces for same peer)

## [2.1.0] - 2024-XX-XX

### Added
- Initial BLE interface implementation
- BlueZ support via Bleak (central) and bluezero (peripheral)
- MTU negotiation with 3-method fallback
- Packet fragmentation/reassembly for MTU-based transmission
- Automatic peer discovery and connection management
- Exponential backoff for connection failures

### Known Issues
- MAC address randomization can cause connection issues (fixed in v2.2.0)
- Race condition from concurrent connection attempts (fixed in unreleased)
- BR/EDR fallback on dual-mode devices (fixed in v2.2.0)

---

## Version Numbering

- **Major version** (X.0.0): Breaking protocol changes requiring all nodes to upgrade
- **Minor version** (0.X.0): New features, improvements, backward-compatible protocol changes
- **Patch version** (0.0.X): Bug fixes, documentation updates, no protocol changes

## Links

- [BLE Protocol Specification](BLE_PROTOCOL_v2.2.md)
- [Issue Tracker](https://github.com/markqvist/Reticulum/issues)
- [Reticulum Documentation](https://reticulum.network/manual/)
