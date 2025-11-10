# Changelog

All notable changes to the BLE-Reticulum project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2025-11-10

### Fixed
- **Release workflow**: Use `gh release create` for atomic release creation to prevent asset upload failures with immutable releases. Previously, `softprops/action-gh-release` created releases and uploaded assets in separate operations, which failed when repository rules made releases immutable immediately.

## [0.1.0] - 2025-11-10

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
