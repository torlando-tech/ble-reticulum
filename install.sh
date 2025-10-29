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

# Helper function: Detect if running in a container environment
is_container() {
    # Check for Docker container
    if [ -f /.dockerenv ]; then
        return 0
    fi
    # Check cgroup for container indicators
    if grep -q -E 'docker|lxc|containerd|kubepods' /proc/1/cgroup 2>/dev/null; then
        return 0
    fi
    return 1
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

# Step 1: Install system dependencies
print_header "Installing System Dependencies"

if command -v apt-get &> /dev/null; then
    # Debian/Ubuntu/Raspberry Pi OS
    print_info "Detected Debian/Ubuntu-based system"

    # Detect architecture for platform-specific dependencies
    ARCH=$(dpkg --print-architecture 2>/dev/null || echo "unknown")
    print_info "Detected architecture: $ARCH"

    PACKAGES="python3-pip python3-gi python3-dbus python3-cairo bluez libcap2-bin"

    # Add libffi-dev only for 32-bit ARM (armhf) - needed for cffi compilation
    # x86_64 and arm64 have pre-built cffi wheels available
    if [[ "$ARCH" == "armhf" ]]; then
        PACKAGES="$PACKAGES libffi-dev"
        print_info "32-bit ARM detected - adding libffi-dev for cffi compilation"
    fi

    echo "Installing: $PACKAGES"

    # Use sudo only if not running as root
    if [ "$EUID" -eq 0 ]; then
        apt-get update
        apt-get install -y $PACKAGES
    else
        sudo apt-get update
        sudo apt-get install -y $PACKAGES
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

# Step 2: Check for Reticulum installation
print_header "Checking for Reticulum"

RNS_VENV=""
RNS_PYTHON=""
INSTALL_MODE=""
PIPX_RNS_PATH=""

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

        # Check if it's a pipx installation (most specific, check first)
        if [[ "$RNS_LOCATION" == *"/pipx/venvs/"* ]]; then
            print_info "RNS appears to be installed via pipx"

            # Verify pipx command is available
            if ! command -v pipx &> /dev/null; then
                print_error "RNS is in a pipx path, but pipx command not found!"
                echo
                echo "Please install pipx:"
                echo "  python3 -m pip install --user pipx"
                echo "  python3 -m pipx ensurepath"
                exit 1
            fi

            # Verify RNS is listed in pipx
            if pipx list 2>/dev/null | grep -q "package rns"; then
                INSTALL_MODE="pipx"

                # Extract pipx venv path (e.g., ~/.local/pipx/venvs/rns)
                PIPX_RNS_PATH=$(echo "$RNS_LOCATION" | grep -oP '^.*?/pipx/venvs/rns')
                RNS_PYTHON="$PIPX_RNS_PATH/bin/python3"

                if [ ! -f "$RNS_PYTHON" ]; then
                    print_error "pipx Python not found at: $RNS_PYTHON"
                    exit 1
                fi

                print_success "Detected pipx installation at: $PIPX_RNS_PATH"
            else
                print_error "RNS appears to be in pipx path, but 'pipx list' doesn't show it"
                echo "Run 'pipx list' to verify your pipx installations"
                exit 1
            fi
        # Check if it's in a virtual environment
        elif [[ "$RNS_LOCATION" == *"/venv/"* ]] || [[ "$RNS_LOCATION" == *"/env/"* ]] || [[ "$VIRTUAL_ENV" != "" ]]; then
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

# Step 3: Install Python dependencies
print_header "Installing Python Dependencies"

# Download pre-built wheels for 32-bit ARM (Pi Zero W optimization)
# Saves ~15-30 minutes of compilation time for packages with C extensions
if [[ "$ARCH" == "armhf" ]] || [[ "$(uname -m)" =~ ^(armv6l|armv7l)$ ]]; then
    PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "unknown")

    if [[ "$PYTHON_VER" == "3.13" ]]; then
        print_info "Python 3.13 on 32-bit ARM detected - downloading pre-built dbus_fast wheel..."
        print_info "This saves ~20 minutes of compilation time on Pi Zero W"

        WHEEL_URL="https://github.com/torlando-tech/ble-reticulum/releases/download/armv6l-wheels-v1/dbus_fast-2.44.5-cp313-cp313-linux_armv6l.whl"
        WHEEL_FILE="/tmp/dbus_fast-armv6l-$$.whl"

        if curl -sL "$WHEEL_URL" -o "$WHEEL_FILE" 2>/dev/null; then
            if [ -f "$WHEEL_FILE" ] && [ -s "$WHEEL_FILE" ]; then
                print_success "Pre-built dbus_fast wheel downloaded (874KB)"
                pip_install "$WHEEL_FILE"
                rm -f "$WHEEL_FILE"
                print_success "dbus_fast installed from pre-built wheel"
            else
                print_warning "Download failed or file empty, will build from source if needed"
                rm -f "$WHEEL_FILE"
            fi
        else
            print_warning "Could not download pre-built wheel, will build from source if needed"
        fi
        echo
    fi
