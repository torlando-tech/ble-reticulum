#!/usr/bin/env python3
"""
Comprehensively refactor BLEInterface.py to use driver abstraction.
"""

def main():
    input_file = '/home/tyler/repos/public/ble-reticulum/src/RNS/Interfaces/BLEInterface.py'
    output_file = input_file  # Overwrite

    with open(input_file, 'r') as f:
        lines = f.readlines()

    new_lines = []
    skip_until = -1  # Line number to skip until
    in_method_to_remove = False
    method_indent = 0

    i = 0
    while i < len(lines):
        line = lines[i]
        line_no = i + 1

        # Skip lines we've marked for deletion
        if i < skip_until:
            i += 1
            continue

        # Remove bleak/bluezero imports (lines 99-172 approximately)
        if line_no == 99 and '# Check for bleak dependency' in line:
            # Skip until we find the end of monkey patch section (line 172)
            while i < len(lines) and not (i > 172 or 'class DiscoveredPeer' in lines[i]):
                i += 1
            # Add driver imports instead
            new_lines.append('# Import driver abstraction\n')
            new_lines.append('try:\n')
            new_lines.append('    from bluetooth_driver import BLEDriverInterface, BLEDevice\n')
            new_lines.append('except ImportError:\n')
            new_lines.append('    try:\n')
            new_lines.append('        from RNS.Interfaces.bluetooth_driver import BLEDriverInterface, BLEDevice\n')
            new_lines.append('    except ImportError:\n')
            new_lines.append('        # Fallback to root directory\n')
            new_lines.append('        import sys\n')
            new_lines.append('        import os\n')
            new_lines.append('        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))\n')
            new_lines.append('        from bluetooth_driver import BLEDriverInterface, BLEDevice\n')
            new_lines.append('\n')
            new_lines.append('# Import platform-specific driver\n')
            new_lines.append('try:\n')
            new_lines.append('    from linux_bluetooth_driver import LinuxBluetoothDriver\n')
            new_lines.append('except ImportError:\n')
            new_lines.append('    try:\n')
            new_lines.append('        from RNS.Interfaces.linux_bluetooth_driver import LinuxBluetoothDriver\n')
            new_lines.append('    except ImportError:\n')
            new_lines.append('        # Fallback to root directory\n')
            new_lines.append('        import sys\n')
            new_lines.append('        import os\n')
            new_lines.append('        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))\n')
            new_lines.append('        from linux_bluetooth_driver import LinuxBluetoothDriver\n')
            new_lines.append('\n')
            new_lines.append('HAS_DRIVER = True\n')
            new_lines.append('\n')
            continue

        # Detect methods to remove
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

        # Check if we're entering a method to remove
        if any(f'def {method}' in line for method in methods_to_remove):
            # Get the indent level of this method
            method_indent = len(line) - len(line.lstrip())
            in_method_to_remove = True
            RNS.log(f"Removing method at line {line_no}: {line.strip()[:50]}")
            i += 1
            continue

        # If we're in a method to remove, skip until we find the next method or class
        if in_method_to_remove:
            current_indent = len(line) - len(line.lstrip())
            # If we find a line at the same or less indent (and it's not blank), we've exited the method
            if line.strip() and current_indent <= method_indent:
                in_method_to_remove = False
                # Don't skip this line, process it normally
            else:
                i += 1
                continue

        # Add the line
        new_lines.append(line)
        i += 1

    # Write output
    with open(output_file, 'w') as f:
        f.writelines(new_lines)

    print(f"Refactored {len(lines)} lines to {len(new_lines)} lines")
    print(f"Removed {len(lines) - len(new_lines)} lines")

if __name__ == '__main__':
    main()
