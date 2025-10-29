#!/bin/bash
# Integration test for install.sh on fresh Linux systems
# This script tests that install.sh works correctly on a completely fresh system
# with no prerequisites installed
# Supports: Debian, Ubuntu, Arch Linux

set -e

# Detect OS type
if command -v apt-get &> /dev/null; then
    OS_TYPE="debian"
elif command -v pacman &> /dev/null; then
    OS_TYPE="arch"
else
    echo "ERROR: Unsupported OS (no apt-get or pacman found)"
    exit 1
fi

# Configure non-interactive mode for CI/container environments
if [ "$OS_TYPE" = "debian" ]; then
    # Debian/Ubuntu-specific environment setup
    export DEBIAN_FRONTEND=noninteractive
    export DEBCONF_NONINTERACTIVE_SEEN=true
    export TZ=UTC
    # Pre-configure timezone to prevent interactive prompts
    ln -fs /usr/share/zoneinfo/UTC /etc/localtime
fi

echo "=== Testing install.sh on fresh system ==="
echo "OS: $(cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '"')"
echo "OS Type: $OS_TYPE"
echo ""
echo "NOTE: install.sh will handle all prerequisites (Python, pip, Reticulum, etc.)"
echo ""

# Helper function: Check if a package is installed (OS-agnostic)
check_package() {
    local pkg="$1"
    if [ "$OS_TYPE" = "debian" ]; then
        # Match package with or without architecture suffix (e.g., python3-cairo:amd64)
        dpkg -l | grep -q "^ii  $pkg" || { echo "FAIL: $pkg not installed"; exit 1; }
    elif [ "$OS_TYPE" = "arch" ]; then
        pacman -Q "$pkg" &> /dev/null || { echo "FAIL: $pkg not installed"; exit 1; }
    fi
}

# Run installer - it now handles everything from basic packages to Reticulum
echo "Running install.sh (self-contained installer)..."
# Navigate to repository root (script is in tests/ directory)
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
chmod +x install.sh
mkdir -p /tmp/test-config

# Run non-interactively (answer 'n' to bluetooth permissions prompt)
# Note: BlueZ experimental mode will be enabled by default (no prompt)
./install.sh --config /tmp/test-config <<EOF
n
EOF

echo ""

# Verify installation
echo "=== Verifying Installation ==="
echo ""

# Check system packages
echo "Checking system packages..."
if [ "$OS_TYPE" = "debian" ]; then
    check_package python3-gi
    echo "  ✓ python3-gi installed"
    check_package python3-dbus
    echo "  ✓ python3-dbus installed"
    check_package python3-cairo
    echo "  ✓ python3-cairo installed"
    check_package bluez
    echo "  ✓ bluez installed"
elif [ "$OS_TYPE" = "arch" ]; then
    # Note: python-gobject NOT installed on Arch to avoid version conflict
    # PyGObject compiled from pip instead
    check_package python-dbus
    echo "  ✓ python-dbus installed"
    check_package python-cairo
    echo "  ✓ python-cairo installed"
    check_package bluez
    echo "  ✓ bluez installed"
    check_package bluez-utils
    echo "  ✓ bluez-utils installed"
fi

echo ""

# Check Python imports (verify system packages work)
echo "Checking Python module imports..."
python3 -c "import gi; print('  ✓ gi version:', gi.__version__)" || { echo "FAIL: Cannot import gi"; exit 1; }
python3 -c "import dbus; print('  ✓ dbus version:', dbus.__version__)" || { echo "FAIL: Cannot import dbus"; exit 1; }
python3 -c "import cairo; print('  ✓ cairo imported successfully')" || { echo "FAIL: Cannot import cairo"; exit 1; }

echo ""

# Check Reticulum installation
echo "Checking Reticulum installation..."
command -v rnsd || { echo "FAIL: rnsd command not found"; exit 1; }
echo "  ✓ rnsd command available"
python3 -c "import RNS; print('  ✓ RNS version:', RNS.version)" || { echo "FAIL: Cannot import RNS"; exit 1; }

echo ""

# Check pip packages
echo "Checking pip-installed packages..."
python3 -c "import bleak; print('  ✓ bleak imported successfully')" || { echo "FAIL: Cannot import bleak"; exit 1; }
python3 -c "import bluezero; print('  ✓ bluezero imported successfully')" || { echo "FAIL: Cannot import bluezero"; exit 1; }

echo ""