fi

if [ "$INSTALL_MODE" = "pipx" ]; then
    print_info "Installing dependencies via pipx inject..."
    print_warning "dbus-python will be compiled from source (may take 2-3 minutes)"
    echo

    # Define dependencies (must match requirements.txt)
    DEPS=("bleak==1.1.1" "bluezero" "dbus-python")

    # Inject each dependency individually
    for dep in "${DEPS[@]}"; do
        print_info "Injecting $dep into RNS environment..."

        if pipx inject rns "$dep"; then
            print_success "Injected $dep"
        else
            print_error "Failed to inject $dep"
            echo
            echo "Common causes:"
            echo "  - Missing system build dependencies (see above)"
            echo "  - Network connectivity issues"
            echo
            echo "Try manually:"
            echo "  pipx inject rns $dep --verbose"
            exit 1
        fi
        echo
    done

    # Verify all modules can be imported
    print_info "Verifying dependencies..."
    if "$RNS_PYTHON" -c "import bleak, bluezero, dbus" 2>/dev/null; then
        print_success "All dependencies verified and working"
    else
        print_error "Dependency verification failed"
        echo
        echo "Test imports manually:"
        echo "  $RNS_PYTHON -c 'import bleak, bluezero, dbus'"
        exit 1
    fi

elif [ "$INSTALL_MODE" = "venv" ]; then
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
cp src/RNS/Interfaces/BLE*.py \
   src/RNS/Interfaces/bluetooth_driver.py \
   src/RNS/Interfaces/linux_bluetooth_driver.py \
   "$INTERFACES_DIR/"

# Create __init__.py if it doesn't exist
if [ ! -f "$INTERFACES_DIR/__init__.py" ]; then
    touch "$INTERFACES_DIR/__init__.py"
fi

print_success "BLE interface files installed"
echo "  - BLEInterface.py"
echo "  - BLEGATTServer.py"
echo "  - BLEFragmentation.py"
echo "  - BLEAgent.py"
echo "  - bluetooth_driver.py"
echo "  - linux_bluetooth_driver.py"

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

    # Skip setcap when running as root (e.g., in containers) - root already has all permissions
    if [ "$EUID" -eq 0 ]; then
        print_info "Running as root - skipping capability grant (not needed)"
        print_info "Root user already has all required Bluetooth permissions"
    elif command -v setcap &> /dev/null; then
        # Determine correct Python path based on installation mode
        if [ "$INSTALL_MODE" = "pipx" ]; then
            PYTHON_PATH="$PIPX_RNS_PATH/bin/python3"
            print_info "Using pipx Python: $PYTHON_PATH"
        elif [ "$INSTALL_MODE" = "venv" ]; then
            PYTHON_PATH="$RNS_VENV/bin/python3"
            print_info "Using venv Python: $PYTHON_PATH"
        else
            PYTHON_PATH=$(which python3)
            print_info "Using system Python: $PYTHON_PATH"
        fi

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
                        # Non-fatal in container/CI environments where systemd isn't running
                        systemctl daemon-reload 2>/dev/null || true
                        systemctl restart bluetooth 2>/dev/null || true
                    else
                        # Not root - use sudo
                        sudo mkdir -p /etc/systemd/system/bluetooth.service.d
                        sudo tee /etc/systemd/system/bluetooth.service.d/override.conf > /dev/null <<EOF
