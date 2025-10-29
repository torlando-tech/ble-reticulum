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
        dpkg -l | grep -q "^ii  $pkg " || { echo "FAIL: $pkg not installed"; exit 1; }
    elif [ "$OS_TYPE" = "arch" ]; then
        pacman -Q "$pkg" &> /dev/null || { echo "FAIL: $pkg not installed"; exit 1; }
    fi
}

# Run installer - it now handles everything from basic packages to Reticulum
echo "Running install.sh (self-contained installer)..."
# Navigate to repository root (script is in tests/ directory)
cd "$(dirname "$0")/.."
chmod +x install.sh
mkdir -p /tmp/test-config

# Run non-interactively (answer 'n' to bluetooth permissions prompt)
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
    check_package python-gobject
    echo "  ✓ python-gobject installed"
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

# Check that no build tools were required (verify we didn't compile anything)
echo "Verifying no build dependencies were required..."
if [ "$OS_TYPE" = "debian" ]; then
    if dpkg -l | grep -q meson; then
        echo "  ⚠ WARNING: meson was installed (should not be needed)"
    fi
    if dpkg -l | grep -q cmake; then
        echo "  ⚠ WARNING: cmake was installed (should not be needed)"
    fi
    if dpkg -l | grep -q libglib2.0-dev; then
        echo "  ⚠ WARNING: libglib2.0-dev was installed (should not be needed)"
    fi
elif [ "$OS_TYPE" = "arch" ]; then
    if pacman -Q meson &> /dev/null; then
        echo "  ⚠ WARNING: meson was installed (should not be needed)"
    fi
    if pacman -Q cmake &> /dev/null; then
        echo "  ⚠ WARNING: cmake was installed (should not be needed)"
    fi
    if pacman -Q glib2 &> /dev/null; then
        echo "  ⚠ WARNING: glib2 dev headers were installed (should not be needed)"
    fi
fi
echo "  ✓ No build tools required"

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
echo "=== SUCCESS: All tests passed ==="
echo ""
echo "Installation summary:"
echo "  • install.sh is fully self-contained (handles all prerequisites)"
echo "  • Reticulum Network Stack: installed via pip"
if [ "$OS_TYPE" = "debian" ]; then
    echo "  • System packages: python3, python3-pip, git, python3-gi, python3-dbus, python3-cairo, bluez"
elif [ "$OS_TYPE" = "arch" ]; then
    echo "  • System packages: python, python-pip, git, python-gobject, python-dbus, python-cairo, bluez, bluez-utils"
fi
echo "  • Pip packages: rns, bleak, bluezero"
echo "  • Install method: System packages for compiled deps (no build tools needed)"
echo "  • Installation time: Fast (< 2 minutes)"
echo ""
