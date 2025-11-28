#!/bin/bash
# OpenSensor Enviroplus - One-Line Installer for Raspberry Pi
# Usage: curl -LsSf https://raw.githubusercontent.com/walkthru-earth/opensensor-enviroplus/main/install.sh | sudo bash
#
# This script:
# 1. Installs system dependencies (apt packages)
# 2. Enables I2C and SPI interfaces
# 3. Installs UV package manager
# 4. Installs opensensor-enviroplus via uv tool
# 5. Fixes sensor permissions
# 6. Prompts for reboot

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Banner
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     OpenSensor.Space Enviro+ Installer for Raspberry Pi   ║${NC}"
echo -e "${GREEN}║             https://opensensor.space                      ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check if running on Raspberry Pi
check_raspberry_pi() {
    if [[ ! -f /proc/device-tree/model ]]; then
        warn "Cannot detect Raspberry Pi. Proceeding anyway..."
        return
    fi

    model=$(cat /proc/device-tree/model 2>/dev/null || echo "Unknown")
    info "Detected: $model"

    if [[ ! "$model" =~ "Raspberry Pi" ]]; then
        warn "This doesn't appear to be a Raspberry Pi. Some features may not work."
    fi
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root (use sudo)"
    fi

    # Get the actual user (not root)
    if [[ -n "$SUDO_USER" ]]; then
        ACTUAL_USER="$SUDO_USER"
    else
        ACTUAL_USER="$USER"
    fi
    ACTUAL_HOME=$(getent passwd "$ACTUAL_USER" | cut -d: -f6)

    info "Installing for user: $ACTUAL_USER"
}

# Install system dependencies
install_system_deps() {
    info "Updating package lists..."
    apt-get update -qq

    info "Installing system dependencies..."
    apt-get install -y -qq \
        git \
        python3-dev \
        python3-cffi \
        libportaudio2 \
        i2c-tools \
        > /dev/null

    success "System dependencies installed"
}

# Enable I2C and SPI interfaces
enable_interfaces() {
    info "Enabling I2C interface..."
    if command -v raspi-config &> /dev/null; then
        raspi-config nonint do_i2c 0 2>/dev/null || warn "Could not enable I2C (may already be enabled)"
        success "I2C interface enabled"

        info "Enabling SPI interface..."
        raspi-config nonint do_spi 0 2>/dev/null || warn "Could not enable SPI (may already be enabled)"
        success "SPI interface enabled"
    else
        warn "raspi-config not found. Please enable I2C and SPI manually."
    fi
}

# Install UV package manager
install_uv() {
    info "Checking for UV package manager..."

    # Check if uv is already installed (for actual user)
    if sudo -u "$ACTUAL_USER" bash -c 'command -v uv &> /dev/null'; then
        success "UV already installed"
        return
    fi

    info "Installing UV package manager..."
    sudo -u "$ACTUAL_USER" bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'

    # Source UV environment for current session
    export PATH="$ACTUAL_HOME/.local/bin:$PATH"

    success "UV installed"
}

# Install opensensor-enviroplus
install_opensensor() {
    info "Installing opensensor-enviroplus..."

    # Ensure UV is in PATH
    export PATH="$ACTUAL_HOME/.local/bin:$PATH"

    # Install as the actual user using uv tool
    sudo -u "$ACTUAL_USER" bash -c "
        export PATH=\"$ACTUAL_HOME/.local/bin:\$PATH\"
        source \"$ACTUAL_HOME/.local/bin/env\" 2>/dev/null || true
        uv tool install opensensor-enviroplus
    "

    success "opensensor-enviroplus installed"
}

# Fix sensor permissions
fix_permissions() {
    info "Fixing sensor permissions..."

    # Add user to required groups
    for group in dialout i2c gpio spi; do
        if getent group "$group" > /dev/null 2>&1; then
            usermod -aG "$group" "$ACTUAL_USER" 2>/dev/null || true
        fi
    done

    # Create udev rules for PMS5003 serial port
    cat > /etc/udev/rules.d/99-pms5003.rules << 'EOF'
# PMS5003 Particulate Matter Sensor - allow dialout group access
KERNEL=="ttyAMA0", GROUP="dialout", MODE="0660"
KERNEL=="serial0", GROUP="dialout", MODE="0660"
EOF

    # Reload udev rules
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=tty 2>/dev/null || true

    success "Permissions configured"
}

# Create working directory
setup_working_dir() {
    info "Setting up working directory..."

    WORK_DIR="$ACTUAL_HOME/opensensor"

    sudo -u "$ACTUAL_USER" mkdir -p "$WORK_DIR"
    sudo -u "$ACTUAL_USER" mkdir -p "$WORK_DIR/output"
    sudo -u "$ACTUAL_USER" mkdir -p "$WORK_DIR/logs"

    success "Working directory: $WORK_DIR"
}

# Print next steps
print_next_steps() {
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Installation Complete!${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${YELLOW}IMPORTANT: A reboot is required for permissions to take effect.${NC}"
    echo ""
    echo "After reboot, run these commands:"
    echo ""
    echo -e "  ${BLUE}cd ~/opensensor${NC}"
    echo -e "  ${BLUE}opensensor setup${NC}              # Configure station"
    echo -e "  ${BLUE}opensensor test${NC}               # Test sensors"
    echo -e "  ${BLUE}sudo opensensor service setup${NC} # Install as service"
    echo ""
    echo "Quick commands:"
    echo -e "  ${BLUE}opensensor --help${NC}             # Show all commands"
    echo -e "  ${BLUE}opensensor info${NC}               # Show status"
    echo -e "  ${BLUE}opensensor service logs -f${NC}    # View live logs"
    echo ""
    echo "Documentation: https://github.com/walkthru-earth/opensensor-enviroplus"
    echo ""
}

# Prompt for reboot
prompt_reboot() {
    echo ""
    read -p "Reboot now? (recommended) [y/N]: " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        info "Rebooting..."
        reboot
    else
        warn "Remember to reboot before using sensors!"
    fi
}

# Main installation flow
main() {
    check_raspberry_pi
    check_root
    install_system_deps
    enable_interfaces
    install_uv
    install_opensensor
    fix_permissions
    setup_working_dir
    print_next_steps
    prompt_reboot
}

# Run main function
main "$@"
