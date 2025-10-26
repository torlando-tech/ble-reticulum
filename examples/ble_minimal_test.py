#!/usr/bin/env python3
"""
Minimal BLE Interface Test

This script demonstrates basic BLE interface functionality without
requiring a full Reticulum installation. Use this for development
and testing of the BLE interface itself.

Usage:
    python ble_minimal_test.py [scan|test]

Commands:
    scan - Scan for BLE devices and show what's nearby
    test - Test fragmentation without BLE radio
"""

import sys
import os
import asyncio

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

from RNS.Interfaces.BLEFragmentation import BLEFragmenter, BLEReassembler


def test_fragmentation():
    """Test fragmentation and reassembly without BLE radio"""
    print("=" * 60)
    print("BLE Fragmentation Test")
    print("=" * 60)

    # Create fragmenter and reassembler
    mtu = 185  # Typical BLE 4.2 MTU
    fragmenter = BLEFragmenter(mtu=mtu)
    reassembler = BLEReassembler()

    # Test different packet sizes
    test_cases = [
        (50, "Small packet (no fragmentation)"),
        (185, "Exact MTU size"),
        (300, "Medium packet (2 fragments)"),
        (500, "Large packet (3 fragments)"),
    ]

    for size, description in test_cases:
        print(f"\n{description}:")
        print(f"  Packet size: {size} bytes")

        # Create test packet
        packet = bytes([0x41 + (i % 26) for i in range(size)])  # A-Z pattern

        # Fragment
        fragments = fragmenter.fragment_packet(packet)
        print(f"  Fragments: {len(fragments)}")

        # Calculate overhead
        num_frags, overhead, pct = fragmenter.get_fragment_overhead(size)
        print(f"  Overhead: {overhead} bytes ({pct:.1f}%)")

        # Show fragment details
        for i, frag in enumerate(fragments):
            frag_type = {1: "START", 2: "CONTINUE", 3: "END"}.get(frag[0], "UNKNOWN")
            print(f"    Fragment {i}: {len(frag)} bytes, type={frag_type}")

        # Reassemble
        result = None
        for frag in fragments:
            result = reassembler.receive_fragment(frag, "test_device")
            if result is not None:
                break

        # Verify
        if result == packet:
            print(f"  ✓ Reassembly successful!")
        else:
            print(f"  ✗ Reassembly failed!")
            return False

    # Show statistics
    print(f"\nReassembler Statistics:")
    stats = reassembler.get_statistics()
    for key, value in stats.items():
        print(f"  {key}: {value}")

    print("\n" + "=" * 60)
    print("All tests passed! ✓")
    print("=" * 60)
    return True


async def scan_ble_devices():
    """Scan for nearby BLE devices"""
    print("=" * 60)
    print("BLE Device Scanner")
    print("=" * 60)
    print("Scanning for BLE devices...")
    print("(This will take a few seconds)")
    print()

    try:
        from bleak import BleakScanner

        devices = await BleakScanner.discover(timeout=5.0)

        if not devices:
            print("No BLE devices found.")
            return

        print(f"Found {len(devices)} device(s):\n")

        for i, device in enumerate(devices, 1):
            print(f"{i}. {device.name or 'Unknown'}")
            print(f"   Address: {device.address}")

            # Get RSSI (API varies by bleak version)
            rssi = getattr(device, 'rssi', device.metadata.get('rssi', 'N/A') if hasattr(device, 'metadata') else 'N/A')
            print(f"   RSSI: {rssi} dBm")

            # Get UUIDs (API varies by bleak version)
            uuids = getattr(device, 'uuids', device.metadata.get("uuids", []) if hasattr(device, 'metadata') else [])
            if uuids:
                print(f"   Services: {len(uuids)} advertised")
                for uuid in uuids[:3]:  # Show first 3
                    print(f"     - {uuid}")

            print()

    except ImportError:
        print("ERROR: bleak library not installed")
        print("Install with: pip install bleak>=0.21.0")
        return
    except Exception as e:
        print(f"ERROR: {e}")
        return

    print("=" * 60)


def show_help():
    """Show usage information"""
    print("""
BLE Interface Minimal Test

Usage:
    python ble_minimal_test.py [command]

Commands:
    scan    - Scan for nearby BLE devices
    test    - Test fragmentation logic (no BLE radio needed)
    help    - Show this help message

Examples:
    # Test fragmentation
    python ble_minimal_test.py test

    # Scan for BLE devices
    python ble_minimal_test.py scan
    """)


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        command = "test"  # Default command
    else:
        command = sys.argv[1].lower()

    if command == "test":
        test_fragmentation()
    elif command == "scan":
        asyncio.run(scan_ble_devices())
    elif command == "help":
        show_help()
    else:
        print(f"Unknown command: {command}")
        show_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