[Service]
ExecStart=
ExecStart=$BLUETOOTHD_PATH -E
EOF
                        # Non-fatal in container/CI environments where systemd isn't running
                        sudo systemctl daemon-reload 2>/dev/null || true
                        sudo systemctl restart bluetooth 2>/dev/null || true
                    fi

                    # Verify bluetooth service is running (skip in container environments)
                    if systemctl is-active --quiet bluetooth 2>/dev/null; then
                        # Double-check that -E flag is actually set
                        if ps aux | grep bluetoothd | grep -q -- "-E"; then
                            print_success "BlueZ experimental mode enabled successfully"
                        else
                            print_warning "Bluetooth service restarted but -E flag not detected"
                            print_info "You may need to manually verify: ps aux | grep bluetoothd"
                        fi
                    elif command -v systemctl &> /dev/null && [ ! -f /.dockerenv ]; then
                        # Only show error if systemctl exists and we're not in a container
                        print_error "Bluetooth service failed to start"
                        print_warning "Check status with: sudo systemctl status bluetooth"
                        echo
                    else
                        # Container environment or systemd not available
                        print_info "Systemd override created (service restart skipped in container environment)"
                    fi
                    echo
                fi
            fi
        fi
    fi
fi

# Step 5B: Bluetooth Adapter Power State
print_header "Bluetooth Adapter Power State"

# Skip Bluetooth checks in container environments (no hardware access)
if is_container; then
    print_info "Container environment detected - skipping Bluetooth adapter checks"
    print_warning "Bluetooth hardware is not available in containers"
    print_info "This is expected behavior for CI/testing environments"
    echo
elif command -v bluetoothctl &> /dev/null; then
    print_info "Checking Bluetooth adapter power state..."

    # Check for rfkill blocks first (must be unblocked before power-on works)
    if command -v rfkill &> /dev/null; then
        if rfkill list bluetooth | grep -q "Soft blocked: yes"; then
            print_warning "Bluetooth adapter is soft-blocked by rfkill"
            print_info "Unblocking Bluetooth adapter..."
            # Use sudo only if not running as root (Docker containers run as root without sudo)
            if [ "$EUID" -eq 0 ]; then
                rfkill unblock bluetooth
            else
                sudo rfkill unblock bluetooth
            fi
            sleep 1

            # Verify unblock succeeded
            if rfkill list bluetooth | grep -q "Soft blocked: yes"; then
                print_error "Failed to unblock Bluetooth adapter"
                print_warning "You may need to check hardware switch or BIOS settings"
            else
                print_success "Bluetooth adapter unblocked successfully"
            fi
        fi
    fi

    # Check if adapter is powered
    if bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
        print_success "Bluetooth adapter is powered on"
    else
        print_warning "Bluetooth adapter is not powered"
        print_info "Powering on Bluetooth adapter..."

        # Power on the adapter (non-fatal in container/CI environments where D-Bus may not be running)
        echo -e "power on\nquit" | bluetoothctl > /dev/null 2>&1 || true

        # Verify it worked
        sleep 1
        if bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
            print_success "Bluetooth adapter powered on successfully"
        else
            print_error "Failed to power on Bluetooth adapter"
            echo
            print_info "Troubleshooting steps:"
            echo "  1. Check if adapter is blocked: sudo rfkill list bluetooth"
            echo "  2. Unblock if needed: sudo rfkill unblock bluetooth"
            echo "  3. Try manually: bluetoothctl power on"
            echo "  4. Verify adapter exists: bluetoothctl list"
            echo
            print_warning "BLE interface may not work until adapter is powered on"
        fi
    fi
else
    print_warning "bluetoothctl not available, cannot check adapter power state"
    print_info "Ensure Bluetooth adapter is powered on before running rnsd"
fi

echo

# Step 5C: BlueZ LE-Only Mode Configuration
print_header "BlueZ LE-Only Mode Configuration"

# Skip BlueZ configuration in container environments (no hardware access)
if is_container; then
    print_info "Container environment detected - skipping BlueZ LE-only mode configuration"
    print_warning "BlueZ configuration is not applicable in containers"
    print_info "This is expected behavior for CI/testing environments"
    echo
