# Reticulum BLE Interface

A Bluetooth Low Energy (BLE) interface for [Reticulum Network Stack](https://reticulum.network), enabling mesh networking over BLE without additional hardware on Linux devices.

**⚠️ Platform**: Linux-only (requires BlueZ 5.x for GATT server functionality)
**✅ Tested on**: Raspberry Pi Zero W

## Features

- **Zero dongle requirements**: Works with built-in BLE radios (Raspberry Pi, Linux laptops, etc.)
- **Auto-discovery**: Automatically finds and connects to nearby Reticulum BLE nodes
- **Multi-peer mesh**: Supports up to 7 simultaneous connections for mesh networking (may support more, untested)
- **Dual mode operation**: Acts as both central (scanner/client) and peripheral (advertiser/server)
- **Connection prioritization**: RSSI-based smart peer selection with connection history tracking
- **Packet fragmentation**: Handles BLE MTU limitations (20-512 bytes) transparently
- **Enhanced error handling**: Retry logic, exponential backoff, connection recovery
- **Power management**: Three power modes (aggressive/balanced/saver) for battery efficiency or CPU limitations. Saver mode tested on Raspberry Pi Zero W.

## Installation

**Prerequisites:**
- Python 3.8 or higher
- Reticulum Network Stack already installed ([installation guide](https://reticulum.network))
- Linux with BlueZ 5.x

### Option A: Automated Installation (Recommended)

The installation script automatically detects your Reticulum setup and installs dependencies in the correct environment:

```bash
# Download and run installer
git clone https://github.com/torlando-tech/ble-reticulum.git
cd ble-reticulum
chmod +x install.sh
./install.sh

# For custom config directory:
# ./install.sh --config /path/to/custom/config
```

The script will:
1. ✓ Detect if Reticulum is in a venv or system-wide
2. ✓ Install system dependencies (BlueZ, dbus)
3. ✓ Install Python packages in the correct environment
4. ✓ Copy BLE interface files to `~/.reticulum/interfaces/` (or custom config directory if specified)
5. ✓ Enable BlueZ experimental mode (required for proper BLE connectivity)
6. ✓ Optionally set up Bluetooth permissions

**BlueZ Experimental Mode**: The installer automatically enables BlueZ experimental mode, which is required for proper BLE connectivity. This allows the BLE interface to use LE-specific connection methods instead of defaulting to Classic Bluetooth (BR/EDR), preventing connection errors like "br-connection-profile-unavailable".

To skip this configuration (not recommended):
```bash
./install.sh --skip-experimental
```

### Option B: Manual Installation

#### 1. Install System Dependencies

**Debian/Ubuntu/Raspberry Pi OS:**
```bash
sudo apt-get update
sudo apt-get install python3-pip python3-gi python3-dbus python3-cairo bluez
```

**Arch Linux:**
```bash
sudo pacman -S base-devel gobject-introspection python-pip python-dbus python-cairo bluez bluez-utils
```

**Why these packages?**
- `base-devel`: Build tools (gcc, make, meson) required for compiling PyGObject
- `gobject-introspection`: Development files for GObject introspection (required for PyGObject compilation)
- `python-dbus`: D-Bus Python bindings for BlueZ communication
- `python-cairo`: Cairo graphics library
- `bluez` / `bluez-utils`: Bluetooth stack and utilities for Linux

**Note for Arch users:** PyGObject is intentionally NOT installed as a system package on Arch due to version incompatibility (Arch has 3.54.5, but bluezero requires <3.52.0). Instead, pip will compile the compatible PyGObject version (3.50.2) during installation. This adds ~2 minutes to installation time but ensures compatibility.

#### 2. Install Python Dependencies

**IMPORTANT:** Install in the same environment as Reticulum!

Since we installed system packages for PyGObject, dbus-python, and pycairo in step 1, we only need to install the pure-Python packages:

**If Reticulum is in a virtual environment:**
```bash
# Activate the same venv where Reticulum is installed
source /path/to/reticulum-venv/bin/activate
pip install bleak==1.1.1 bluezero
```

**If Reticulum is installed system-wide:**
```bash
# Install system-wide (may need sudo)
pip install bleak==1.1.1 bluezero
# OR
sudo pip install bleak==1.1.1 bluezero
```

**Note:** The system packages (python3-gi, python3-dbus, python3-cairo) provide PyGObject, dbus-python, and pycairo, eliminating the need for lengthy compilation from source.

#### 3. Copy BLE Interface Files

```bash
# Copy to Reticulum's interface directory
mkdir -p ~/.reticulum/interfaces
cp src/RNS/Interfaces/BLE*.py ~/.reticulum/interfaces/
```

#### 4. Enable BlueZ Experimental Mode (Required)

BlueZ experimental mode is required for proper BLE connectivity. Without it, BlueZ may attempt Classic Bluetooth (BR/EDR) connections instead of BLE (LE) connections, causing connection failures.

Enable experimental mode (BlueZ >= 5.49):
```bash
sudo systemctl edit bluetooth
```

Add these lines:
```
[Service]
ExecStart=
ExecStart=/usr/lib/bluetooth/bluetoothd -E
```

Save and restart Bluetooth:
```bash
sudo systemctl daemon-reload
sudo systemctl restart bluetooth
```

Verify it's enabled:
```bash
ps aux | grep bluetoothd
# Should show: /usr/lib/bluetooth/bluetoothd -E
```

#### 5. Grant Bluetooth Permissions

For non-root operation:
```bash
sudo setcap 'cap_net_raw,cap_net_admin+eip' $(which python3)
```

**Note:** If Reticulum is in a venv, grant permissions to that Python:
```bash
sudo setcap 'cap_net_raw,cap_net_admin+eip' /path/to/venv/bin/python3
```

## Quick Start

### 1. Configure Reticulum

Add the BLE interface to your Reticulum configuration (`~/.reticulum/config`):

```toml
[[BLE Interface]]
  type = BLEInterface
  enabled = yes

  # Optional: customize device name
  # device_name = My-Reticulum-Node
```

For detailed configuration options, see [`examples/config_example.toml`](examples/config_example.toml).

**Custom Config Directory**: If you use a custom Reticulum config directory with `--config`, the BLE interface will automatically use that directory to find its companion modules. No additional configuration needed!

### 2. Start Reticulum

```bash
rnsd --verbose
```

The interface will:
1. Start advertising as a peripheral (if enabled)
2. Scan for nearby BLE peers
3. Automatically connect to discovered peers
4. Form a mesh network with other BLE nodes

### 3. Verify Operation

```bash
# Check interface status
rnstatus

# Monitor announces
rnid -a
```

## Configuration

The BLE interface supports extensive configuration options. See [`examples/config_example.toml`](examples/config_example.toml) for a fully documented example with all available options.

### Key Configuration Options

- **`device_name`**: Advertised device name (auto-generated if not specified)
- **`service_uuid`**: BLE service UUID (must match on all devices)
- **`enable_peripheral`**: Accept incoming connections (default: yes)
- **`enable_central`**: Scan and connect to peers (default: yes)
- **`discovery_interval`**: How often to scan for new peers (default: 5.0 seconds)
- **`max_connections`**: Maximum simultaneous connections (default: 7)
- **`min_rssi`**: Minimum signal strength in dBm (default: -85)
- **`power_mode`**: Power management (aggressive/balanced/saver)

## Testing

For detailed testing information, see [TESTING.md](TESTING.md).

Quick test using example script (no BLE hardware required):
```bash
cd examples
python ble_minimal_test.py test
```

## Troubleshooting

### No peers discovered
- Verify Bluetooth is enabled: `bluetoothctl show`
- Check `service_uuid` matches on all devices
- Try `power_mode = aggressive` for faster discovery
- Increase `min_rssi` to -90 for longer range

### Connection timeouts
- Increase `connection_timeout` to 60
- Reduce `max_connections` to 3-5
- Check for BLE/WiFi interference (both use 2.4 GHz)
- Verify peer is within range (typically 10-30m)

### GATT server failed to start
- Ensure BlueZ 5.x is installed: `bluetoothd --version`
- Check Bluetooth permissions (see Installation → Manual Installation → step 4)
- Try `sudo rnsd` temporarily to verify (not recommended for production)
- Set `enable_peripheral = no` to disable peripheral mode

### Permission denied errors
- Grant capabilities to Python (see Installation → Manual Installation → step 5)
- Or run with sudo: `sudo rnsd` (not recommended)

### BR/EDR connection errors (br-connection-profile-unavailable, ProfileUnavailable)
These errors occur when BlueZ attempts Classic Bluetooth (BR/EDR) connections instead of BLE (LE) connections. This is the most common BLE connection issue.

**Symptoms:**
- Devices connect then immediately disconnect
- Errors: "br-connection-profile-unavailable", "ProfileUnavailable"
- "ConnectDevice() unavailable" in logs
- Devices get blacklisted after multiple failures

**Solution:**
Enable BlueZ experimental mode (see Installation → Manual Installation → step 4). If you used the automated installer, re-run it without `--skip-experimental`.

## Architecture

The BLE interface consists of four main components:

- **`BLEInterface.py`**: Main interface implementation, handles discovery and connections
- **`BLEGATTServer.py`**: GATT server for peripheral mode (accepting connections)
- **`BLEFragmentation.py`**: Packet fragmentation/reassembly for BLE MTU limits
- **`BLEAgent.py`**: Per-peer connection management

## Development Setup

For contributors and developers who want to work on the BLE interface code itself.

**Note:** This setup is different from the production installation above. Use a virtual environment for development to avoid conflicts.

```bash
# Clone repository
git clone https://github.com/torlando-tech/ble-reticulum.git
cd ble-reticulum

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install RNS (required for tests)
pip install rns

# Install all dependencies (runtime + development + testing)
pip install -r requirements-dev.txt

# Create package structure for tests
touch src/RNS/__init__.py
touch src/RNS/Interfaces/__init__.py

# Run tests
pytest

# Run tests with coverage
pytest --cov=src/RNS/Interfaces --cov-report=html
```

For detailed development and testing guidelines, see [CONTRIBUTING.md](CONTRIBUTING.md) and [TESTING.md](TESTING.md).

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Code style guidelines
- Pull request process
- Bug report templates
- Feature request guidelines

## Supporting
[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/B0B51NFT1Z)

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Reticulum Network Stack](https://reticulum.network) by Mark Qvist
- Built using [bleak](https://github.com/hbldh/bleak) for BLE central operations
- Built using [bluezero](https://github.com/ukBaz/python-bluezero) for GATT server

## Links

- [Reticulum Network Stack](https://reticulum.network)
- [Reticulum Documentation](https://markqvist.github.io/Reticulum/manual/)
- [Reticulum GitHub](https://github.com/markqvist/Reticulum)
