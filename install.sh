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
SKIP_BLUEZ_EXPERIMENTAL=false
SKIP_BT_PERMISSIONS=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --config)
            CUSTOM_CONFIG_DIR="$2"
            shift 2
            ;;
        --skip-experimental)
            SKIP_BLUEZ_EXPERIMENTAL=true
            shift
            ;;
        --skip-bt-permissions)
            SKIP_BT_PERMISSIONS=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --config CONFIG_DIR       Install to custom Reticulum config directory"
            echo "                            (default: ~/.reticulum)"
            echo "  --skip-experimental       Skip enabling BlueZ experimental mode"
            echo "                            WARNING: May cause BLE connection failures"
            echo "  --skip-bt-permissions     Skip granting Bluetooth permissions"
            echo "                            (you will need to run rnsd with sudo)"
            echo "  -h, --help               Show this help message"
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

# Add user's local bin to PATH if it exists (common pip install location)
if [ -d "$HOME/.local/bin" ]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

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

    # Add user's local bin to PATH (pip may install there)
    export PATH="$HOME/.local/bin:$PATH"

    # Verify installation (check multiple locations and Python import)
    if command -v rnsd &> /dev/null; then
        print_success "Reticulum installed successfully"
        INSTALL_MODE="system"
        RNS_PYTHON="python3"
    elif [ -f "$HOME/.local/bin/rnsd" ]; then
        print_success "Reticulum installed successfully (user installation)"
        print_info "rnsd location: $HOME/.local/bin/rnsd"
        INSTALL_MODE="system"
        RNS_PYTHON="python3"
        # Ensure it's executable
        chmod +x "$HOME/.local/bin/rnsd" 2>/dev/null || true

        # Automatically add ~/.local/bin to PATH if not already there
        if [ -f "$HOME/.bashrc" ]; then
            if ! grep -q '.local/bin' "$HOME/.bashrc" 2>/dev/null; then
                print_info "Adding ~/.local/bin to PATH in ~/.bashrc..."
                echo '' >> "$HOME/.bashrc"
                echo '# Added by Reticulum BLE installer' >> "$HOME/.bashrc"
                echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
                print_success "Added ~/.local/bin to PATH in ~/.bashrc"
                print_warning "Reload your shell to use rnsd command:"
                echo "  source ~/.bashrc"
                echo "  # Or open a new terminal"
                echo
            else
                print_info "~/.local/bin already in PATH configuration"
            fi
        fi
    elif python3 -c "import RNS" 2>/dev/null; then
        print_warning "Reticulum Python package installed, but rnsd command not found in PATH"
        print_info "You may need to add ~/.local/bin to your PATH"
        echo "  Add to ~/.bashrc: export PATH=\"\$HOME/.local/bin:\$PATH\""
        INSTALL_MODE="system"
        RNS_PYTHON="python3"
    else
        print_error "Reticulum installation failed"
        echo
        echo "Please try installing manually:"
        echo "  pip install rns"
        echo "  # Then add to PATH: export PATH=\"\$HOME/.local/bin:\$PATH\""
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
    echo "Installing: python3-pip python3-gi python3-dbus python3-cairo bluez libcap2-bin"
    # Use sudo only if not running as root
    if [ "$EUID" -eq 0 ]; then
        apt-get update
        apt-get install -y python3-pip python3-gi python3-dbus python3-cairo bluez libcap2-bin
    else
        sudo apt-get update
        sudo apt-get install -y python3-pip python3-gi python3-dbus python3-cairo bluez libcap2-bin
    fi
    print_success "System dependencies installed (using pre-compiled system packages)"
elif command -v pacman &> /dev/null; then
    # Arch Linux
    print_info "Detected Arch Linux"
    echo "Installing: base-devel gobject-introspection python-pip python-dbus python-cairo bluez bluez-utils libcap"
    print_warning "Note: PyGObject will be compiled from pip due to version requirements (bluezero needs <3.52.0, Arch has 3.54.5)"
    # Use sudo only if not running as root
    if [ "$EUID" -eq 0 ]; then
        # Sync package database first (may have been synced in basic prereqs, but ensure it's current)
        pacman -Sy --noconfirm
        # Skip python-gobject to avoid version conflict - pip will compile PyGObject
        # gobject-introspection provides dev files needed for PyGObject compilation
        pacman -S --needed --noconfirm base-devel gobject-introspection python-pip python-dbus python-cairo bluez bluez-utils libcap
    else
        sudo pacman -Sy --noconfirm
        sudo pacman -S --needed --noconfirm base-devel gobject-introspection python-pip python-dbus python-cairo bluez bluez-utils libcap
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

# Check if user wants to skip Bluetooth permissions
if [ "$SKIP_BT_PERMISSIONS" = true ]; then
    print_warning "Skipping Bluetooth permissions (--skip-bt-permissions flag)"
    print_info "You will need to run rnsd with sudo, or grant permissions manually:"
    echo "  sudo setcap 'cap_net_raw,cap_net_admin+eip' \$(readlink -f \$(which python3))"
    echo
