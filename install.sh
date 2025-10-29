#!/bin/bash
# Reticulum BLE Interface Installation Script
# This script installs the BLE interface and all its prerequisites
# Handles: basic system packages, Reticulum Network Stack, system dependencies, and BLE interface

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print functions
print_header() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

# Helper function: pip install with compatibility across all OS versions
pip_install() {
    local packages="$*"

    # Check if pip supports --break-system-packages flag (pip 23.0+, PEP 668)
    if pip3 install --help 2>/dev/null | grep -q -- --break-system-packages; then
        # Debian 12+, Trixie, Ubuntu 24.04+ with externally-managed-environment
        pip3 install $packages --break-system-packages
    else
        # Ubuntu 22.04 and earlier (pip 22.x without the flag)
        pip3 install $packages
    fi
}

# Parse command line arguments
CUSTOM_CONFIG_DIR=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --config)
            CUSTOM_CONFIG_DIR="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--config CONFIG_DIR]"
            echo ""
            echo "Options:"
            echo "  --config CONFIG_DIR    Install to custom Reticulum config directory"
            echo "                         (default: ~/.reticulum)"
            echo "  -h, --help            Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Check if running on Linux
if [[ "$OSTYPE" != "linux-gnu"* ]]; then
    print_error "This interface only works on Linux (requires BlueZ)"
    exit 1
fi

# Detect CI environment and configure non-interactive mode
if [[ -n "$CI" ]] || [[ -n "$GITHUB_ACTIONS" ]] || [[ -n "$DEBIAN_FRONTEND" ]]; then
    export DEBIAN_FRONTEND=noninteractive
    export DEBCONF_NONINTERACTIVE_SEEN=true
fi

print_header "Reticulum BLE Interface Installer"
echo

# Step 0: Ensure basic prerequisites are installed
print_header "Checking Basic Prerequisites"

# Check if we have package manager access
if command -v apt-get &> /dev/null; then
    # Debian/Ubuntu - check for basic packages
    MISSING_PACKAGES=()
    for pkg in python3 python3-pip git; do
        if ! dpkg -l | grep -q "^ii  $pkg "; then
            MISSING_PACKAGES+=($pkg)
        fi
    done

    if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
        print_info "Installing basic prerequisites: ${MISSING_PACKAGES[*]}"
        # Use sudo only if not running as root (Debian containers run as root without sudo)
        if [ "$EUID" -eq 0 ]; then
            apt-get update -qq
            apt-get install -y -q ${MISSING_PACKAGES[*]}
        else
            sudo apt-get update -qq
            sudo apt-get install -y -q ${MISSING_PACKAGES[*]}
        fi
        print_success "Basic prerequisites installed"
    else
        print_success "Basic prerequisites already installed"
    fi
elif command -v pacman &> /dev/null; then
    # Arch Linux - check for basic packages
    MISSING_PACKAGES=()
    for pkg in python python-pip git; do
        if ! pacman -Q $pkg &> /dev/null; then
            MISSING_PACKAGES+=($pkg)
        fi
    done

    if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
        print_info "Installing basic prerequisites: ${MISSING_PACKAGES[*]}"
        # Use sudo only if not running as root
        if [ "$EUID" -eq 0 ]; then
            # Sync package database first (required in fresh containers)
            pacman -Sy --noconfirm
            pacman -S --noconfirm ${MISSING_PACKAGES[*]}
        else
            sudo pacman -Sy --noconfirm
            sudo pacman -S --noconfirm ${MISSING_PACKAGES[*]}
        fi
        print_success "Basic prerequisites installed"
    else
        print_success "Basic prerequisites already installed"
    fi
fi

echo

# Step 1: Check for Reticulum installation
print_header "Checking for Reticulum"

RNS_VENV=""
RNS_PYTHON=""
INSTALL_MODE=""

