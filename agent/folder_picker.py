"""Native folder dialog opened on the agent's desktop through a separate PowerShell process."""

import asyncio
import base64
import contextlib
import os
import shutil
import subprocess
import sys

from agent.code_git import _kill_tree
from shared.logger import get_logger

logger = get_logger(__name__)

PICK_FOLDER_TIMEOUT: float = 300.0

CODE_TIMEOUT = "FOLDER_PICKER_TIMEOUT"
CODE_UNAVAILABLE = "FOLDER_PICKER_UNAVAILABLE"
CODE_UNSUPPORTED = "FOLDER_PICKER_UNSUPPORTED"

MSG_TIMEOUT = "Окно выбора папки закрыто по таймауту."
MSG_UNAVAILABLE = "Не удалось открыть окно выбора папки. Укажите путь вручную."
MSG_UNSUPPORTED = "Выбор папки доступен только на Windows. Укажите путь вручную."

# Fixed script: nothing from a request is ever interpolated into it.
_PS_SCRIPT = """\
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Выберите папку git-репозитория'
$dialog.ShowNewFolderButton = $false
if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::Out.Write($dialog.SelectedPath)
}
$owner.Dispose()
"""


class FolderPickerError(Exception):
    """The folder dialog could not deliver a result; carries a stable code and a user message."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code: str = code
        self.detail: str = detail


def _build_argv() -> list[str]:
    """Build the PowerShell command line; the script travels as -EncodedCommand."""
    exe = shutil.which("powershell.exe") or os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )
    encoded = base64.b64encode(_PS_SCRIPT.encode("utf-16-le")).decode("ascii")
    return [
        exe,
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded,
    ]


async def _spawn(argv: list[str]) -> asyncio.subprocess.Process:
    """Start the dialog process without a console window."""
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


async def pick_folder(timeout: float | None = None) -> str | None:
    """Show the folder dialog and return the chosen path, or None when it was cancelled."""
    if sys.platform != "win32":
        raise FolderPickerError(CODE_UNSUPPORTED, MSG_UNSUPPORTED)
    limit = PICK_FOLDER_TIMEOUT if timeout is None else timeout
    try:
        proc = await _spawn(_build_argv())
    except OSError as exc:
        logger.error("folder_picker_failed", error=type(exc).__name__)
        raise FolderPickerError(CODE_UNAVAILABLE, MSG_UNAVAILABLE) from exc
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), limit)
    except TimeoutError:
        _kill_tree(proc.pid)
        await proc.wait()
        logger.error("folder_picker_timeout")
        raise FolderPickerError(CODE_TIMEOUT, MSG_TIMEOUT) from None
    except asyncio.CancelledError:
        _kill_tree(proc.pid)
        with contextlib.suppress(Exception):
            await proc.wait()
        raise
    if proc.returncode != 0:
        logger.error("folder_picker_failed", returncode=proc.returncode)
        raise FolderPickerError(CODE_UNAVAILABLE, MSG_UNAVAILABLE)
    path = (out or b"").decode("utf-8", errors="replace").strip()
    if not path:
        logger.info("folder_picker_cancelled")
        return None
    logger.info("folder_picker_selected")
    return path