else
    print_info "For BLE to work without root, Python needs network capabilities"
    print_info "Automatically granting capabilities to Python..."
    echo

    # Check if setcap is available
    if ! command -v setcap &> /dev/null; then
        print_error "setcap command not found"
        print_info "Installing libcap2-bin package..."

        if command -v apt-get &> /dev/null; then
            if [ "$EUID" -eq 0 ]; then
                apt-get install -y libcap2-bin
            else
                sudo apt-get install -y libcap2-bin
            fi
        elif command -v pacman &> /dev/null; then
            if [ "$EUID" -eq 0 ]; then
                pacman -S --needed --noconfirm libcap
            else
                sudo pacman -S --needed --noconfirm libcap
            fi
        else
            print_error "Could not install libcap2-bin/libcap automatically"
            print_warning "Please install manually and re-run the installer"
            echo
        fi

        # Verify setcap is now available
        if ! command -v setcap &> /dev/null; then
            print_error "setcap still not available after installation attempt"
            print_warning "You will need to run rnsd with sudo, or manually install libcap2-bin"
            echo
        else
            print_success "setcap installed successfully"
            echo
        fi
    fi

    if command -v setcap &> /dev/null; then
        # Get python3 path
        PYTHON_PATH=$(which python3)
        print_info "Detected Python at: $PYTHON_PATH"

        # Check if it's a symlink and resolve it
        if [ -L "$PYTHON_PATH" ]; then
            print_warning "Python3 is a symlink (setcap requires the actual binary)"
            PYTHON_REAL=$(readlink -f "$PYTHON_PATH")
            print_info "Resolved to actual binary: $PYTHON_REAL"

            # Verify the resolved path exists and is a file
            if [ ! -f "$PYTHON_REAL" ]; then
                print_error "Could not resolve Python binary: $PYTHON_REAL"
                print_warning "You may need to run rnsd with sudo"
                echo
            elif [ -L "$PYTHON_REAL" ]; then
                print_error "Python path is still a symlink after resolution"
                print_warning "Manual intervention required - you may need to run rnsd with sudo"
                echo
            else
                PYTHON_PATH="$PYTHON_REAL"
            fi
        fi

        # Grant capabilities if we have a valid path
        if [ -f "$PYTHON_PATH" ] && [ ! -L "$PYTHON_PATH" ]; then
            print_info "Granting capabilities to: $PYTHON_PATH"
            sudo setcap 'cap_net_raw,cap_net_admin+eip' "$PYTHON_PATH"

            if [ $? -eq 0 ]; then
                print_success "Bluetooth permissions granted successfully"
                # Verify capabilities were actually set
                if command -v getcap &> /dev/null; then
                    CAPS=$(getcap "$PYTHON_PATH" 2>/dev/null)
                    if [ -n "$CAPS" ]; then
                        print_info "Verified: $CAPS"
                    fi
                fi
            else
                print_error "Failed to grant Bluetooth permissions"
                print_warning "You may need to run rnsd with sudo"
                echo
                print_info "To grant permissions manually, run:"
                echo "  sudo setcap 'cap_net_raw,cap_net_admin+eip' $PYTHON_PATH"
            fi
        else
            print_error "Could not determine valid Python binary path"
            print_warning "You may need to run rnsd with sudo"
        fi
    else
        print_error "setcap command not available, cannot grant permissions"
        print_warning "You will need to run rnsd with sudo"
    fi

    echo
fi

# Step 5A: BlueZ Experimental Mode
print_header "BlueZ Experimental Mode"

# Check if bluetoothctl is available
if ! command -v bluetoothctl &> /dev/null; then
    print_warning "bluetoothctl not found - BlueZ may not be installed"
    print_info "BLE interface requires BlueZ for Bluetooth functionality"
    echo
elif ! command -v systemctl &> /dev/null; then
    print_warning "systemctl not found - cannot configure BlueZ experimental mode"
    print_info "This system may not use systemd, or this may be a container environment"
    echo
