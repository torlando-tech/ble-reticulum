#!/bin/bash
# Reticulum BLE Interface Installation Script
# This script installs the BLE interface to an existing Reticulum installation

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

print_header "Reticulum BLE Interface Installer"
echo

# Step 1: Check for Reticulum installation
print_info "Checking for Reticulum installation..."

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
    print_error "Reticulum (rnsd) not found!"
    echo
    echo "Please install Reticulum first:"
    echo "  pip install rns"
    echo "Or visit: https://reticulum.network"
    exit 1
fi

echo

# Step 2: Install system dependencies
print_header "Installing System Dependencies"

if command -v apt-get &> /dev/null; then
    # Debian/Ubuntu/Raspberry Pi OS
    print_info "Detected Debian/Ubuntu-based system"
    echo "Installing: python3-pip python3-dbus bluez"
    sudo apt-get update
    sudo apt-get install -y python3-pip python3-dbus bluez
    print_success "System dependencies installed"
elif command -v pacman &> /dev/null; then
    # Arch Linux
    print_info "Detected Arch Linux"
    echo "Installing: python-pip python-dbus bluez bluez-utils"
    sudo pacman -S --noconfirm python-pip python-dbus bluez bluez-utils
    print_success "System dependencies installed"
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

if [ "$INSTALL_MODE" = "venv" ]; then
    print_info "Installing to virtual environment: $RNS_VENV"

    if [ ! -f "$RNS_PYTHON" ]; then
        print_error "Python not found at: $RNS_PYTHON"
        exit 1
    fi

    # Activate venv and install
    source "$RNS_VENV/bin/activate"
    pip install -r requirements.txt
    print_success "Python dependencies installed in virtual environment"

elif [ "$INSTALL_MODE" = "system" ]; then
    print_info "Installing system-wide Python packages"

    # Try without sudo first
    if pip install -r requirements.txt 2>/dev/null; then
        print_success "Python dependencies installed (user)"
    else
        print_warning "User install failed, trying with sudo..."
        sudo pip install -r requirements.txt
        print_success "Python dependencies installed (system)"
    fi
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
