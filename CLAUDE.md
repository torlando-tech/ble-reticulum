# Claude Code Reference Guide

Quick reference for AI assistants working on the BLE-Reticulum project.

## Project Overview

A Bluetooth Low Energy (BLE) interface for [Reticulum Network Stack](https://reticulum.network), enabling mesh networking over BLE on Linux devices with BlueZ 5.x. Supports dual-mode operation (central + peripheral), multi-peer mesh networking, and automatic peer discovery.

## Key Documentation

### Protocol & Architecture
- **[BLE_PROTOCOL_v2.2.md](BLE_PROTOCOL_v2.2.md)** - Complete protocol specification
  - 5 comprehensive lifecycle sequence diagrams (Mermaid format)
  - Configuration reference (13 parameters)
  - Platform-specific workarounds (BlueZ patches)
  - MAC sorting, identity handshake, fragmentation details
  - Use this as the authoritative technical reference

- **[REFACTORING_GUIDE.md](REFACTORING_GUIDE.md)** - Driver abstraction architecture
  - Reference for implementing new platform drivers
  - Explains `BLEDriverInterface` contract

### User Documentation
- **[README.md](README.md)** - Installation, quick start, troubleshooting
- **[TESTING.md](TESTING.md)** - Test execution and procedures
- **[CONTRIBUTING.md](CONTRIBUTING.md)** - Code style and PR process

## Architecture

**Main Components:**
- `BLEInterface.py` - High-level Reticulum interface logic
- `linux_bluetooth_driver.py` - Linux platform driver (Bleak + bluezero)
- `bluetooth_driver.py` - Abstract driver interface
- `BLEGATTServer.py` - Peripheral mode GATT server
- `BLEFragmentation.py` - MTU-based packet fragmentation/reassembly

**Driver Abstraction:** The interface uses a driver-based architecture to separate Reticulum protocol logic from platform-specific BLE implementations.

## Current Status

**Branch:** `refactor/abstraction-layer` (driver abstraction complete, awaiting merge)

**Technologies:**
- [Bleak](https://github.com/hbldh/bleak) - BLE central operations
- [bluezero](https://github.com/ukBaz/python-bluezero) - GATT server (peripheral mode)
- BlueZ 5.x - Linux Bluetooth stack

## Development Workflow

1. **Understanding the protocol:** Read BLE_PROTOCOL_v2.2.md sequence diagrams
2. **Making changes:** Follow code patterns in existing driver implementations
3. **Testing:** See TESTING.md for test execution
4. **Contributing:** Follow guidelines in CONTRIBUTING.md

## Key Files by Function

**Discovery & Connection:**
- `BLEInterface.py:_perform_discovery()` - Peer discovery and scoring
- `BLEInterface.py:_connect_to_peer()` - Connection establishment

**Data Flow:**
- `BLEFragmentation.py` - Packet fragmentation/reassembly
- `BLEInterface.py:handle_*_data()` - Data routing

**Platform Integration:**
- `linux_bluetooth_driver.py` - BlueZ interaction
- `linux_bluetooth_driver.py:apply_bluez_*_patch()` - Platform workarounds

## Quick Debugging

**Check documentation first:**
- Protocol issues → BLE_PROTOCOL_v2.2.md
- Connection failures → BLE_PROTOCOL_v2.2.md § Troubleshooting
- BlueZ quirks → BLE_PROTOCOL_v2.2.md § Platform-Specific Workarounds

**Common issues are documented** in the protocol spec with solutions.
