"""Build a clean Windows portable ZIP without importing or starting the app.

Use --dry-run to validate inputs and print the plan without running PyInstaller.
Build instructions: scripts/PORTABLE_BUILD.md.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
APP_NAME = "CodexSessionManager"
WEB_FILES = ("index.html", "styles.css", "app.js")
FORBIDDEN_PARTS = {"tool", "backups", "change-backups", ".git", ".env", "__pycache__"}
FORBIDDEN_FILES = {"auth.json", "auth.enc", "credential-user-scope", "config.local.json"}


def package_files(folder: Path) -> list[Path]:
    """Fail closed on links and known private/runtime data, even in extra runtime input."""
    files: list[Path] = []
    for item in folder.rglob("*"):
        relative = item.relative_to(folder)
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError(f"Không đóng gói liên kết tệp: {relative}")
        if any(part.casefold() in FORBIDDEN_PARTS for part in relative.parts):
            raise ValueError(f"Phát hiện thư mục ngoài phạm vi phát hành: {relative}")
        if item.name.casefold() in FORBIDDEN_FILES:
            raise ValueError(f"Phát hiện dữ liệu xác thực ngoài phạm vi phát hành: {relative}")
        if item.is_file():
            files.append(item)
    return sorted(files, key=lambda item: item.relative_to(folder).as_posix().casefold())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="preview", help="Nhãn phiên bản, ví dụ 1.0.0")
    parser.add_argument("--output-dir", type=Path, default=PROJECT / "dist")
    parser.add_argument(
        "--webview2-runtime", type=Path,
        help="Fixed Version Runtime đã giải nén, chứa msedgewebview2.exe ở thư mục gốc",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.version):
        parser.error("Nhãn phiên bản chỉ chứa chữ, số, dấu chấm, gạch ngang hoặc gạch dưới.")
    return args


def build(args: argparse.Namespace) -> dict[str, object]:
    if not (PROJECT / "codex-session-manager.spec").is_file():
        raise ValueError("Không tìm thấy cấu hình PyInstaller.")
    for name in WEB_FILES:
        if not (PROJECT / "web" / name).is_file():
            raise ValueError(f"Thiếu giao diện web/{name}")
    runtime = args.webview2_runtime.resolve() if args.webview2_runtime else None
    if runtime:
        if not runtime.is_dir() or not (runtime / "msedgewebview2.exe").is_file():
            raise ValueError("Đường dẫn runtime phải chứa trực tiếp msedgewebview2.exe.")
        # Do not accept a project root or accidentally walk its user data as a runtime.
        package_files(runtime)
    output = args.output_dir.resolve()
    architecture = "arm64" if platform.machine().casefold() in {"arm64", "aarch64"} else "x64"
    archive = output / f"{APP_NAME}-{args.version}-windows-{architecture}.zip"
    if archive.exists():
        raise FileExistsError(f"Tệp phát hành đã tồn tại; đổi --version hoặc --output-dir: {archive}")
    plan = {
        "archive": str(archive),
        "architecture": architecture,
        "web_assets": [f"web/{name}" for name in WEB_FILES],
        "bundled_webview2": str(runtime) if runtime else None,
        "user_data_included": False,
        "launch_app": False,
    }
    if args.dry_run:
        return {"dry_run": True, **plan}
    if sys.platform != "win32":
        raise RuntimeError("Hãy build trên Windows; PyInstaller không build chéo hệ điều hành.")
    if sys.maxsize <= 2**32:
        raise RuntimeError("Cần Python 64-bit để tạo bản phát hành Windows 64-bit.")
    if importlib.util.find_spec("PyInstaller") is None:
        raise RuntimeError("Thiếu PyInstaller. Cài requirements-build.txt trong môi trường build riêng.")
    output.mkdir(parents=True, exist_ok=True)
    build_root = PROJECT / ".build" / "portable" / uuid.uuid4().hex
    build_root.mkdir(parents=True, exist_ok=False)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(build_root / "dist"), "--workpath", str(build_root / "work"),
        str(PROJECT / "codex-session-manager.spec"),
    ], check=True, cwd=PROJECT)
    payload = build_root / "dist" / APP_NAME
    if not (payload / f"{APP_NAME}.exe").is_file():
        raise RuntimeError("PyInstaller không tạo được tệp chạy dự kiến.")
    for name in WEB_FILES:
        if not (payload / "_internal" / "web" / name).is_file():
            raise RuntimeError(f"Bản đóng gói thiếu web/{name}")
    if runtime:
        shutil.copytree(runtime, payload / "WebView2Runtime", symlinks=False)
        for license_name in ("EULA-WebView2-FixedVersion.html", "WebView2-runtime-source.json"):
            source = runtime.parent / license_name
            if source.is_file():
                if source.is_symlink():
                    raise ValueError(f"Không đóng gói liên kết giấy phép runtime: {license_name}")
                shutil.copy2(source, payload / license_name)
    (payload / "BAT_DAU.txt").write_text(
        "CODEX & ANTIGRAVITY SESSION MANAGER\n\n"
        "Giải nén toàn bộ ZIP vào thư mục có quyền ghi, sau đó mở CodexSessionManager.exe.\n"
        "Không tách EXE khỏi thư mục _internal. Không cần cài Python.\n"
        "Hỗ trợ Windows 10/11 x64 có .NET Framework phù hợp; cần Codex/Antigravity đã cài để chuyển phiên.\n"
        "Dữ liệu phiên sẽ được tạo khi sử dụng; bản phát hành không chứa phiên có sẵn.\n"
        + ("Đã kèm WebView2 Fixed Version Runtime trong WebView2Runtime.\n" if runtime else
           "Cần Microsoft Edge WebView2 Runtime đã có trên máy. ZIP này không kèm runtime.\n")
        + "Không chia sẻ thư mục dữ liệu phiên của bạn khi gửi ứng dụng cho người khác.\n"
        "Bản chưa ký số có thể được Windows SmartScreen yêu cầu xác minh nguồn tải.\n",
        encoding="utf-8",
    )
    files = package_files(payload)
    manifest = {
        "app": APP_NAME,
        "version": args.version,
        "architecture": architecture,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "webview2_bundled": bool(runtime),
        "files": [
            {"path": item.relative_to(payload).as_posix(), "size": item.stat().st_size, "sha256": sha256(item)}
            for item in files
        ],
    }
    (payload / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = package_files(payload)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for item in files:
            bundle.write(item, f"{APP_NAME}/{item.relative_to(payload).as_posix()}")
    checksum = sha256(archive)
    archive.with_suffix(".zip.sha256").write_text(f"{checksum}  {archive.name}\n", encoding="ascii")
    return {"ok": True, **plan, "sha256": checksum, "file_count": len(files)}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        print(json.dumps(build(args), ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Build không hoàn tất: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
