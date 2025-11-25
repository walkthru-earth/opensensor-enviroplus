"""
Smart systemd service manager with automatic path detection.
Handles installation, configuration, and management of opensensor as a systemd service.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Tuple


class ServiceManager:
    """Manages systemd service for opensensor with auto-detection of all paths."""

    SERVICE_NAME = "opensensor"

    def __init__(self):
        """Initialize service manager with auto-detected paths."""
        # Auto-detect all paths
        self.user = self._detect_user()
        self.group = self._detect_group()
        self.project_root = self._detect_project_root()
        self.venv_path = self._detect_venv_path()
        self.python_path = self._detect_python_executable()
        self.env_file = self.project_root / ".env"
        self.service_file = Path(f"/etc/systemd/system/{self.SERVICE_NAME}.service")

    def _detect_user(self) -> str:
        """Detect the actual user running the application (not root/sudo)."""
        # If running under sudo, get the original user
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user:
            return sudo_user

        # Otherwise use current user
        return os.environ.get("USER", "root")

    def _detect_group(self) -> str:
        """Detect the primary group of the user."""
        try:
            import grp
            import pwd

            user_info = pwd.getpwnam(self.user)
            group_info = grp.getgrgid(user_info.pw_gid)
            return group_info.gr_name
        except (ImportError, KeyError):
            # Fallback to same as user
            return self.user

    def _detect_project_root(self) -> Path:
        """
        Detect the project root directory.
        Works by finding the directory containing pyproject.toml.
        """
        # Start from the current file's directory
        current = Path(__file__).resolve()

        # Walk up the directory tree
        for parent in [current] + list(current.parents):
            if (parent / "pyproject.toml").exists():
                return parent

        # Fallback: use current working directory
        cwd = Path.cwd()
        if (cwd / "pyproject.toml").exists():
            return cwd

        # Last resort: use the package installation directory
        return Path(__file__).resolve().parent.parent.parent.parent

    def _detect_venv_path(self) -> Path:
        """Detect the virtual environment path (usually .venv in project root)."""
        # Check if we're running in a virtual environment
        venv = os.environ.get("VIRTUAL_ENV")
        if venv:
            return Path(venv)

        # Check for .venv in project root
        venv_path = self.project_root / ".venv"
        if venv_path.exists():
            return venv_path

        # Check for venv in project root
        venv_path = self.project_root / "venv"
        if venv_path.exists():
            return venv_path

        # Fallback: assume .venv
        return self.project_root / ".venv"

    def _detect_python_executable(self) -> Path:
        """Detect the Python executable in the virtual environment."""
        python_path = self.venv_path / "bin" / "python"

        if python_path.exists():
            return python_path

        # Fallback to python3
        python_path = self.venv_path / "bin" / "python3"
        if python_path.exists():
            return python_path

        # Last resort: use current Python
        return Path(sys.executable)

    def _check_sudo(self) -> bool:
        """Check if running with sudo/root privileges."""
        return os.geteuid() == 0

    def _require_sudo(self) -> None:
        """Raise error if not running with sudo privileges."""
        if not self._check_sudo():
            raise PermissionError(
                "This operation requires sudo privileges. Run with: sudo opensensor service ..."
            )

    def _run_systemctl(self, *args: str) -> Tuple[int, str, str]:
        """
        Run systemctl command and return (returncode, stdout, stderr).

        Args:
            *args: Arguments to pass to systemctl

        Returns:
            Tuple of (returncode, stdout, stderr)
        """
        try:
            result = subprocess.run(
                ["systemctl", *args],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return 1, "", "Command timed out"
        except FileNotFoundError:
            return 1, "", "systemctl command not found (is systemd installed?)"

    def _generate_service_content(self) -> str:
        """Generate systemd service file content with auto-detected paths."""
        # Detect the opensensor CLI entry point
        cli_path = self.venv_path / "bin" / "opensensor"

        # Build PATH environment variable
        venv_bin = self.venv_path / "bin"
        path_env = f"{venv_bin}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

        return f"""[Unit]
Description=OpenSensor Enviro+ Data Collector
Documentation=https://opensensor.space
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={self.user}
Group={self.group}
WorkingDirectory={self.project_root}
Environment=PATH={path_env}
EnvironmentFile={self.env_file}
ExecStart={cli_path} start --foreground
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier={self.SERVICE_NAME}

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={self.project_root}/output {self.project_root}/logs

