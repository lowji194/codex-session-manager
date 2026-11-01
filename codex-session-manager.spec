# Windows portable directory bundle. User session data is never a build input.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

project = Path(SPECPATH)
webview_datas, webview_binaries, webview_hidden = collect_all("webview")
pythonnet_datas, pythonnet_binaries, pythonnet_hidden = collect_all("pythonnet")

analysis = Analysis(
    [str(project / "main.py")],
    pathex=[str(project)],
    binaries=webview_binaries + pythonnet_binaries,
    datas=[
        (str(project / "web" / "index.html"), "web"),
        (str(project / "web" / "styles.css"), "web"),
        (str(project / "web" / "app.js"), "web"),
    ] + webview_datas + pythonnet_datas,
    hiddenimports=webview_hidden + pythonnet_hidden + collect_submodules("clr_loader") + [
        "clr",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="CodexSessionManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    uac_admin=False,
)
bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="CodexSessionManager",
)