# Check if rnsd is available
if command -v rnsd &> /dev/null; then
    print_success "Found rnsd command"

    # Try to import RNS and find its location
    RNS_LOCATION=$(python3 -c "import RNS; print(RNS.__file__)" 2>/dev/null || echo "")

    if [ -n "$RNS_LOCATION" ]; then
        print_success "Found RNS Python package at: $RNS_LOCATION"

        # Check if it's in a virtual environment
        if [[ "$RNS_LOCATION" == *"/venv/"* ]] || [[ "$RNS_LOCATION" == *"/env/"* ]] || [[ "$VIRTUAL_ENV" != "" ]]; then
            # RNS is in a venv
            if [ -n "$VIRTUAL_ENV" ]; then
                RNS_VENV="$VIRTUAL_ENV"
                print_info "RNS is installed in active virtual environment: $VIRTUAL_ENV"
            else
                # Try to find the venv root
                RNS_VENV=$(echo "$RNS_LOCATION" | grep -oP '^.*?/(venv|env)' || echo "")
                if [ -n "$RNS_VENV" ]; then
                    print_info "RNS is installed in virtual environment: $RNS_VENV"
                fi
            fi
            INSTALL_MODE="venv"
            RNS_PYTHON="$RNS_VENV/bin/python3"
        else
            # RNS is system-wide
            print_info "RNS is installed system-wide"
            INSTALL_MODE="system"
            RNS_PYTHON="python3"
        fi
    fi
else
    print_warning "Reticulum (rnsd) not found"
    print_info "Installing Reticulum Network Stack..."

    # Install Reticulum using our pip helper function
    pip_install rns

    # Verify installation
    if command -v rnsd &> /dev/null; then
        print_success "Reticulum installed successfully"
        INSTALL_MODE="system"
        RNS_PYTHON="python3"
    else
        print_error "Reticulum installation failed"
        echo
        echo "Please try installing manually:"
        echo "  pip install rns"
        echo "Or visit: https://reticulum.network"
        exit 1
    fi
fi

echo

# Step 2: Install system dependencies
print_header "Installing System Dependencies"

if command -v apt-get &> /dev/null; then
    # Debian/Ubuntu/Raspberry Pi OS
    print_info "Detected Debian/Ubuntu-based system"
    echo "Installing: python3-pip python3-gi python3-dbus python3-cairo bluez"
    # Use sudo only if not running as root
    if [ "$EUID" -eq 0 ]; then
        apt-get update
        apt-get install -y python3-pip python3-gi python3-dbus python3-cairo bluez
    else
        sudo apt-get update
        sudo apt-get install -y python3-pip python3-gi python3-dbus python3-cairo bluez
    fi
    print_success "System dependencies installed (using pre-compiled system packages)"
elif command -v pacman &> /dev/null; then
    # Arch Linux
    print_info "Detected Arch Linux"
    echo "Installing: base-devel python-pip python-dbus python-cairo bluez bluez-utils"
    print_warning "Note: PyGObject will be compiled from pip due to version requirements (bluezero needs <3.52.0, Arch has 3.54.5)"
    # Use sudo only if not running as root
    if [ "$EUID" -eq 0 ]; then
        # Sync package database first (may have been synced in basic prereqs, but ensure it's current)
        pacman -Sy --noconfirm
        # Skip python-gobject to avoid version conflict - pip will compile PyGObject
        pacman -S --needed --noconfirm base-devel python-pip python-dbus python-cairo bluez bluez-utils
    else
        sudo pacman -Sy --noconfirm
        sudo pacman -S --needed --noconfirm base-devel python-pip python-dbus python-cairo bluez bluez-utils
    fi
    print_success "System dependencies installed (PyGObject will be compiled from pip)"
else
    print_warning "Could not detect package manager"
    print_info "Please manually install: BlueZ 5.x, python3-dbus"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo

# Step 3: Install Python dependencies
print_header "Installing Python Dependencies"

print_info "Installing pip packages (PyGObject, dbus-python, pycairo provided by system packages)"

if [ "$INSTALL_MODE" = "venv" ]; then
    print_info "Installing to virtual environment: $RNS_VENV"

    if [ ! -f "$RNS_PYTHON" ]; then
        print_error "Python not found at: $RNS_PYTHON"
        exit 1
    fi

    # Activate venv and install
    source "$RNS_VENV/bin/activate"

    # Install only packages not provided by system packages
    # System packages provide: PyGObject (gi), dbus-python (dbus), pycairo (cairo)
    # We need to install: bleak, bluezero
    pip_install bleak==1.1.1 bluezero
    print_success "Python dependencies installed in virtual environment"
    print_info "Note: Using system-provided PyGObject, dbus-python, and pycairo"

