from __future__ import annotations

from pathlib import PureWindowsPath
import re
import shutil
import subprocess
from typing import Callable


class HostSpaceUnavailableError(RuntimeError): pass


class WindowsVhdxSpaceMonitor:
    """Read physical Windows-volume free space for an explicitly named WSL VHDX."""

    def __init__(self, vhdx_path: str, *, timeout_seconds: float = 10.0,
                 runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
                 powershell: str | None = None) -> None:
        path = PureWindowsPath(vhdx_path)
        if not re.fullmatch(r"[A-Za-z]:", path.drive) or path.suffix.casefold() != ".vhdx":
            raise ValueError("vhdx_path must be an absolute drive-qualified Windows .vhdx path")
        if timeout_seconds <= 0: raise ValueError("timeout_seconds must be positive")
        executable = powershell or shutil.which("powershell.exe")
        if not executable: raise HostSpaceUnavailableError("powershell.exe is unavailable from WSL")
        self.vhdx_path = str(path); self.volume = path.drive.upper()
        self.timeout_seconds = timeout_seconds; self.runner = runner; self.powershell = executable

    def __call__(self) -> int:
        escaped_path = self.vhdx_path.replace("'", "''")
        escaped_volume = self.volume.replace("'", "''")
        script = (
            f"$p='{escaped_path}';"
            "if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { exit 44 };"
            f"$d=Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='{escaped_volume}'\";"
            "if ($null -eq $d) { exit 45 };"
            "[Console]::Out.Write([string][int64]$d.FreeSpace)"
        )
        try:
            result = self.runner([self.powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, timeout=self.timeout_seconds, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise HostSpaceUnavailableError("Windows host-space query failed") from exc
        if result.returncode != 0:
            raise HostSpaceUnavailableError(f"Windows host-space query exited {result.returncode}")
        try: value = int(result.stdout.strip())
        except ValueError as exc: raise HostSpaceUnavailableError("invalid Windows free-space response") from exc
        if value < 0: raise HostSpaceUnavailableError("negative Windows free-space response")
        return value