[Install]
WantedBy=multi-user.target
"""

    def install(self) -> None:
        """Create and install the systemd service file."""
        self._require_sudo()

        # Check if .env exists
        if not self.env_file.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {self.env_file}\n"
                "Run 'opensensor setup' first to create configuration."
            )

        # Generate service content
        service_content = self._generate_service_content()

        # Write service file
        self.service_file.write_text(service_content)

        # Reload systemd daemon
        returncode, stdout, stderr = self._run_systemctl("daemon-reload")
        if returncode != 0:
            raise RuntimeError(f"Failed to reload systemd daemon: {stderr}")

    def uninstall(self) -> None:
        """Remove the systemd service file."""
        self._require_sudo()

        if not self.service_file.exists():
            raise FileNotFoundError(f"Service file not found: {self.service_file}")

        # Remove service file
        self.service_file.unlink()

        # Reload systemd daemon
        self._run_systemctl("daemon-reload")

    def enable(self) -> None:
        """Enable the service to start on boot."""
        self._require_sudo()

        returncode, stdout, stderr = self._run_systemctl("enable", self.SERVICE_NAME)
        if returncode != 0:
            raise RuntimeError(f"Failed to enable service: {stderr}")

    def disable(self) -> None:
        """Disable the service from starting on boot."""
        self._require_sudo()

        returncode, stdout, stderr = self._run_systemctl("disable", self.SERVICE_NAME)
        if returncode != 0:
            raise RuntimeError(f"Failed to disable service: {stderr}")

    def start(self) -> None:
        """Start the service."""
        self._require_sudo()

        returncode, stdout, stderr = self._run_systemctl("start", self.SERVICE_NAME)
        if returncode != 0:
            raise RuntimeError(f"Failed to start service: {stderr}")

    def stop(self) -> None:
        """Stop the service."""
        self._require_sudo()

        returncode, stdout, stderr = self._run_systemctl("stop", self.SERVICE_NAME)
        if returncode != 0:
            raise RuntimeError(f"Failed to stop service: {stderr}")

    def restart(self) -> None:
        """Restart the service."""
        self._require_sudo()

        returncode, stdout, stderr = self._run_systemctl("restart", self.SERVICE_NAME)
        if returncode != 0:
            raise RuntimeError(f"Failed to restart service: {stderr}")

    def status(self) -> Tuple[str, bool]:
        """
        Get service status.

        Returns:
            Tuple of (status_output, is_active)
        """
        # Get detailed status
        returncode, stdout, stderr = self._run_systemctl("status", self.SERVICE_NAME)

        # Check if active
        _, active_stdout, _ = self._run_systemctl("is-active", self.SERVICE_NAME)
        is_active = active_stdout.strip() == "active"

        return stdout if stdout else stderr, is_active

    def is_installed(self) -> bool:
        """Check if the service file exists."""
        return self.service_file.exists()

    def is_enabled(self) -> bool:
        """Check if the service is enabled."""
        returncode, stdout, _ = self._run_systemctl("is-enabled", self.SERVICE_NAME)
        return returncode == 0 and stdout.strip() == "enabled"

    def is_active(self) -> bool:
        """Check if the service is currently running."""
        returncode, stdout, _ = self._run_systemctl("is-active", self.SERVICE_NAME)
        return returncode == 0 and stdout.strip() == "active"

    def get_logs(self, lines: int = 50, follow: bool = False) -> None:
        """
        Show service logs using journalctl.

        Args:
            lines: Number of lines to show
            follow: Follow log output
        """
        cmd = ["journalctl", "-u", self.SERVICE_NAME, "-n", str(lines)]

        if follow:
            cmd.append("-f")

        try:
            if follow:
                # For follow mode, run interactively
                subprocess.run(cmd)
            else:
                # For non-follow, capture and print
                result = subprocess.run(cmd, capture_output=True, text=True)
                print(result.stdout)
        except KeyboardInterrupt:
            # Graceful exit on Ctrl+C
            pass
        except FileNotFoundError:
            raise RuntimeError("journalctl command not found (is systemd installed?)")

    def get_info(self) -> dict:
        """Get information about detected paths and configuration."""
        return {
            "user": self.user,
            "group": self.group,
            "project_root": str(self.project_root),
            "venv_path": str(self.venv_path),
            "python_path": str(self.python_path),
            "env_file": str(self.env_file),
            "service_file": str(self.service_file),
            "service_name": self.SERVICE_NAME,
            "installed": self.is_installed(),
            "enabled": self.is_enabled() if self.is_installed() else False,
            "active": self.is_active() if self.is_installed() else False,
        }
