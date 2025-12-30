#!/usr/bin/env python3
"""
Script to refactor BLEInterface.py to use the driver abstraction.

This script performs automated transformations to remove platform-specific
code and replace it with driver abstraction calls.
"""

import re

def read_file(path):
    with open(path, 'r') as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)

def refactor_imports(content):
    """Remove platform-specific imports and add driver imports."""
    # Remove bleak imports
    content = re.sub(r'# Check for bleak dependency.*?HAS_BLEAK = False\n',
                     '', content, flags=re.DOTALL)

    # Remove monkey patch code (lines 107-172 approximately)
    content = re.sub(r'# ={70,}\n# Monkey patch.*?RNS\.log\(f"Failed to apply.*?\n',
                     '', content, flags=re.DOTALL)

    # Add driver imports after BLEFragmentation imports
    driver_imports = '''
# Import driver abstraction
try:
    from bluetooth_driver import BLEDriverInterface, BLEDevice
except ImportError:
    try:
        from RNS.Interfaces.bluetooth_driver import BLEDriverInterface, BLEDevice
    except ImportError:
        # Fallback to root directory
        import sys
        import os
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
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
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
        from linux_bluetooth_driver import LinuxBluetoothDriver

HAS_DRIVER = True
'''

    # Find BLEGATTServer import section and add driver imports after
    content = re.sub(
        r'(except ImportError:\s+HAS_GATT_SERVER = False)\n',
        r'\1\n' + driver_imports + '\n',
        content
    )

    return content

def refactor_init(content):
    """Refactor __init__ method to use driver."""
    # This is complex, will need manual editing
    # For now, just remove the dependency check for bleak
    content = re.sub(
        r'        # Check dependencies\s+if not HAS_BLEAK:.*?pip install bleak==1\.1\.1"\s+\)',
        '        # Check dependencies\n        if not HAS_DRIVER:\n            raise ImportError(\n                "BLEInterface requires the driver abstraction. "\n                "Ensure bluetooth_driver.py and linux_bluetooth_driver.py are available."\n            )',
        content,
        flags=re.DOTALL
    )

    return content

def main():
    input_path = '/home/tyler/repos/public/ble-reticulum/src/RNS/Interfaces/BLEInterface.py'
    output_path = input_path  # Overwrite in place

    print(f"Reading {input_path}...")
    content = read_file(input_path)

    print("Refactoring imports...")
    content = refactor_imports(content)

    print("Refactoring __init__...")
    content = refactor_init(content)

    print(f"Writing {output_path}...")
    write_file(output_path, content)

    print("Done! Manual edits still required for:")
    print("  - __init__ method (driver creation, callbacks)")
    print("  - Remove async methods (_discover_peers, _connect_to_peer, etc.)")
    print("  - Replace BLE operations with driver calls")
    print("  - Add driver callback implementations")

if __name__ == '__main__':
    main()