elif ! command -v bluetoothctl &> /dev/null; then
    print_warning "bluetoothctl not found - skipping LE-only mode configuration"
    echo
elif [ ! -f /etc/bluetooth/main.conf ]; then
    print_warning "/etc/bluetooth/main.conf not found - BlueZ config file missing"
    echo
else
    print_info "Configuring BlueZ adapter for LE-only mode (BLE-only, no BR/EDR Classic)"
    print_info "This prevents 'br-connection-profile-unavailable' errors on dual-mode hardware"
    echo

    # Check if ControllerMode is already set to 'le'
    if grep -q "^[[:space:]]*ControllerMode[[:space:]]*=[[:space:]]*le" /etc/bluetooth/main.conf 2>/dev/null; then
        print_success "ControllerMode already set to 'le' in /etc/bluetooth/main.conf"
        echo
    else
        print_info "Adding ControllerMode = le to /etc/bluetooth/main.conf..."

        # Create backup
        BACKUP_FILE="/etc/bluetooth/main.conf.backup.$(date +%Y%m%d_%H%M%S)"
        if sudo cp /etc/bluetooth/main.conf "$BACKUP_FILE" 2>/dev/null; then
            print_success "Created backup: $BACKUP_FILE"
        else
            print_warning "Could not create backup (continuing anyway)"
        fi

        # Check if [General] section exists
        if grep -q "^\[General\]" /etc/bluetooth/main.conf 2>/dev/null; then
            # [General] section exists - add ControllerMode after it
            # First, check if ControllerMode is commented out or set to something else
            if grep -q "^[[:space:]]*#[[:space:]]*ControllerMode" /etc/bluetooth/main.conf 2>/dev/null; then
                # Commented out - uncomment and set to le
                sudo sed -i 's/^[[:space:]]*#[[:space:]]*ControllerMode[[:space:]]*=.*/ControllerMode = le/' /etc/bluetooth/main.conf
                print_success "Uncommented and set ControllerMode = le"
            elif grep -q "^[[:space:]]*ControllerMode[[:space:]]*=" /etc/bluetooth/main.conf 2>/dev/null; then
                # Already exists but set to different value - update it
                sudo sed -i 's/^[[:space:]]*ControllerMode[[:space:]]*=.*/ControllerMode = le/' /etc/bluetooth/main.conf
                print_success "Updated existing ControllerMode to 'le'"
            else
                # Doesn't exist - add it after [General]
                sudo sed -i '/^\[General\]/a ControllerMode = le' /etc/bluetooth/main.conf
                print_success "Added ControllerMode = le under [General] section"
            fi
        else
            # No [General] section - add both section and setting at end
            echo "" | sudo tee -a /etc/bluetooth/main.conf > /dev/null
            echo "[General]" | sudo tee -a /etc/bluetooth/main.conf > /dev/null
            echo "ControllerMode = le" | sudo tee -a /etc/bluetooth/main.conf > /dev/null
            print_success "Added [General] section with ControllerMode = le"
        fi

        echo
        print_info "Restarting BlueZ service to apply changes..."
        if sudo systemctl restart bluetooth 2>/dev/null || sudo service bluetooth restart 2>/dev/null; then
            print_success "BlueZ service restarted successfully"
            sleep 2  # Give BlueZ time to reinitialize

            # Verify the setting was applied
            if grep -q "^[[:space:]]*ControllerMode[[:space:]]*=[[:space:]]*le" /etc/bluetooth/main.conf 2>/dev/null; then
                print_success "ControllerMode = le configuration verified"
            else
                print_warning "Could not verify ControllerMode setting - check manually"
            fi
        else
            print_error "Failed to restart BlueZ service"
            print_info "You may need to restart manually: sudo systemctl restart bluetooth"
        fi
        echo
    fi
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
echo "   Add this section (copy-paste ready):"
echo
echo "  [[BLE Interface]]"
echo "    type = BLEInterface"
echo "    enabled = yes"
echo
echo "    # Enable both modes for mesh"
echo "    enable_peripheral = yes"
echo "    enable_central = yes"
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
