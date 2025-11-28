"""
Smart systemd service manager with fully dynamic path detection.

This module handles installation, configuration, and management of opensensor
as a systemd service. It uses only dynamic discovery methods - no hardcoded paths.

Key principles:
1. Use Python's introspection to find where we're running from
2. Use system tools (which, uv) to discover executables
3. Respect XDG standards and environment variables
4. Provide clear feedback about what was detected
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from opensensor_enviroplus.utils.env import (
    detect_installation_type,
    detect_virtual_env,
    find_env_file,
    get_current_user,
    get_user_group,
    get_user_home,
)


@dataclass
class ExecutableInfo:
    """Information about a discovered executable."""

    path: Path
    exists: bool
    source: str  # How it was discovered (e.g., "shutil.which", "sys.executable", "uv tool")

    def __str__(self) -> str:
        status = "exists" if self.exists else "NOT FOUND"
        return f"{self.path} ({status}, via {self.source})"


@dataclass
class EnvironmentInfo:
    """Dynamically detected environment information."""

    # User info (from system)
    user: str
    group: str
    home: Path

    # Python environment
    python_executable: Path
    virtual_env: Path | None
    is_venv: bool

    # Package info
    package_name: str = "opensensor-enviroplus"
    cli_name: str = "opensensor"

    # Discovered paths
    cli_executable: ExecutableInfo | None = None
    working_directory: Path = field(default_factory=Path.cwd)
    env_file: Path | None = None

    # Installation type detection
    installation_type: str = "unknown"  # "venv", "uv_tool", "uvx_ephemeral", "system", "editable"


class ServiceManager:
    """
    Manages systemd service for opensensor with fully dynamic path detection.

    All paths are discovered at runtime using:
    - Python introspection (sys.executable, sys.prefix, etc.)
    - System tools (shutil.which, subprocess calls to 'uv')
    - Environment variables (VIRTUAL_ENV, XDG_*, etc.)
    - OS-level user/group detection
    """

    SERVICE_NAME = "opensensor"

    def __init__(self) -> None:
        """Initialize with dynamically detected environment."""
        self.env = self._detect_environment()
        self.service_file = Path(f"/etc/systemd/system/{self.SERVICE_NAME}.service")

        # Legacy compatibility attributes
        self.user = self.env.user
        self.group = self.env.group
        self.project_root = self.env.working_directory
        self.venv_path = self.env.virtual_env or self.env.python_executable.parent.parent
        self.python_path = self.env.python_executable
        self.env_file = self.env.env_file or (self.project_root / ".env")

    def _detect_environment(self) -> EnvironmentInfo:
        """Detect the complete runtime environment dynamically."""
        # 1. Detect user (handle sudo) - using shared utilities
        user = get_current_user()
        group = get_user_group(user)
        home = get_user_home(user)

        # 2. Detect Python environment - using shared utilities
        python_exe = Path(sys.executable).resolve()
        virtual_env = detect_virtual_env()
        is_venv = virtual_env is not None

        # 3. Detect installation type - using shared utilities
        installation_type = detect_installation_type()

        # 4. Find CLI executable
        cli_executable = self._find_cli_executable(user, home)

        # 5. Find working directory and env file - using shared utilities
        working_dir, env_file = self._find_working_directory_and_env(user, home)

        return EnvironmentInfo(
            user=user,
            group=group,
            home=home,
            python_executable=python_exe,
            virtual_env=virtual_env,
            is_venv=is_venv,
            cli_executable=cli_executable,
            working_directory=working_dir,
            env_file=env_file,
            installation_type=installation_type,
        )

    def _find_cli_executable(self, _user: str, home: Path) -> ExecutableInfo | None:
        """
        Find the CLI executable using multiple discovery methods.

        Order of precedence:
        1. shutil.which() - finds in PATH
        2. uv tool dir --bin - if uv is available
        3. Common locations based on detected environment
        """
        cli_name = "opensensor"

        # Method 1: Use shutil.which (most reliable - respects PATH)
        which_result = shutil.which(cli_name)
        if which_result:
            path = Path(which_result).resolve()
            return ExecutableInfo(path=path, exists=path.exists(), source="PATH (shutil.which)")

        # Method 2: Try uv tool dir --bin if uv is available
        uv_bin_dir = self._get_uv_tool_bin_dir()
        if uv_bin_dir:
            uv_cli = uv_bin_dir / cli_name
            if uv_cli.exists():
                return ExecutableInfo(path=uv_cli, exists=True, source="uv tool dir --bin")

        # Method 3: Check virtual environment bin (if in venv)
        venv = detect_virtual_env()
        if venv:
            venv_cli = venv / "bin" / cli_name
            if venv_cli.exists():
                return ExecutableInfo(path=venv_cli, exists=True, source="VIRTUAL_ENV/bin")

        # Method 4: Check same directory as Python executable
        python_bin_dir = Path(sys.executable).parent
        sibling_cli = python_bin_dir / cli_name
        if sibling_cli.exists():
            return ExecutableInfo(path=sibling_cli, exists=True, source="sys.executable sibling")

        # Method 5: XDG bin directory (common for user installs)
        xdg_bin = self._get_xdg_bin_dir(home)
        if xdg_bin:
            xdg_cli = xdg_bin / cli_name
            if xdg_cli.exists():
                return ExecutableInfo(path=xdg_cli, exists=True, source="XDG_BIN_HOME")

        # Not found - return None with diagnostic info
        return None

    def _get_uv_tool_bin_dir(self) -> Path | None:
        """Get uv's tool bin directory by running 'uv tool dir --bin'."""
        try:
            result = subprocess.run(
                ["uv", "tool", "dir", "--bin"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return Path(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return None

    def _get_xdg_bin_dir(self, home: Path) -> Path | None:
        """Get the XDG bin directory."""
        # Check environment variables in order of precedence
        if xdg_bin := os.environ.get("UV_TOOL_BIN_DIR"):
            return Path(xdg_bin)
        if xdg_bin := os.environ.get("XDG_BIN_HOME"):
            return Path(xdg_bin)
        if xdg_data := os.environ.get("XDG_DATA_HOME"):
            return Path(xdg_data).parent / "bin"
        # Default XDG location
        return home / ".local" / "bin"

    def _find_working_directory_and_env(self, _user: str, _home: Path) -> tuple[Path, Path | None]:
        """Find the working directory and .env file using shared utilities."""
        cwd = Path.cwd()

        # Use shared find_env_file utility
        env_file = find_env_file()

        if env_file and env_file.exists():
            # Use the directory containing .env as working directory
            return env_file.parent, env_file

        # No .env found - return cwd and expected location
        return cwd, cwd / ".env"

    def _check_sudo(self) -> bool:
        """Check if running with sudo/root privileges."""
        return os.geteuid() == 0

    def _require_sudo(self) -> None:
        """
        Ensure running with sudo privileges.
        If not root, re-execute with sudo.
        """
        if not self._check_sudo():
            python_exe = sys.executable
            cmd = [
                "sudo",
                python_exe,
                "-m",
                "opensensor_enviroplus.cli.app",
                *sys.argv[1:],
            ]
            print(f"This operation requires sudo. Re-executing with: {' '.join(cmd)}")
            try:
                os.execvp("sudo", cmd)
            except OSError as e:
                raise PermissionError(
                    f"Failed to execute with sudo: {e}\nTry manually: sudo {' '.join(cmd)}"
                ) from e

    def _run_systemctl(self, *args: str) -> tuple[int, str, str]:
        """Run systemctl command and return (returncode, stdout, stderr)."""
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

    def _build_path_env(self) -> str:
        """Build PATH environment variable dynamically."""
        path_parts: list[str] = []
        seen: set[str] = set()

        def add_path(p: Path | None) -> None:
            if p and p.exists():
                p_str = str(p)
                if p_str not in seen:
                    path_parts.append(p_str)
                    seen.add(p_str)

        # 1. CLI executable's directory (most important)
        if self.env.cli_executable and self.env.cli_executable.exists:
            add_path(self.env.cli_executable.path.parent)

        # 2. Virtual environment bin
        if self.env.virtual_env:
            add_path(self.env.virtual_env / "bin")

        # 3. XDG bin directory
        add_path(self._get_xdg_bin_dir(self.env.home))

        # 4. uv tool bin directory
        uv_bin = self._get_uv_tool_bin_dir()
        if uv_bin:
            add_path(uv_bin)

        # 5. Python executable's directory
        add_path(self.env.python_executable.parent)

        # 6. Standard system paths
        system_paths = [
            "/usr/local/sbin",
            "/usr/local/bin",
            "/usr/sbin",
            "/usr/bin",
            "/sbin",
            "/bin",
        ]
        for p in system_paths:
            path_parts.append(p)

        return ":".join(path_parts)

    def _generate_service_content(self) -> str:
        """Generate systemd service file content."""
        if not self.env.cli_executable:
            raise RuntimeError("CLI executable not found. Cannot generate service file.")

        cli_path = self.env.cli_executable.path
        path_env = self._build_path_env()
        env_file = self.env.env_file or (self.env.working_directory / ".env")
        working_dir = self.env.working_directory

        return f"""[Unit]
Description=OpenSensor Enviro+ Data Collector
Documentation=https://opensensor.space
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={self.env.user}
Group={self.env.group}
WorkingDirectory={working_dir}
Environment=PATH={path_env}
EnvironmentFile={env_file}
ExecStart={cli_path} start
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
ReadWritePaths={working_dir}/output {working_dir}/logs

[Install]
WantedBy=multi-user.target
"""

    def _validate_for_install(self) -> list[str]:
        """Validate environment before installation. Returns list of errors."""
        errors: list[str] = []

        # Check for ephemeral uvx environment
        if self.env.installation_type == "uvx_ephemeral":
            errors.append(
                "Running from uvx ephemeral environment.\n"
                "The uvx cache is temporary and will be cleaned up.\n"
                "Please install permanently first:\n"
                "  uv tool install opensensor-enviroplus\n"
                "  # or\n"
                "  pip install opensensor-enviroplus"
            )

        # Check CLI executable
        if not self.env.cli_executable:
            errors.append(
                f"Cannot find '{self.env.cli_name}' executable.\n"
                "Make sure the package is properly installed:\n"
                "  uv tool install opensensor-enviroplus\n"
                "  # or\n"
                "  pip install opensensor-enviroplus\n\n"
                "Then ensure it's in your PATH:\n"
                "  which opensensor"
            )
        elif not self.env.cli_executable.exists:
            errors.append(
                f"CLI executable not found at: {self.env.cli_executable.path}\n"
                f"(Discovered via: {self.env.cli_executable.source})"
            )

        # Check .env file
        if not self.env.env_file or not self.env.env_file.exists():
            expected = self.env.env_file or (self.env.working_directory / ".env")
            errors.append(
                f"Configuration file not found: {expected}\n"
                "Run 'opensensor setup' first to create configuration."
            )

        return errors

    def install(self) -> None:
        """Create and install the systemd service file."""
        self._require_sudo()

        # Validate environment
        errors = self._validate_for_install()
        if errors:
            raise RuntimeError("\n\n".join(errors))

        # Create required directories
        output_dir = self.env.working_directory / "output"
        logs_dir = self.env.working_directory / "logs"
        output_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Set ownership
        shutil.chown(str(output_dir), user=self.env.user, group=self.env.group)
        shutil.chown(str(logs_dir), user=self.env.user, group=self.env.group)

        # Generate and write service file
        service_content = self._generate_service_content()
        self.service_file.write_text(service_content)

        # Reload systemd
        returncode, _, stderr = self._run_systemctl("daemon-reload")
        if returncode != 0:
            raise RuntimeError(f"Failed to reload systemd daemon: {stderr}")

    def uninstall(self) -> None:
        """Remove the systemd service file."""
        self._require_sudo()

        if not self.service_file.exists():
            raise FileNotFoundError(f"Service file not found: {self.service_file}")

        self.service_file.unlink()
        self._run_systemctl("daemon-reload")

    def enable(self) -> None:
        """Enable the service to start on boot."""
        self._require_sudo()
        returncode, _, stderr = self._run_systemctl("enable", self.SERVICE_NAME)
        if returncode != 0:
            raise RuntimeError(f"Failed to enable service: {stderr}")

    def disable(self) -> None:
        """Disable the service from starting on boot."""
        self._require_sudo()
        returncode, _, stderr = self._run_systemctl("disable", self.SERVICE_NAME)
        if returncode != 0:
            raise RuntimeError(f"Failed to disable service: {stderr}")

    def start(self) -> None:
        """Start the service."""
        self._require_sudo()
        returncode, _, stderr = self._run_systemctl("start", self.SERVICE_NAME)
        if returncode != 0:
            raise RuntimeError(f"Failed to start service: {stderr}")

    def stop(self) -> None:
        """Stop the service."""
        self._require_sudo()
        returncode, _, stderr = self._run_systemctl("stop", self.SERVICE_NAME)
        if returncode != 0:
            raise RuntimeError(f"Failed to stop service: {stderr}")

    def restart(self) -> None:
        """Restart the service."""
        self._require_sudo()
        returncode, _, stderr = self._run_systemctl("restart", self.SERVICE_NAME)
        if returncode != 0:
            raise RuntimeError(f"Failed to restart service: {stderr}")

    def status(self) -> tuple[str, bool]:
        """Get service status. Returns (status_output, is_active)."""
        _, stdout, stderr = self._run_systemctl("status", self.SERVICE_NAME)
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
        """Show service logs using journalctl."""
        cmd = ["journalctl", "-u", self.SERVICE_NAME, "-n", str(lines)]
        if follow:
            cmd.append("-f")

        try:
            if follow:
                subprocess.run(cmd)
            else:
                result = subprocess.run(cmd, capture_output=True, text=True)
                print(result.stdout)
        except KeyboardInterrupt:
            pass
        except FileNotFoundError as e:
            raise RuntimeError("journalctl command not found (is systemd installed?)") from e

    def get_info(self) -> dict:
        """Get comprehensive information about detected environment."""
        cli_info = self.env.cli_executable
        return {
            # User info
            "user": self.env.user,
            "group": self.env.group,
            "home": str(self.env.home),
            # Python environment
            "python_executable": str(self.env.python_executable),
            "virtual_env": str(self.env.virtual_env) if self.env.virtual_env else None,
            "is_venv": self.env.is_venv,
            "installation_type": self.env.installation_type,
            # CLI executable
            "cli_executable": str(cli_info.path) if cli_info else None,
            "cli_exists": cli_info.exists if cli_info else False,
            "cli_discovery_method": cli_info.source if cli_info else None,
            # Paths
            "working_directory": str(self.env.working_directory),
            "env_file": str(self.env.env_file) if self.env.env_file else None,
            "env_file_exists": self.env.env_file.exists() if self.env.env_file else False,
            # Service status
            "service_name": self.SERVICE_NAME,
            "service_file": str(self.service_file),
            "installed": self.is_installed(),
            "enabled": self.is_enabled() if self.is_installed() else False,
            "active": self.is_active() if self.is_installed() else False,
            # PATH that will be used
            "path_env": self._build_path_env(),
        }