elif [ "$INSTALL_MODE" = "system" ]; then
    print_info "Installing system-wide Python packages"

    # Install only packages not provided by system packages
    # Use pip_install helper for compatibility
    pip_install bleak==1.1.1 bluezero
    print_success "Python dependencies installed"
    print_info "Note: Using system-provided PyGObject, dbus-python, and pycairo"
else
    print_error "Could not determine installation mode"
    exit 1
fi

echo

# Step 4: Copy BLE interface files
print_header "Installing BLE Interface Files"

# Determine where to copy files
if [ -n "$CUSTOM_CONFIG_DIR" ]; then
    # Use custom config directory if specified
    CONFIG_DIR="$CUSTOM_CONFIG_DIR"
    print_info "Using custom config directory: $CONFIG_DIR"
else
    # Default to ~/.reticulum
    CONFIG_DIR="$HOME/.reticulum"
    print_info "Using default config directory: $CONFIG_DIR"
fi

INTERFACES_DIR="$CONFIG_DIR/interfaces"

# Create directory if it doesn't exist
mkdir -p "$INTERFACES_DIR"

# Copy interface files
print_info "Copying BLE interface files to: $INTERFACES_DIR"
cp src/RNS/Interfaces/BLE*.py "$INTERFACES_DIR/"

# Create __init__.py if it doesn't exist
if [ ! -f "$INTERFACES_DIR/__init__.py" ]; then
    touch "$INTERFACES_DIR/__init__.py"
fi

print_success "BLE interface files installed"
echo "  - BLEInterface.py"
echo "  - BLEGATTServer.py"
echo "  - BLEFragmentation.py"
echo "  - BLEAgent.py"

echo

# Step 5: Bluetooth permissions
print_header "Bluetooth Permissions"

print_info "For BLE to work without root, Python needs network capabilities"
echo

PYTHON_PATH=$(which python3)

echo "The following command will grant capabilities to Python:"
echo "  sudo setcap 'cap_net_raw,cap_net_admin+eip' $PYTHON_PATH"
echo

read -p "Grant Bluetooth permissions now? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo setcap 'cap_net_raw,cap_net_admin+eip' "$PYTHON_PATH"
    print_success "Bluetooth permissions granted"
else
    print_warning "Skipped. You may need to run rnsd with sudo"
    echo "  To grant permissions later, run:"
    echo "  sudo setcap 'cap_net_raw,cap_net_admin+eip' \$(which python3)"
fi

echo

# Step 6: Configuration
print_header "Configuration"

CONFIG_FILE="$CONFIG_DIR/config"

print_info "Next steps:"
echo
echo "1. Add the BLE interface to your Reticulum config:"
echo "   File: $CONFIG_FILE"
echo
echo "   Add this section:"
echo "   ┌─────────────────────────────────────────┐"
echo "   │ [[BLE Interface]]                       │"
echo "   │   type = BLEInterface                   │"
echo "   │   enabled = yes                         │"
echo "   │                                         │"
echo "   │   # Enable both modes for mesh          │"
echo "   │   enable_peripheral = yes               │"
echo "   │   enable_central = yes                  │"
echo "   └─────────────────────────────────────────┘"
echo
echo "2. See examples/config_example.toml for all configuration options"
echo
echo "3. Start Reticulum:"
if [ -n "$CUSTOM_CONFIG_DIR" ]; then
    echo "   rnsd --config $CONFIG_DIR --verbose"
else
    echo "   rnsd --verbose"
fi
echo
echo "4. Verify the interface is running:"
echo "   rnstatus"
echo

print_header "Installation Complete!"
print_success "BLE interface is ready to use"
echo
echo "For troubleshooting, see: README.md#troubleshooting"
echo "For configuration options, see: examples/config_example.toml"
echo
