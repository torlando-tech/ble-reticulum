#!/usr/bin/env python3
"""
Helper script to identify sections of BLEInterface.py that need refactoring.
"""

with open('/home/tyler/repos/public/ble-reticulum/src/RNS/Interfaces/BLEInterface.py', 'r') as f:
    lines = f.readlines()

# Find methods to remove
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

print("Methods to remove:")
for method in methods_to_remove:
    for i, line in enumerate(lines):
        if f'def {method}' in line or f'async def {method}' in line:
            print(f"  Line {i+1}: {line.strip()}")
            break

# Find key sections
print("\nKey sections:")
for i, line in enumerate(lines):
    if 'class DiscoveredPeer' in line:
        print(f"  DiscoveredPeer class: line {i+1}")
    elif 'class BLEInterface' in line:
        print(f"  BLEInterface class: line {i+1}")
    elif 'class BLEPeerInterface' in line:
        print(f"  BLEPeerInterface class: line {i+1}")
    elif line.strip().startswith('def __init__(self, owner, configuration)'):
        print(f"  BLEInterface.__init__: line {i+1}")
    elif '_score_peer' in line and 'def' in line:
        print(f"  _score_peer: line {i+1}")
    elif '_handle_ble_data' in line and 'def' in line:
        print(f"  _handle_ble_data: line {i+1}")