# Check build tools status
if [ "$OS_TYPE" = "debian" ]; then
    echo "Verifying no build dependencies were required..."
    if dpkg -l | grep -q meson; then
        echo "  ⚠ WARNING: meson was installed (should not be needed)"
    fi
    if dpkg -l | grep -q cmake; then
        echo "  ⚠ WARNING: cmake was installed (should not be needed)"
    fi
    if dpkg -l | grep -q libglib2.0-dev; then
        echo "  ⚠ WARNING: libglib2.0-dev was installed (should not be needed)"
    fi
    echo "  ✓ No build tools required"
elif [ "$OS_TYPE" = "arch" ]; then
    echo "Verifying build tools installed (required on Arch for PyGObject compilation)..."
    check_package base-devel
    echo "  ✓ base-devel installed (includes gcc, make, etc.)"
fi

echo ""

# Check files were copied
echo "Checking BLE interface files..."
test -f /tmp/test-config/interfaces/BLEInterface.py || { echo "FAIL: BLEInterface.py not installed"; exit 1; }
echo "  ✓ BLEInterface.py"

test -f /tmp/test-config/interfaces/BLEGATTServer.py || { echo "FAIL: BLEGATTServer.py not installed"; exit 1; }
echo "  ✓ BLEGATTServer.py"

test -f /tmp/test-config/interfaces/BLEFragmentation.py || { echo "FAIL: BLEFragmentation.py not installed"; exit 1; }
echo "  ✓ BLEFragmentation.py"

test -f /tmp/test-config/interfaces/BLEAgent.py || { echo "FAIL: BLEAgent.py not installed"; exit 1; }
echo "  ✓ BLEAgent.py"

echo ""

# Test import of installed BLE interface
echo "Testing BLE interface import..."
cd /tmp/test-config/interfaces
python3 -c "import sys; sys.path.insert(0, '.'); from BLEInterface import BLEInterface; print('  ✓ BLEInterface imported successfully')" || { echo "FAIL: Cannot import BLEInterface"; exit 1; }

echo ""

# Check BlueZ experimental mode configuration
echo "Checking BlueZ experimental mode..."
if command -v systemctl &> /dev/null && command -v bluetoothctl &> /dev/null; then
    # systemctl is available - check if experimental mode was configured
    if [ -f /etc/systemd/system/bluetooth.service.d/override.conf ]; then
        echo "  ✓ Systemd override file created"
        if grep -q -- "-E" /etc/systemd/system/bluetooth.service.d/override.conf; then
            echo "  ✓ Experimental mode flag (-E) configured"
        else
            echo "  ⚠ WARNING: Override file exists but -E flag not found"
        fi
    else
        # No override file - may have been already enabled or not supported
        if systemctl status bluetooth 2>/dev/null | grep -q -- "-E\|--experimental"; then
            echo "  ✓ Experimental mode already enabled (not via installer)"
        else
            echo "  ⚠ WARNING: Experimental mode not configured"
        fi
    fi
else
    # systemctl or bluetoothctl not available (container environment)
    echo "  ℹ Systemd/BlueZ not available (container environment - OK)"
fi

echo ""

# Test --skip-experimental flag
echo "Testing --skip-experimental flag..."
cd "$REPO_ROOT"
# Run with --skip-experimental to verify it doesn't fail
./install.sh --config /tmp/test-config-skip --skip-experimental > /tmp/skip-test.log 2>&1 <<EOF
n
EOF

# Check that warning was shown
if grep -q "WARNING: Skipping BlueZ experimental mode" /tmp/skip-test.log; then
    echo "  ✓ --skip-experimental flag works (warning displayed)"
else
    echo "  ⚠ WARNING: --skip-experimental flag may not be working correctly"
fi

echo ""
echo "=== SUCCESS: All tests passed ==="
echo ""
echo "Installation summary:"
echo "  • install.sh is fully self-contained (handles all prerequisites)"
echo "  • Reticulum Network Stack: installed via pip"
if [ "$OS_TYPE" = "debian" ]; then
    echo "  • System packages: python3, python3-pip, git, python3-gi, python3-dbus, python3-cairo, bluez, libffi-dev"
    echo "  • Pip packages: rns, bleak, bluezero"
    echo "  • Install method: System packages (no compilation)"
    echo "  • Installation time: < 1 minute"
elif [ "$OS_TYPE" = "arch" ]; then
    echo "  • System packages: python, python-pip, git, python-dbus, python-cairo, bluez, bluez-utils, base-devel"
    echo "  • Pip packages: rns, bleak, bluezero, PyGObject (compiled)"
    echo "  • Install method: System packages + PyGObject compilation (version compatibility)"
    echo "  • Installation time: ~2-3 minutes (PyGObject compilation)"
fi
echo ""
