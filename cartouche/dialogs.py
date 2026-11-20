"""Fenêtres Windows de choix de fichier ou de dossier.

Un navigateur ne donne jamais le chemin complet d'un fichier : ces fenêtres
sont donc ouvertes par le serveur local, via PowerShell et Windows Forms.
"""

from __future__ import annotations

import base64
import subprocess

CREATE_NO_WINDOW = 0x08000000

_OWNER = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.FormBorderStyle = 'None'
$owner.Opacity = 0
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Show()
$owner.Activate()
"""


def _quote(value: str) -> str:
    return "'" + (value or "").replace("'", "''") + "'"


def _run(script: str, timeout: int = 900) -> list[str]:
    full = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n" + _OWNER + script + "\n$owner.Close()\n"
    encoded = base64.b64encode(full.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in result.stdout.decode("utf-8", "replace").splitlines() if line.strip()]


def pick_files(title: str, filter_spec: str, initial_dir: str = "", multiple: bool = True) -> list[str]:
    """filter_spec au format Windows, par exemple 'Fichiers PKG (*.pkg)|*.pkg'."""
    script = f"""
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = {_quote(title)}
$dialog.Filter = {_quote(filter_spec)}
$dialog.Multiselect = ${'true' if multiple else 'false'}
if ({_quote(initial_dir)} -ne '' -and (Test-Path -LiteralPath {_quote(initial_dir)})) {{ $dialog.InitialDirectory = {_quote(initial_dir)} }}
if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {{ $dialog.FileNames }}
"""
    return _run(script)


def pick_folder(description: str, initial_dir: str = "") -> str:
    script = f"""
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = {_quote(description)}
$dialog.ShowNewFolderButton = $true
if ({_quote(initial_dir)} -ne '' -and (Test-Path -LiteralPath {_quote(initial_dir)})) {{ $dialog.SelectedPath = {_quote(initial_dir)} }}
if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {{ $dialog.SelectedPath }}
"""
    lines = _run(script)
    return lines[0] if lines else ""
