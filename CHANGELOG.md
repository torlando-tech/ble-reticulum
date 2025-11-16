# Changelog

All notable changes to the BLE-Reticulum project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.2] - 2025-11-15

### Added
- pipx installation support with automated D-Bus dependency handling
- BlueZ LE-only mode configuration in installer (prevents BR/EDR fallback)
- Scanner watchdog to detect and recover from Bluetooth stack corruption
- Service UUID filtering for more efficient peer discovery
- Pre-built wheel support for Pi Zero W Python 3.13 (saves 20+ min install time)

### Fixed
- Connection race conditions from concurrent connection attempts to the same peer
- BlueZ D-Bus state corruption after connection timeouts/failures - devices can now reconnect after blacklist period
- Scanner interference causing "Operation already in progress" during connections
- GATT server race where services weren't available on D-Bus before first connection attempt
- D-Bus disconnect monitoring switched to ObjectManager with polling fallback
- Peripheral disconnect cleanup preventing new connections after hitting peer limit
- Identity mapping cleanup on disconnect (prevents stale peer tracking)
- RSSI sentinel value filtering (-127 from BlueZ)
- Columba Android compatibility (filter 1-byte keepalive packets)

### Changed
- Refactored to driver-based architecture (future Windows/macOS/Android support)

## [0.1.1] - 2025-11-10

### Fixed
- **Release workflow**: Use `gh release create` for atomic release creation to prevent asset upload failures with immutable releases. Previously, `softprops/action-gh-release` created releases and uploaded assets in separate operations, which failed when repository rules made releases immutable immediately.

## [0.1.0] - Unreleased

### Added
- **Installation system**
  - Cross-platform installer script (`install.sh`) supporting Debian, Ubuntu, Arch Linux, and Raspberry Pi OS
  - ARM architecture support (32-bit armhf and 64-bit arm64)
  - Custom configuration directory support via `--config` flag
  - Python symlink resolution for correct interpreter detection
  - Automatic PATH configuration for user installations

- **BlueZ configuration automation**
  - Automatic BlueZ experimental mode enablement (fixes BLE connection issues)
  - Bluetooth adapter auto-power-on functionality
  - rfkill auto-unblocking for Bluetooth devices
  - Systemd service integration with proper permissions

- **CI/CD infrastructure**
  - GitHub Actions workflows for automated testing
  - Multi-distribution testing matrix (Debian, Ubuntu, Arch, Raspberry Pi OS)
  - ARM architecture testing on Raspberry Pi OS
  - Non-interactive installation mode for CI environments

- **Installer robustness**
  - Root/non-root detection with appropriate sudo handling
  - Graceful degradation when systemd unavailable
  - Virtual environment detection and support
  - Compatibility with PEP 668 (externally-managed-environment)
  - Platform-specific dependency handling (libffi-dev for 32-bit ARM)

### Changed
- Improved error messages and user feedback during installation
- Enhanced logging for troubleshooting installation issues

### Fixed
- Path handling for system vs. user installations
- Permission issues with Bluetooth capabilities (setcap)
- Dependency resolution across different Linux distributions
- PyGObject version conflicts on Arch Linux

## [2.2.0] - Unreleased

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

## [2.1.0] - Unreleased

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