else
    # Detect BlueZ version
    BLUEZ_VERSION=$(bluetoothctl --version 2>/dev/null | grep -oP '\d+\.\d+' | head -1)

    if [ -z "$BLUEZ_VERSION" ]; then
        print_warning "Could not detect BlueZ version"
        echo
    else
        print_info "Detected BlueZ version: $BLUEZ_VERSION"

        # Parse version to check if >= 5.49
        VERSION_MAJOR=$(echo "$BLUEZ_VERSION" | cut -d. -f1)
        VERSION_MINOR=$(echo "$BLUEZ_VERSION" | cut -d. -f2)

        if [ "$VERSION_MAJOR" -lt 5 ] || ([ "$VERSION_MAJOR" -eq 5 ] && [ "$VERSION_MINOR" -lt 49 ]); then
            print_warning "BlueZ version $BLUEZ_VERSION does not support experimental mode (requires >= 5.49)"
            print_info "BLE interface will work with standard connection methods"
            print_info "Consider upgrading BlueZ for full BLE compatibility"
            echo
        else
            # Check if experimental mode is already enabled
            if systemctl status bluetooth 2>/dev/null | grep -q -- "-E\|--experimental"; then
                print_success "BlueZ experimental mode already enabled"
                echo
            elif [ "$SKIP_BLUEZ_EXPERIMENTAL" = true ]; then
                # User explicitly skipped experimental mode - show strong warning
                echo
                print_error "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                print_error "WARNING: Skipping BlueZ experimental mode"
                print_error "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                echo
                echo -e "${RED}BLE connections may fail with errors like:${NC}"
                echo "  • br-connection-profile-unavailable"
                echo "  • ProfileUnavailable"
                echo "  • Immediate disconnections after pairing"
                echo
                echo -e "${RED}Your BLE interface may attempt Classic Bluetooth (BR/EDR)${NC}"
                echo -e "${RED}connections instead of BLE (LE) connections.${NC}"
                echo
                echo -e "${YELLOW}This is NOT RECOMMENDED unless you have a specific reason.${NC}"
                echo
                echo "To enable experimental mode later:"
                echo "  1. sudo systemctl edit bluetooth"
                echo "  2. Add these lines:"
                echo "       [Service]"
                echo "       ExecStart="
                echo "       ExecStart=/usr/lib/bluetooth/bluetoothd -E"
                echo "  3. sudo systemctl daemon-reload"
                echo "  4. sudo systemctl restart bluetooth"
                echo
                print_error "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                echo
            else
                # Enable experimental mode by default
                print_info "Enabling BlueZ experimental mode (required for proper BLE connectivity)"
                print_info "This enables LE-specific connection methods (ConnectDevice API)"
                echo

                # Find bluetoothd path
                BLUETOOTHD_PATH=""
                for path in /usr/lib/bluetooth/bluetoothd /usr/libexec/bluetooth/bluetoothd; do
                    if [ -f "$path" ]; then
                        BLUETOOTHD_PATH="$path"
                        break
                    fi
                done

                if [ -z "$BLUETOOTHD_PATH" ]; then
                    print_error "Could not find bluetoothd binary"
                    print_warning "Tried: /usr/lib/bluetooth/bluetoothd, /usr/libexec/bluetooth/bluetoothd"
                    echo
                else
                    print_info "Using bluetoothd at: $BLUETOOTHD_PATH"

                    # Create systemd override
                    print_info "Creating systemd override for bluetooth service..."

                    # Use sudo only if not running as root
                    if [ "$EUID" -eq 0 ]; then
                        # Running as root - no sudo needed
                        mkdir -p /etc/systemd/system/bluetooth.service.d
                        cat > /etc/systemd/system/bluetooth.service.d/override.conf <<EOF
[Service]
ExecStart=
ExecStart=$BLUETOOTHD_PATH -E
EOF
                        systemctl daemon-reload
                        systemctl restart bluetooth
                    else
                        # Not root - use sudo
                        sudo mkdir -p /etc/systemd/system/bluetooth.service.d
                        sudo tee /etc/systemd/system/bluetooth.service.d/override.conf > /dev/null <<EOF
[Service]
ExecStart=
ExecStart=$BLUETOOTHD_PATH -E
EOF
                        sudo systemctl daemon-reload
                        sudo systemctl restart bluetooth
                    fi

                    # Verify bluetooth service is running
                    if systemctl is-active --quiet bluetooth; then
                        # Double-check that -E flag is actually set
                        if ps aux | grep bluetoothd | grep -q -- "-E"; then
                            print_success "BlueZ experimental mode enabled successfully"
                        else
                            print_warning "Bluetooth service restarted but -E flag not detected"
                            print_info "You may need to manually verify: ps aux | grep bluetoothd"
                        fi
                    else
                        print_error "Bluetooth service failed to start"
                        print_warning "Check status with: sudo systemctl status bluetooth"
                        echo
                    fi
                    echo
                fi
            fi
        fi
    fi
fi

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

# Add PATH note only if rnsd is in user's local bin AND not added to .bashrc
STEP_NUM=3
if [ -f "$HOME/.local/bin/rnsd" ] && [ -f "$HOME/.bashrc" ]; then
    # Only show if we didn't automatically add it (i.e., it wasn't already there)
    if ! grep -q '.local/bin' "$HOME/.bashrc" 2>/dev/null; then
        echo "3. Add ~/.local/bin to your PATH (for rnsd command):"
        echo "   echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
        echo "   source ~/.bashrc"
        echo
        STEP_NUM=4
    fi
fi

echo "$STEP_NUM. Start Reticulum:"
if [ -n "$CUSTOM_CONFIG_DIR" ]; then
    echo "   rnsd --config $CONFIG_DIR --verbose"
else
    echo "   rnsd --verbose"
fi
echo
STEP_NUM=$((STEP_NUM + 1))
echo "$STEP_NUM. Verify the interface is running:"
echo "   rnstatus"
echo

print_header "Installation Complete!"
print_success "BLE interface is ready to use"
echo
echo "For troubleshooting, see: README.md#troubleshooting"
echo "For configuration options, see: examples/config_example.toml"
echo
