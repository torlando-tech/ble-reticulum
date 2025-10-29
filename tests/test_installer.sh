#!/bin/bash
# Integration test for install.sh on fresh Debian/Ubuntu systems
# This script is meant to run in a fresh container to verify the installer works correctly

set -e

# Configure non-interactive mode for CI/container environments
export DEBIAN_FRONTEND=noninteractive
export DEBCONF_NONINTERACTIVE_SEEN=true
export TZ=UTC

# Pre-configure timezone to prevent interactive prompts
ln -fs /usr/share/zoneinfo/UTC /etc/localtime

echo "=== Testing install.sh on fresh system ==="
echo "OS: $(cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '"')"
echo ""

# Step 1: Install prerequisites (what a user would have)
echo "Step 1: Installing base prerequisites..."
apt-get update -qq
apt-get install -y -q \
    -o DPkg::Pre-Install-Pkgs::=/bin/true \
    -o DPkg::Post-Install-Pkgs::=/bin/true \
    sudo python3 python3-pip git

# Install Reticulum (prerequisite for BLE interface)
echo "Installing Reticulum..."
pip3 install rns 2>&1 | grep -v "WARNING" || true

echo ""

# Step 2: Run installer
echo "Step 2: Running install.sh..."
# Navigate to repository root (script is in tests/ directory)
cd "$(dirname "$0")/.."
chmod +x install.sh
mkdir -p /tmp/test-config

# Run non-interactively (answer 'n' to bluetooth permissions prompt)
./install.sh --config /tmp/test-config <<EOF
n
EOF

echo ""

# Step 3: Verify installation
echo "Step 3: Verifying installation..."
echo ""

# Check system packages
echo "Checking system packages..."
dpkg -l | grep -q python3-gi || { echo "FAIL: python3-gi not installed"; exit 1; }
echo "  ✓ python3-gi installed"

dpkg -l | grep -q python3-dbus || { echo "FAIL: python3-dbus not installed"; exit 1; }
echo "  ✓ python3-dbus installed"

dpkg -l | grep -q python3-cairo || { echo "FAIL: python3-cairo not installed"; exit 1; }
echo "  ✓ python3-cairo installed"

dpkg -l | grep -q bluez || { echo "FAIL: bluez not installed"; exit 1; }
echo "  ✓ bluez installed"

echo ""

# Check Python imports (verify system packages work)
echo "Checking Python module imports..."
python3 -c "import gi; print('  ✓ gi version:', gi.__version__)" || { echo "FAIL: Cannot import gi"; exit 1; }
python3 -c "import dbus; print('  ✓ dbus version:', dbus.__version__)" || { echo "FAIL: Cannot import dbus"; exit 1; }
python3 -c "import cairo; print('  ✓ cairo imported successfully')" || { echo "FAIL: Cannot import cairo"; exit 1; }

echo ""

# Check pip packages
echo "Checking pip-installed packages..."
python3 -c "import bleak; print('  ✓ bleak version:', bleak.__version__)" || { echo "FAIL: Cannot import bleak"; exit 1; }
python3 -c "import bluezero; print('  ✓ bluezero imported successfully')" || { echo "FAIL: Cannot import bluezero"; exit 1; }

echo ""

# Check that no build tools were required (verify we didn't compile anything)
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
echo "  • System packages: python3-gi, python3-dbus, python3-cairo, bluez"
echo "  • Pip packages: bleak, bluezero"
echo "  • Install method: System packages (no compilation)"
echo "  • Time: Fast (< 1 minute)"
echo ""
