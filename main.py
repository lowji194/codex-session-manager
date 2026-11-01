from __future__ import annotations

import base64
import argparse
import ctypes
import json
import os
import queue
import re
import shutil
import ssl
import sys
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

APP_NAME = "Codex & Antigravity Session Manager"
CODEX_APP_ID = "OpenAI.Codex_2p2nqsd0c76g0!App"
FROZEN = bool(getattr(sys, "frozen", False))
PROJECT_ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
CONFIG_PATH = Path(os.environ.get("CODEX_SESSION_MANAGER_CONFIG", PROJECT_ROOT / "config.local.json"))
try:
    LOCAL_CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.is_file() and "--self-check" not in sys.argv else {}
    if not isinstance(LOCAL_CONFIG, dict):
        LOCAL_CONFIG = {}
except (OSError, ValueError):
    LOCAL_CONFIG = {}
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
AUTH_PATH = CODEX_HOME / "auth.json"
DATA_ROOT = Path(os.environ.get("CODEX_SESSION_MANAGER_DATA_DIR", PROJECT_ROOT / "tool"))
BACKUP_DIR = DATA_ROOT / "backups"
AGY_BACKUP_DIR = BACKUP_DIR / "antigravity"
LEGACY_BACKUP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CodexSessionManager" / "backups"
LEGACY_MIGRATION_MARKER = DATA_ROOT / ".legacy-backups-migrated"
WEB_DIR = RESOURCE_ROOT / "web"
AGY_CREDENTIAL_TARGET = "gemini:antigravity"
AGY_SCOPE_PATH = Path(os.environ.get("APPDATA", Path.home())) / "Antigravity" / "credential-user-scope"
AGY_EXE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / "antigravity" / "Antigravity.exe"
AGY_DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Antigravity"
AGY_MAIN_LOG = AGY_DATA_DIR / "logs" / "main.log"
AGY_LANGUAGE_LOG = AGY_DATA_DIR / "logs" / "language_server.log"
CHATGPT_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
QUOTA_REFRESH_SECONDS = max(60, int(os.environ.get("CODEX_SESSION_MANAGER_QUOTA_TTL", "300")))
HTTP_TIMEOUT_SECONDS = 15
AGY_QUOTA_URL = "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
AGY_CLIENT_METADATA_URL = "https://antigravity.google/oauth/client-metadata.json"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
AGY_OAUTH_CLIENT_ID = os.environ.get(
    "AGY_OAUTH_CLIENT_ID",
    "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com",
)
AGY_OAUTH_CLIENT_SECRET = os.environ.get("AGY_OAUTH_CLIENT_SECRET", str(LOCAL_CONFIG.get("AGY_OAUTH_CLIENT_SECRET", "")))
_agy_client_id_cache = ""


def fetch_antigravity_quota(timeout: float = 4.0) -> tuple[dict[str, Any], str]:
    """Read quota from the active AGY language server without exposing its CSRF token."""
    try:
        main_log = AGY_MAIN_LOG.read_text(encoding="utf-8", errors="replace")
        language_log = AGY_LANGUAGE_LOG.read_text(encoding="utf-8", errors="replace")
        ui_ports = re.findall(r"Local:\s+https://127\.0\.0\.1:(\d+)/", main_log)
        api_ports = re.findall(r"listening on random port at (\d+) for HTTP", language_log)
        if not ui_ports or not api_ports:
            raise RuntimeError("Antigravity chưa khởi tạo dịch vụ quota")

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(
            f"https://127.0.0.1:{ui_ports[-1]}/", timeout=timeout, context=context,
        ) as response:
            html = response.read().decode("utf-8", errors="replace")
        config_match = re.search(r"window\.__APP_CONFIG__\s*=\s*(\{.*?\});", html, re.DOTALL)
        if not config_match:
            raise RuntimeError("Không đọc được cấu hình quota Antigravity")
        csrf_token = str(json.loads(config_match.group(1)).get("csrfToken") or "")
        if not csrf_token:
            raise RuntimeError("Antigravity chưa cấp khóa đọc quota")

        endpoint = (
            f"http://127.0.0.1:{api_ports[-1]}"
            "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
        )
        request = urllib.request.Request(
            endpoint,
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "Connect-Protocol-Version": "1",
                "x-codeium-csrf-token": csrf_token,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            document = json.loads(response.read().decode("utf-8"))

        quotas: dict[str, Any] = {}
        for group in (document.get("response") or {}).get("groups") or []:
            buckets = [item for item in (group.get("buckets") or []) if item.get("remainingFraction") is not None]
            if not buckets:
                continue
            limiting = min(buckets, key=lambda item: float(item.get("remainingFraction", 1)))
            remaining = max(0.0, min(100.0, float(limiting.get("remainingFraction", 0)) * 100))
            group_name = str(group.get("displayName") or "").casefold()
            prefix = "GEM" if "gemini" in group_name else "3P"
            window = "7D" if str(limiting.get("window") or "").casefold() == "weekly" else "5H"
            quotas[f"{prefix} {window}"] = {
                "used": round(100 - remaining, 1),
                "total": 100,
                "remaining": round(remaining, 1),
                "resetAt": limiting.get("resetTime"),
                "unlimited": False,
            }
        if not quotas:
            raise RuntimeError("Antigravity chưa trả dữ liệu quota")
        return {
            "plan": "AGY",
            "limitReached": any(float(item["remaining"]) <= 0 for item in quotas.values()),
            "quotas": quotas,
            "resetCredits": None,
        }, ""
    except Exception as exc:
        return {"plan": "AGY", "limitReached": False, "quotas": {}, "resetCredits": None}, str(exc)


def _agy_usage_from_groups(groups: list[dict[str, Any]]) -> dict[str, Any]:
    quotas: dict[str, Any] = {}
    for group in groups:
        group_name = str(group.get("displayName") or "").casefold()
        prefix = "GEM" if "gemini" in group_name else "3P"
        for bucket in group.get("buckets") or []:
            if bucket.get("remainingFraction") is None:
                continue
            fraction = float(bucket["remainingFraction"])
            if not __import__("math").isfinite(fraction):
                continue
            remaining = round(max(0.0, min(100.0, fraction * 100)), 1)
            raw_window = str(bucket.get("window") or "").casefold()
            window = {"weekly": "7D", "five_hour": "5H", "five_hours": "5H", "5h": "5H", "daily": "1D"}.get(raw_window, raw_window.upper() or "QUOTA")
            label = f"{prefix} {window}"
            # Preserve each reported window instead of choosing one bucket for a group.
            if label not in quotas or remaining < quotas[label]["remaining"]:
                quotas[label] = {"used": round(100 - remaining, 1), "total": 100,
                                 "remaining": remaining, "resetAt": bucket.get("resetTime"), "unlimited": False}
    if not quotas:
        raise RuntimeError("Antigravity chưa trả dữ liệu quota")
    return {"plan": "AGY", "limitReached": any(item["remaining"] <= 0 for item in quotas.values()),
            "quotas": quotas, "resetCredits": None}


def _agy_cloud_quota(access_token: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        AGY_QUOTA_URL,
        data=b"{}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "antigravity/2.28.1",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        document = json.loads(response.read().decode("utf-8"))
    return _agy_usage_from_groups(document.get("groups") or [])


def _agy_oauth_client_id(timeout: float = 5.0) -> str:
    return AGY_OAUTH_CLIENT_ID


def fetch_antigravity_quota_for_credential(
    credential: WindowsCredential,
    timeout: float = 8.0,
) -> tuple[dict[str, Any], str, WindowsCredential | None]:
    """Read one saved AGY account quota; refresh and return its credential when needed."""
    empty = {"plan": "AGY", "limitReached": False, "quotas": {}, "resetCredits": None}
    try:
        document = json.loads(credential.blob.decode("utf-8"))
        token = document.get("token") or {}
        access_token = str(token.get("access_token") or "")
        if not access_token:
            raise RuntimeError("Backup AGY thiếu access token")
        try:
            return _agy_cloud_quota(access_token, timeout), "", None
        except urllib.error.HTTPError as exc:
            if exc.code not in {401, 403}:
                raise

        refresh_token = str(token.get("refresh_token") or "")
        if not refresh_token:
            raise RuntimeError("Phiên AGY đã hết hạn và thiếu refresh token")
        refresh_body = urllib.parse.urlencode({
            "client_id": AGY_OAUTH_CLIENT_ID,
            "client_secret": AGY_OAUTH_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }).encode("ascii")
        refresh_request = urllib.request.Request(
            GOOGLE_TOKEN_URL,
            data=refresh_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(refresh_request, timeout=timeout) as response:
            refreshed = json.loads(response.read().decode("utf-8"))
        token.update(refreshed)
        token["refresh_token"] = str(refreshed.get("refresh_token") or refresh_token)
        expires_in = int(refreshed.get("expires_in") or 3600)
        token["expiry"] = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat().replace("+00:00", "Z")
        updated = WindowsCredential(
            target=credential.target,
            blob=json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            username=credential.username,
            comment=credential.comment,
            target_alias=credential.target_alias,
            credential_type=credential.credential_type,
            persist=credential.persist,
        )
        return _agy_cloud_quota(str(token.get("access_token") or ""), timeout), "", updated
    except Exception as exc:
        return empty, str(exc), None


def migrate_legacy_backups(
    source: Path = LEGACY_BACKUP_DIR,
    destination: Path = BACKUP_DIR,
) -> int:
    """Copy legacy backups into the tool folder without deleting or overwriting."""
    if not source.is_dir() or source.resolve() == destination.resolve():
        return 0

    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for old_path in source.glob("*.csm"):
        target = destination / old_path.name
        if target.exists():
            continue
        shutil.copy2(old_path, target)
        copied += 1
    return copied


def migrate_legacy_backups_once(
    source: Path = LEGACY_BACKUP_DIR,
    destination: Path = BACKUP_DIR,
    marker: Path = LEGACY_MIGRATION_MARKER,
) -> int:
    """Import legacy backups once so a later user deletion stays deleted."""
    if marker.exists():
        return 0
    copied = migrate_legacy_backups(source, destination)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        datetime.now().astimezone().isoformat(timespec="seconds") + "\n",
        encoding="utf-8",
    )
    return copied


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


@dataclass
class WindowsCredential:
    target: str
    blob: bytes
    username: str = ""
    comment: str = ""
    target_alias: str = ""
    credential_type: int = 1
    persist: int = 2


def credential_read(target: str) -> WindowsCredential | None:
    pointer = ctypes.POINTER(CREDENTIALW)()
    advapi32 = ctypes.windll.advapi32
    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
    ]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    if not advapi32.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        error = ctypes.windll.kernel32.GetLastError()
        if error == 1168:  # ERROR_NOT_FOUND
            return None
        raise ctypes.WinError(error)
    try:
        value = pointer.contents
        blob = (
            ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize)
            if value.CredentialBlobSize
            else b""
        )
        return WindowsCredential(
            target=value.TargetName or target,
            blob=blob,
            username=value.UserName or "",
            comment=value.Comment or "",
            target_alias=value.TargetAlias or "",
            credential_type=int(value.Type),
            persist=int(value.Persist),
        )
    finally:
        advapi32.CredFree(pointer)


def credential_write(value: WindowsCredential) -> None:
    advapi32 = ctypes.windll.advapi32
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    blob_buffer = (ctypes.c_ubyte * max(1, len(value.blob)))()
    if value.blob:
        ctypes.memmove(blob_buffer, value.blob, len(value.blob))
    credential = CREDENTIALW(
        0,
        value.credential_type,
        value.target,
        value.comment or None,
        FILETIME(),
        len(value.blob),
        ctypes.cast(blob_buffer, ctypes.POINTER(ctypes.c_ubyte)),
        value.persist,
        0,
        None,
        value.target_alias or None,
        value.username or None,
    )
    if not advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.windll.kernel32.GetLastError())


def credential_delete(target: str) -> None:
    advapi32 = ctypes.windll.advapi32
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL
    if advapi32.CredDeleteW(target, 1, 0):
        return
    error = ctypes.windll.kernel32.GetLastError()
    if error != 1168:
        raise ctypes.WinError(error)


def agy_profile_from_credential(raw: bytes) -> dict[str, str]:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Credential Antigravity không phải JSON hợp lệ") from exc
    email = str(document.get("id_token_email") or "").strip()
    token = document.get("token") or {}
    if not email or not isinstance(token, dict) or not (
        token.get("refresh_token") or token.get("access_token")
    ):
        raise ValueError("Credential Antigravity thiếu email hoặc token đăng nhập")
    return {"email": email, "name": email.split("@", 1)[0]}


def _blob(data: bytes) -> tuple[DATA_BLOB, Any]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def dpapi_protect(data: bytes) -> bytes:
    source, keepalive = _blob(data)
    target = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "CodexSessionManager", None, None, None, 0, ctypes.byref(target)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def dpapi_unprotect(data: bytes) -> bytes:
    source, keepalive = _blob(data)
    target = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (IndexError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def profile_from_auth(raw: bytes) -> dict[str, str]:
    try:
        auth = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("auth.json không phải JSON hợp lệ") from exc

    tokens = auth.get("tokens") or {}
    claims: list[dict[str, Any]] = []
    for key in ("id_token", "access_token"):
        token = tokens.get(key)
        if isinstance(token, str):
            claims.append(decode_jwt_payload(token))

    profile: dict[str, str] = {}
    for claims_item in claims:
        for key in ("name", "email"):
            value = claims_item.get(key)
            if value and key not in profile:
                profile[key] = str(value)
    if not profile.get("email"):
        raise ValueError("Không tìm thấy email trong phiên Codex")
    profile.setdefault("name", profile["email"].split("@", 1)[0])
    return profile


def safe_part(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text.strip())
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:80] or "unknown"


PLAN_SORT_ORDER = {
    "PRO": 0,
    "PLUS": 1,
    "GO": 2,
    "FREE": 3,
}


def stable_account_sort_key(account: dict[str, Any]) -> tuple[int, str, str, str]:
    """Keep account cards stable; live quota and switch state must not affect order."""
    plan = str(account.get("plan") or "").strip().upper()
    email = str(account.get("email") or "").strip().casefold()
    name = str(account.get("name") or "").strip().casefold()
    account_id = str(account.get("id") or "").strip().casefold()
    return PLAN_SORT_ORDER.get(plan, 99), email, name, account_id


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows readers/scanners may briefly hold a handle without delete sharing.
        # Retry the atomic replace; never truncate or remove the existing backup.
        for attempt in range(6):
            try:
                os.replace(temp_name, path)
                break
            except OSError as exc:
                if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 5:
                    if isinstance(exc, PermissionError):
                        raise PermissionError(
                            exc.errno,
                            "Không thể thay thế file. Kiểm tra quyền Modify của thư mục "
                            "và ứng dụng đang giữ file; bản cũ vẫn được giữ nguyên",
                            str(path),
                        ) from exc
                    raise
                time.sleep(0.05 * (2 ** attempt))
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


class QuotaAuthError(RuntimeError):
    pass


def auth_document(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("auth.json không phải JSON hợp lệ") from exc
    if not isinstance(value, dict):
        raise ValueError("auth.json không phải object JSON")
    return value


def auth_cache_key(raw: bytes) -> str:
    auth = auth_document(raw)
    tokens = auth.get("tokens") or {}
    account_id = str(tokens.get("account_id") or "").strip()
    if account_id:
        return account_id.casefold()
    return profile_from_auth(raw)["email"].casefold()


def _clamp_percent(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return None


def _window_label(duration_mins: int | None, fallback: str) -> str:
    if duration_mins == 300:
        return "SESSION"
    if duration_mins == 10080:
        return "7D"
    if duration_mins and duration_mins % 1440 == 0:
        return f"{duration_mins // 1440}D"
    if duration_mins and duration_mins % 60 == 0:
        return f"{duration_mins // 60}H"
    return fallback


def _format_quota_window(window: dict[str, Any] | None, fallback: str) -> tuple[str, str, int | None]:
    if not isinstance(window, dict):
        return "—", "", None
    used = _clamp_percent(window.get("used_percent", window.get("usedPercent")))
    duration_seconds = window.get("limit_window_seconds")
    duration_mins = window.get("windowDurationMins")
    if duration_mins is None and duration_seconds is not None:
        try:
            duration_mins = int(duration_seconds) // 60
        except (TypeError, ValueError):
            duration_mins = None
    try:
        duration_mins = int(duration_mins) if duration_mins is not None else None
    except (TypeError, ValueError):
        duration_mins = None
    reset_at = window.get("reset_at", window.get("resetsAt"))
    try:
        reset_at = int(reset_at) if reset_at is not None else None
    except (TypeError, ValueError):
        reset_at = None
    if used is None:
        return "—", "", reset_at
    label = _window_label(duration_mins, fallback)
    return f"{label} {used}% · còn {100 - used}", label, reset_at


def quota_from_usage(payload: dict[str, Any]) -> dict[str, Any]:
    rate_limit = payload.get("rate_limit") or {}
    primary_window = rate_limit.get("primary_window") or {}
    secondary_window = rate_limit.get("secondary_window") or {}
    primary_text, primary_label, primary_reset = _format_quota_window(
        primary_window, "SESSION"
    )
    secondary_text, secondary_label, secondary_reset = _format_quota_window(
        secondary_window, "7D"
    )
    primary_used = _clamp_percent(primary_window.get("used_percent", primary_window.get("usedPercent")))
    secondary_used = _clamp_percent(secondary_window.get("used_percent", secondary_window.get("usedPercent")))
    reached = bool(rate_limit.get("limit_reached")) or primary_used == 100
    reset_credits = payload.get("rate_limit_reset_credits") or {}
    credits = payload.get("credits") or {}
    resets = []
    for label, timestamp in ((primary_label, primary_reset), (secondary_label, secondary_reset)):
        if label and timestamp:
            resets.append(f"{label} {datetime.fromtimestamp(timestamp).astimezone().strftime('%H:%M · %d/%m')}")
    windows = []
    for label, used, reset_at in (
        (primary_label, primary_used, primary_reset),
        (secondary_label, secondary_used, secondary_reset),
    ):
        if label and used is not None:
            windows.append({
                "label": label,
                "used": used,
                "total": 100,
                "remaining": 100 - used,
                "reset_at": reset_at,
                "unlimited": False,
            })
    return {
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider": "codex",
        "plan": str(payload.get("plan_type") or "unknown").upper(),
        "status": "ĐÃ HẾT QUOTA" if reached else "HOẠT ĐỘNG",
        "percent": primary_used,
        "session": primary_text,
        "weekly": secondary_text,
        "reset": " | ".join(resets),
        "reset_credits": reset_credits.get("available_count"),
        "credits": credits.get("balance"),
        "windows": windows,
        "found": True,
        "source": "codex-session",
    }


def quota_from_app_server(payload: dict[str, Any]) -> dict[str, Any]:
    limits_by_id = payload.get("rateLimitsByLimitId") or {}
    limits = limits_by_id.get("codex") or payload.get("rateLimits") or {}
    usage = {
        "plan_type": limits.get("planType"),
        "rate_limit": {
            "limit_reached": bool(limits.get("rateLimitReachedType")),
            "primary_window": limits.get("primary"),
            "secondary_window": limits.get("secondary"),
        },
        "credits": limits.get("credits") or {},
        "rate_limit_reset_credits": {
            "available_count": (payload.get("rateLimitResetCredits") or {}).get("availableCount")
        },
    }
    return quota_from_usage(usage)


def fetch_quota_http(raw: bytes) -> dict[str, Any]:
    tokens = auth_document(raw).get("tokens") or {}
    access_token = str(tokens.get("access_token") or "").strip()
    if not access_token:
        raise QuotaAuthError("Phiên không có access token ChatGPT")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "codex-session-manager/1.0",
    }
    account_id = str(tokens.get("account_id") or "").strip()
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    request = urllib.request.Request(CHATGPT_USAGE_URL, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise QuotaAuthError("Token truy cập đã hết hạn") from exc
        if exc.code == 429:
            raise RuntimeError("Máy chủ quota đang giới hạn tần suất") from exc
        raise RuntimeError(f"Máy chủ quota trả HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Không kết nối được máy chủ quota Codex") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Phản hồi quota Codex không hợp lệ") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Phản hồi quota Codex không đúng định dạng")
    return quota_from_usage(payload)


def refresh_quota_with_app_server(raw: bytes) -> tuple[dict[str, Any], bytes]:
    executable = shutil.which("codex.cmd") or shutil.which("codex")
    if not executable:
        raise RuntimeError("Không tìm thấy Codex CLI để làm mới token")
    with tempfile.TemporaryDirectory(prefix="csm-quota-") as folder:
        temp_home = Path(folder)
        temp_auth = temp_home / "auth.json"
        temp_auth.write_bytes(raw)
        env = os.environ.copy()
        env["CODEX_HOME"] = str(temp_home)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            [executable, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
            creationflags=flags,
        )
        output: queue.Queue[str] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                output.put(line)

        threading.Thread(target=read_output, daemon=True).start()

        def send(message: dict[str, Any]) -> None:
            if process.stdin is None:
                raise RuntimeError("Codex App Server không có stdin")
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()

        try:
            send({
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "codex-session-manager", "version": "1.0"}},
            })
            initialize = json.loads(output.get(timeout=10))
            if "result" not in initialize:
                raise RuntimeError("Codex App Server không khởi tạo được")
            send({"method": "initialized"})
            send({"id": 2, "method": "account/rateLimits/read", "params": None})
            deadline = time.monotonic() + 20
            response: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                item = json.loads(output.get(timeout=max(0.1, deadline - time.monotonic())))
                if item.get("id") == 2:
                    response = item
                    break
            if not response or "result" not in response:
                message = ((response or {}).get("error") or {}).get("message")
                raise RuntimeError(message or "Codex App Server không trả dữ liệu quota")
            updated_raw = temp_auth.read_bytes()
            return quota_from_app_server(response["result"] or {}), updated_raw
        except queue.Empty as exc:
            raise RuntimeError("Codex App Server hết thời gian phản hồi") from exc
        finally:
            if process.stdin:
                process.stdin.close()
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()


class QuotaService:
    def __init__(self, ttl_seconds: int = QUOTA_REFRESH_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def _cached(self, key: str, force: bool) -> dict[str, Any] | None:
        with self._lock:
            item = self._cache.get(key)
            if item and not force and time.monotonic() - item[0] < self.ttl_seconds:
                return dict(item[1])
        return None

    def fetch(
        self,
        key: str,
        raw: bytes,
        force: bool = False,
        allow_token_refresh: bool = True,
    ) -> tuple[dict[str, Any], bytes | None]:
        cached = self._cached(key, force)
        if cached is not None:
            return cached, None
        updated_raw: bytes | None = None
        try:
            quota = fetch_quota_http(raw)
        except QuotaAuthError as exc:
            # Refreshing an expired backup starts a Codex App Server process and
            # can wait up to 20 seconds. Only the active account is allowed to
            # do that. Inactive backups stay fast and refresh after a switch.
            if allow_token_refresh:
                quota, refreshed = refresh_quota_with_app_server(raw)
                if refreshed != raw:
                    updated_raw = refreshed
            else:
                quota = {
                    "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "status": "UNAVAILABLE",
                    "error": str(exc),
                    "found": False,
                    "source": "codex-session",
                }
        except Exception as exc:
            quota = {
                "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "status": "LỖI ĐỒNG BỘ",
                "error": str(exc),
                "found": False,
                "source": "codex-session",
            }
        with self._lock:
            self._cache[key] = (time.monotonic(), dict(quota))
        return quota, updated_raw


QUOTA_SERVICE = QuotaService()


def stop_codex() -> None:
    script = r"""
$targets = Get-CimInstance Win32_Process | Where-Object {
  ($_.ExecutablePath -like '*\\OpenAI\\Codex\\*') -or
  ($_.ExecutablePath -like '*\\WindowsApps\\OpenAI.Codex_*') -or
  ($_.Name -eq 'ChatGPT.exe' -and $_.CommandLine -like '*Codex*')
}
$roots = @($targets | Where-Object { $_.Name -eq 'ChatGPT.exe' -and $_.CommandLine -notmatch '--type=' })
foreach ($p in $roots) { & taskkill.exe /PID $p.ProcessId /T /F 2>$null | Out-Null }
Start-Sleep -Milliseconds 700
$left = Get-CimInstance Win32_Process | Where-Object {
  ($_.ExecutablePath -like '*\\OpenAI\\Codex\\*') -or
  ($_.ExecutablePath -like '*\\WindowsApps\\OpenAI.Codex_*')
}
foreach ($p in $left) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, timeout=20, creationflags=flags,
    )
    time.sleep(0.6)


def start_codex() -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["explorer.exe", f"shell:AppsFolder\\{CODEX_APP_ID}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
    )


def stop_antigravity() -> None:
    script = r"""
$all = @(Get-CimInstance Win32_Process)
$targets = @($all | Where-Object { $_.Name -eq 'Antigravity.exe' })
$roots = @($targets | Where-Object { $_.CommandLine -notmatch '--type=' })
$ids = [System.Collections.Generic.HashSet[int]]::new()
foreach ($p in $roots) { [void]$ids.Add([int]$p.ProcessId) }
do {
  $added = $false
  foreach ($p in $all) {
    if ($ids.Contains([int]$p.ParentProcessId) -and $ids.Add([int]$p.ProcessId)) { $added = $true }
  }
} while ($added)
foreach ($p in $roots) { & taskkill.exe /PID $p.ProcessId /T /F 2>$null | Out-Null }
Start-Sleep -Milliseconds 700
$left = @(Get-CimInstance Win32_Process | Where-Object { $ids.Contains([int]$_.ProcessId) })
foreach ($p in $left) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 300
$remaining = @(Get-CimInstance Win32_Process | Where-Object { $ids.Contains([int]$_.ProcessId) })
if ($remaining.Count) {
  Write-Error ('Không thể đóng tiến trình Antigravity: ' + (($remaining.ProcessId | Sort-Object) -join ','))
  exit 5
}
"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=20, creationflags=flags,
    )
    if result.returncode:
        raise PermissionError(
            "Không thể đóng Antigravity. Hãy mở Session Manager bằng start.cmd "
            "và chấp nhận quyền quản trị."
        )
    time.sleep(0.6)


def start_antigravity() -> None:
    if not AGY_EXE.is_file():
        raise FileNotFoundError(f"Không tìm thấy Antigravity tại {AGY_EXE}")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [str(AGY_EXE)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


@dataclass
class BackupRecord:
    path: Path
    name: str
    email: str
    created_at: str
    quota: dict[str, Any] = field(default_factory=dict)
    provider: str = "codex"


class SessionStore:
    def __init__(self, auth_path: Path = AUTH_PATH, backup_dir: Path = BACKUP_DIR):
        self.auth_path = auth_path
        self.backup_dir = backup_dir
        if self.backup_dir == BACKUP_DIR:
            migrate_legacy_backups_once(destination=self.backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def current_profile(self) -> dict[str, str] | None:
        if not self.auth_path.exists():
            return None
        return profile_from_auth(self.auth_path.read_bytes())

    def create_backup(self) -> BackupRecord:
        if not self.auth_path.exists():
            raise FileNotFoundError("Codex hiện không có phiên đăng nhập để backup")
        raw = self.auth_path.read_bytes()
        profile = profile_from_auth(raw)
        now = datetime.now().astimezone()
        # Mỗi email chỉ có một backup. Ghi file mới thành công trước, sau đó mới
        # dọn các file kiểu cũ/trùng email để không bao giờ mất bản đang có nếu
        # mã hóa hoặc ghi đĩa thất bại giữa chừng.
        filename = f"{safe_part(profile['name'])}__{safe_part(profile['email'])}.csm"
        target = self.backup_dir / filename
        same_account_paths = [
            record.path
            for record in self.list_backups()
            if record.email.casefold() == profile["email"].casefold()
        ]
        document = {
            "format": 1,
            "name": profile["name"],
            "email": profile["email"],
            "created_at": now.isoformat(timespec="seconds"),
            "quota": {},
            "auth_dpapi": base64.b64encode(dpapi_protect(raw)).decode("ascii"),
        }
        atomic_write(target, json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))
        for old_path in same_account_paths:
            if old_path != target:
                try:
                    old_path.unlink()
                except FileNotFoundError:
                    pass
        return self.read_record(target)

    def read_record(self, path: Path) -> BackupRecord:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return BackupRecord(path, doc["name"], doc["email"], doc["created_at"], doc.get("quota") or {})

    def list_backups(self) -> list[BackupRecord]:
        records: list[BackupRecord] = []
        for path in self.backup_dir.glob("*.csm"):
            try:
                records.append(self.read_record(path))
            except (OSError, KeyError, json.JSONDecodeError):
                continue
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def read_auth(self, record: BackupRecord) -> bytes:
        doc = json.loads(record.path.read_text(encoding="utf-8"))
        encrypted = base64.b64decode(doc["auth_dpapi"], validate=True)
        raw = dpapi_unprotect(encrypted)
        profile = profile_from_auth(raw)
        if profile["email"].casefold() != record.email.casefold():
            raise ValueError("Email trong token không khớp metadata backup")
        return raw

    def update_auth(self, record: BackupRecord, raw: bytes, quota: dict[str, Any] | None = None) -> None:
        profile = profile_from_auth(raw)
        if profile["email"].casefold() != record.email.casefold():
            raise ValueError("Email trong token làm mới không khớp metadata backup")
        doc = json.loads(record.path.read_text(encoding="utf-8"))
        doc["auth_dpapi"] = base64.b64encode(dpapi_protect(raw)).decode("ascii")
        if quota is not None:
            doc["quota"] = quota
        atomic_write(record.path, json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"))

    def restore(self, record: BackupRecord) -> None:
        raw = self.read_auth(record)
        if self.auth_path.exists():
            self.create_backup()
        atomic_write(self.auth_path, raw)

    def sign_out_safely(self) -> BackupRecord:
        record = self.create_backup()
        self.auth_path.unlink()
        return record

    def delete(self, record: BackupRecord) -> None:
        record.path.unlink()

    def find_by_email(self, email: str) -> BackupRecord | None:
        target = email.strip().casefold()
        return next((record for record in self.list_backups() if record.email.casefold() == target), None)


class AntigravitySessionStore:
    def __init__(
        self,
        backup_dir: Path = AGY_BACKUP_DIR,
        credential_target: str = AGY_CREDENTIAL_TARGET,
        scope_path: Path = AGY_SCOPE_PATH,
    ):
        self.backup_dir = backup_dir
        self.credential_target = credential_target
        self.scope_path = scope_path
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def current_credential(self) -> WindowsCredential | None:
        return credential_read(self.credential_target)

    def current_profile(self) -> dict[str, str] | None:
        credential = self.current_credential()
        return agy_profile_from_credential(credential.blob) if credential else None

    @staticmethod
    def _credential_document(credential: WindowsCredential, scope: bytes) -> bytes:
        payload = {
            "credential": {
                "target": credential.target,
                "type": credential.credential_type,
                "persist": credential.persist,
                "username": credential.username,
                "comment": credential.comment,
                "target_alias": credential.target_alias,
                "blob": base64.b64encode(credential.blob).decode("ascii"),
            },
            "scope": base64.b64encode(scope).decode("ascii"),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def create_backup(self) -> BackupRecord:
        credential = self.current_credential()
        if not credential:
            raise FileNotFoundError("Antigravity hiện không có phiên đăng nhập để backup")
        if not self.scope_path.is_file():
            raise FileNotFoundError("Không tìm thấy credential-user-scope của Antigravity")
        profile = agy_profile_from_credential(credential.blob)
        scope = self.scope_path.read_bytes()
        if not scope:
            raise ValueError("credential-user-scope của Antigravity đang rỗng")
        now = datetime.now().astimezone()
        filename = f"{safe_part(profile['name'])}__{safe_part(profile['email'])}.agy"
        target = self.backup_dir / filename
        same_account_paths = [
            record.path
            for record in self.list_backups()
            if record.email.casefold() == profile["email"].casefold()
        ]
        payload = self._credential_document(credential, scope)
        document = {
            "format": 2,
            "provider": "antigravity",
            "name": profile["name"],
            "email": profile["email"],
            "created_at": now.isoformat(timespec="seconds"),
            "payload_dpapi": base64.b64encode(dpapi_protect(payload)).decode("ascii"),
        }
        atomic_write(target, json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))
        for old_path in same_account_paths:
            if old_path != target:
                try:
                    old_path.unlink()
                except FileNotFoundError:
                    pass
        return self.read_record(target)

    def read_record(self, path: Path) -> BackupRecord:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("format") != 2 or document.get("provider") != "antigravity":
            raise ValueError("Backup không phải phiên Antigravity")
        return BackupRecord(
            path,
            document["name"],
            document["email"],
            document["created_at"],
            provider="antigravity",
        )

    def list_backups(self) -> list[BackupRecord]:
        records: list[BackupRecord] = []
        for path in self.backup_dir.glob("*.agy"):
            try:
                records.append(self.read_record(path))
            except (OSError, KeyError, ValueError, json.JSONDecodeError):
                continue
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def read_session(self, record: BackupRecord) -> tuple[WindowsCredential, bytes]:
        document = json.loads(record.path.read_text(encoding="utf-8"))
        encrypted = base64.b64decode(document["payload_dpapi"], validate=True)
        payload = json.loads(dpapi_unprotect(encrypted).decode("utf-8"))
        item = payload["credential"]
        if item.get("target") != self.credential_target:
            raise ValueError("Credential target trong backup Antigravity không hợp lệ")
        credential = WindowsCredential(
            target=item["target"],
            blob=base64.b64decode(item["blob"], validate=True),
            username=item.get("username") or "",
            comment=item.get("comment") or "",
            target_alias=item.get("target_alias") or "",
            credential_type=int(item.get("type", 1)),
            persist=int(item.get("persist", 2)),
        )
        scope = base64.b64decode(payload["scope"], validate=True)
        profile = agy_profile_from_credential(credential.blob)
        if profile["email"].casefold() != record.email.casefold():
            raise ValueError("Email trong credential AGY không khớp metadata backup")
        if not scope:
            raise ValueError("Backup AGY thiếu credential-user-scope")
        return credential, scope

    def update_record_credential(
        self,
        record: BackupRecord,
        credential: WindowsCredential,
        scope: bytes,
    ) -> None:
        profile = agy_profile_from_credential(credential.blob)
        if profile["email"].casefold() != record.email.casefold():
            raise ValueError("Không thể cập nhật token cho backup AGY khác tài khoản")
        payload = self._credential_document(credential, scope)
        document = {
            "format": 2,
            "provider": "antigravity",
            "name": record.name,
            "email": record.email,
            "created_at": record.created_at,
            "payload_dpapi": base64.b64encode(dpapi_protect(payload)).decode("ascii"),
        }
        atomic_write(record.path, json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))

    def restore(self, record: BackupRecord) -> None:
        target_credential, target_scope = self.read_session(record)
        current_credential = self.current_credential()
        current_scope = self.scope_path.read_bytes() if self.scope_path.is_file() else None
        if current_credential:
            self.create_backup()
        try:
            atomic_write(self.scope_path, target_scope)
            credential_write(target_credential)
        except Exception:
            if current_scope is not None:
                atomic_write(self.scope_path, current_scope)
            if current_credential:
                credential_write(current_credential)
            raise

    def sign_out_safely(self) -> BackupRecord:
        record = self.create_backup()
        credential_delete(self.credential_target)
        return record

    def delete(self, record: BackupRecord) -> None:
        record.path.unlink()

    def find_by_email(self, email: str) -> BackupRecord | None:
        target = email.strip().casefold()
        return next((record for record in self.list_backups() if record.email.casefold() == target), None)


def switch_to_email(email: str, store: SessionStore | None = None) -> dict[str, Any]:
    store = store or SessionStore()
    target = email.strip()
    if not target:
        raise ValueError("Thiếu email phiên cần chuyển")

    current = store.current_profile()
    if current and current["email"].casefold() == target.casefold():
        return {"ok": True, "changed": False, "email": current["email"], "message": "Phiên này đang được sử dụng"}

    record = store.find_by_email(target)
    if not record:
        raise FileNotFoundError(f"Chưa có backup cho {target}")

    stop_codex()
    try:
        # SessionStore.restore luôn backup phiên hiện tại trước khi ghi auth mới.
        store.restore(record)
    finally:
        start_codex()
    return {"ok": True, "changed": True, "email": record.email, "message": f"Đã chuyển sang {record.email}"}


def switch_agy_to_email(email: str, store: AntigravitySessionStore | None = None) -> dict[str, Any]:
    store = store or AntigravitySessionStore()
    target = email.strip()
    if not target:
        raise ValueError("Thiếu email phiên AGY cần chuyển")
    current = store.current_profile()
    if current and current["email"].casefold() == target.casefold():
        return {"ok": True, "changed": False, "email": current["email"], "message": "Phiên AGY này đang được sử dụng"}
    record = store.find_by_email(target)
    if not record:
        raise FileNotFoundError(f"Chưa có backup AGY cho {target}")
    stop_antigravity()
    try:
        store.restore(record)
    finally:
        start_antigravity()
    return {"ok": True, "changed": True, "email": record.email, "message": f"Đã chuyển AGY sang {record.email}"}


def rainmeter_quota_payload(max_accounts: int = 8) -> dict[str, Any]:
    state = WebApi().build_state(force_quota=True)
    accounts = []
    for priority, item in enumerate((state.get("backups") or [])[:max_accounts], 1):
        quotas: dict[str, Any] = {}
        for window in item.get("quotaWindows") or []:
            reset_at = window.get("reset_at")
            reset_iso = None
            if reset_at:
                reset_iso = datetime.fromtimestamp(int(reset_at)).astimezone().isoformat(timespec="seconds")
            quotas[str(window.get("label") or "QUOTA")] = {
                "used": window.get("used"),
                "total": window.get("total", 100),
                "remaining": window.get("remaining"),
                "resetAt": reset_iso,
                "unlimited": bool(window.get("unlimited")),
            }
        status = str(item.get("status") or "")
        error = "" if item.get("quotaSource") == "codex-session" else status
        accounts.append({
            "connection": {
                "id": item.get("id"),
                "provider": "codex",
                "name": item.get("email"),
                "email": item.get("email"),
                "priority": priority,
                "isActive": True,
                "testStatus": "active" if not error else "unavailable",
            },
            "usage": {
                "plan": str(item.get("plan") or "").lower(),
                "limitReached": "HẾT" in status.upper(),
                "quotas": quotas,
                "resetCredits": item.get("resetCredits"),
                "credits": item.get("credits"),
            },
            "error": error,
        })

    agy_store = AntigravitySessionStore()
    try:
        agy_profile = agy_store.current_profile()
        agy_error = ""
    except Exception as exc:
        agy_profile = None
        agy_error = str(exc)
    agy_current_email = str((agy_profile or {}).get("email") or "")
    remaining_slots = max(0, max_accounts - len(accounts))
    agy_records = agy_store.list_backups()[:remaining_slots]
    agy_results: dict[Path, tuple[dict[str, Any], str]] = {}

    def load_agy_quota(record: BackupRecord) -> tuple[Path, dict[str, Any], str]:
        try:
            credential, scope = agy_store.read_session(record)
            if agy_current_email and record.email.casefold() == agy_current_email.casefold():
                try:
                    live_cred = credential_read(AGY_CREDENTIAL_TARGET)
                    if live_cred:
                        live_usage, live_error, updated_live = fetch_antigravity_quota_for_credential(live_cred)
                        if not live_error and live_usage.get("quotas"):
                            if updated_live:
                                credential_write(updated_live)
                                agy_store.update_record_credential(record, updated_live, scope)
                            return record.path, live_usage, ""
                except Exception:
                    pass
            usage, error, updated_credential = fetch_antigravity_quota_for_credential(credential)
            if updated_credential:
                agy_store.update_record_credential(record, updated_credential, scope)
                if agy_current_email and record.email.casefold() == agy_current_email.casefold():
                    try:
                        credential_write(updated_credential)
                    except Exception:
                        pass
            return record.path, usage, error
        except Exception as exc:
            return record.path, {"plan": "AGY", "limitReached": False, "quotas": {}, "resetCredits": None}, str(exc)

    if agy_records:
        with ThreadPoolExecutor(max_workers=min(4, len(agy_records))) as executor:
            for path, usage, error in executor.map(load_agy_quota, agy_records):
                agy_results[path] = (usage, error)

    for priority, record in enumerate(agy_records, len(accounts) + 1):
        usage, quota_error = agy_results.get(record.path, (
            {"plan": "AGY", "limitReached": False, "quotas": {}, "resetCredits": None},
            "Chưa đọc được quota AGY",
        ))
        accounts.append({
            "connection": {
                "id": record.path.name,
                "provider": "antigravity",
                "name": record.email,
                "email": record.email,
                "priority": priority,
                "isActive": True,
                "testStatus": "active",
            },
            "usage": usage,
            "error": quota_error,
        })
    return {
        "fetchedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "local-session",
        "currentEmail": (state.get("current") or {}).get("email", ""),
        "currentEmails": {
            "codex": (state.get("current") or {}).get("email", ""),
            "antigravity": agy_current_email,
        },
        "total": len(accounts),
        "accounts": accounts,
        "error": state.get("quotaError") or agy_error or "",
    }


class WebApi:
    def __init__(self):
        self.codex_store = SessionStore()
        self.agy_store = AntigravitySessionStore()
        self.provider = "codex"
        self.store: SessionStore | AntigravitySessionStore = self.codex_store
        self.records: list[BackupRecord] = []
        self._agy_quota_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._agy_quota_lock = threading.Lock()

    @staticmethod
    def _format_date(value: str) -> str:
        if not value:
            return "—"
        try:
            return datetime.fromisoformat(value).astimezone().strftime("%H:%M · %d/%m/%Y")
        except ValueError:
            return value

    def _read_agy_quota(self, credential: WindowsCredential, force: bool = False) -> tuple[dict[str, Any], WindowsCredential | None]:
        import hashlib
        key = hashlib.sha256(credential.blob).hexdigest()
        with self._agy_quota_lock:
            cached = self._agy_quota_cache.get(key)
            if cached and not force and time.monotonic() - cached[0] < QUOTA_REFRESH_SECONDS:
                return dict(cached[1]), None
        usage, error, refreshed = fetch_antigravity_quota_for_credential(credential)
        windows = [{"label": label, "used": value["used"], "total": value.get("total", 100),
                    "remaining": value["remaining"], "resetAt": value.get("resetAt")}
                   for label, value in (usage.get("quotas") or {}).items()]
        result = {"quotaWindows": windows, "status": "ĐÃ HẾT QUOTA" if usage.get("limitReached") else "HOẠT ĐỘNG",
                  "error": "Không đọc được quota Antigravity. Kiểm tra đăng nhập hoặc cấu hình OAuth." if error else "",
                  "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        if error or not windows:
            result["status"] = "UNAVAILABLE"
            result["error"] = result["error"] or "Antigravity chưa trả dữ liệu quota."
        with self._agy_quota_lock:
            self._agy_quota_cache[key] = (time.monotonic(), dict(result))
            if refreshed:
                self._agy_quota_cache[hashlib.sha256(refreshed.blob).hexdigest()] = (time.monotonic(), dict(result))
        return result, refreshed

    def build_agy_state(self, force_quota: bool = False) -> dict[str, Any]:
        try:
            current_credential = self.agy_store.current_credential()
            profile = agy_profile_from_credential(current_credential.blob) if current_credential else None
            profile_error = ""
        except Exception:
            current_credential, profile = None, None
            profile_error = "Không đọc được phiên Antigravity trên máy."
        self.records = self.agy_store.list_backups()
        current_email = str((profile or {}).get("email") or "").casefold()
        current_quota: dict[str, Any] = {"quotaWindows": [], "status": "UNAVAILABLE", "error": "", "fetched_at": ""}
        if current_credential:
            current_quota, updated = self._read_agy_quota(current_credential, force_quota)
            if updated:
                try:
                    latest = self.agy_store.current_credential()
                    if latest and latest.blob == current_credential.blob:
                        credential_write(updated)
                        current_credential = updated
                except Exception:
                    current_quota["error"] = "Quota đã đọc nhưng chưa lưu được phiên làm mới."
        def load_record(record: BackupRecord) -> dict[str, Any]:
            active = bool(current_email and record.email.casefold() == current_email)
            quota = current_quota if active and current_credential else None
            try:
                saved, scope = self.agy_store.read_session(record)
                if quota is None:
                    quota, refreshed = self._read_agy_quota(saved, force_quota)
                    if refreshed:
                        self.agy_store.update_record_credential(record, refreshed, scope)
                elif current_credential.blob != saved.blob:
                    self.agy_store.update_record_credential(record, current_credential, scope)
            except Exception:
                if quota is None:
                    quota = {"quotaWindows": [], "status": "UNAVAILABLE", "error": "Không đọc được quota của phiên đã lưu.", "fetched_at": ""}
            return {"id": record.path.name, "name": record.name, "email": record.email, "plan": "AGY",
                    "status": quota["status"], "isCurrent": active, "session": "—", "weekly": "—",
                    "quotaWindows": quota.get("quotaWindows", []), "resetCredits": None, "credits": None,
                    "quotaUpdated": self._format_date(quota.get("fetched_at", "")), "backupAt": self._format_date(record.created_at),
                    "quotaSource": "antigravity-session", "quotaError": quota.get("error", "")}
        if self.records:
            with ThreadPoolExecutor(max_workers=min(4, len(self.records))) as executor:
                records = list(executor.map(load_record, self.records))
        else:
            records = []
        errors = sum(bool(item.get("quotaError")) for item in records)
        error = f"{errors} phiên chưa đọc được quota" if errors else current_quota.get("error", "")
        return {"provider": "antigravity", "appName": "Antigravity", "supportsQuota": True,
                "current": profile, "currentQuota": current_quota, "currentError": profile_error, "backups": records,
                "backupFolder": str(AGY_BACKUP_DIR), "quotaSource": "Antigravity session quota",
                "quotaFetchedAt": self._format_date(current_quota.get("fetched_at", "")), "quotaError": error}

    def build_state(self, force_quota: bool = False) -> dict[str, Any]:
        if self.provider == "antigravity":
            return self.build_agy_state(force_quota)
        try:
            profile = self.codex_store.current_profile()
            profile_error = ""
        except Exception as exc:
            profile = None
            profile_error = str(exc)
        self.records = self.codex_store.list_backups()
        sources: dict[str, dict[str, Any]] = {}
        record_keys: dict[Path, str] = {}
        record_errors: dict[Path, str] = {}
        current_key = ""

        if profile and AUTH_PATH.exists():
            try:
                raw = AUTH_PATH.read_bytes()
                current_key = auth_cache_key(raw)
                sources[current_key] = {"raw": raw, "current": True, "records": []}
            except Exception as exc:
                profile_error = str(exc)

        for record in self.records:
            try:
                raw = self.codex_store.read_auth(record)
                key = auth_cache_key(raw)
                record_keys[record.path] = key
                source = sources.setdefault(key, {"raw": raw, "current": False, "records": []})
                source["records"].append(record)
            except Exception as exc:
                record_errors[record.path] = str(exc)

        quotas: dict[str, dict[str, Any]] = {}
        if sources:
            workers = min(4, len(sources))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="quota") as pool:
                futures = {
                    pool.submit(
                        QUOTA_SERVICE.fetch,
                        key,
                        source["raw"],
                        force_quota,
                        bool(source["current"]),
                    ): (key, source)
                    for key, source in sources.items()
                }
                for future in as_completed(futures):
                    key, source = futures[future]
                    try:
                        quota, updated_raw = future.result()
                    except Exception as exc:
                        quota, updated_raw = {
                            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                            "status": "LỖI ĐỒNG BỘ",
                            "error": str(exc),
                            "found": False,
                            "source": "codex-session",
                        }, None
                    quotas[key] = quota
                    if updated_raw:
                        if source["current"]:
                            atomic_write(AUTH_PATH, updated_raw)
                        for record in source["records"]:
                            self.codex_store.update_auth(record, updated_raw, quota)

        current_quota = quotas.get(current_key, {}) if current_key else {}
        records = []
        for record in self.records:
            snapshot = record.quota
            key = record_keys.get(record.path, "")
            live_quota = quotas.get(key, {})
            if record.path in record_errors:
                live_quota = {
                    "status": "LỖI PHIÊN",
                    "error": record_errors[record.path],
                    "found": False,
                    "source": "codex-session",
                }
            quota = {**snapshot, **live_quota} if live_quota else snapshot
            records.append({
                "id": record.path.name,
                "name": record.name,
                "email": record.email,
                "plan": quota.get("plan") or "—",
                "status": quota.get("status") or "Chưa có dữ liệu",
                "session": quota.get("session") or "—",
                "weekly": quota.get("weekly") or "—",
                "quotaWindows": quota.get("windows") or [],
                "resetCredits": quota.get("reset_credits"),
                "credits": quota.get("credits"),
                "quotaUpdated": self._format_date(quota.get("fetched_at", "")),
                "backupAt": self._format_date(record.created_at),
                "quotaSource": "codex-session" if live_quota.get("found") else "error",
            })
        records.sort(key=stable_account_sort_key)
        quota_errors = [quota.get("error") for quota in quotas.values() if quota.get("error")]
        quota_errors.extend(record_errors.values())
        fetched_values = [quota.get("fetched_at", "") for quota in quotas.values() if quota.get("fetched_at")]
        quota_fetched_at = max(fetched_values, default="")
        quota_error = f"{len(quota_errors)} phiên không đồng bộ được quota" if quota_errors else ""
        return {
            "provider": "codex",
            "appName": "Codex",
            "supportsQuota": True,
            "current": profile,
            "currentQuota": current_quota,
            "currentError": profile_error,
            "backups": records,
            "backupFolder": str(BACKUP_DIR),
            "quotaSource": "Codex session token",
            "quotaFetchedAt": self._format_date(quota_fetched_at),
            "quotaError": quota_error,
        }

    def find_record(self, record_id: str) -> BackupRecord | None:
        return next((record for record in self.store.list_backups() if record.path.name == record_id), None)

    def with_app_restart(self, operation: Callable[[], Any]) -> Any:
        stop_app = stop_antigravity if self.provider == "antigravity" else stop_codex
        start_app = start_antigravity if self.provider == "antigravity" else start_codex
        stop_app()
        try:
            return operation()
        finally:
            start_app()

    @staticmethod
    def _result(message: str, state: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "message": message, "state": state}

    def get_state(self) -> dict[str, Any]:
        return self.build_state()

    def select_provider(self, provider: str) -> dict[str, Any]:
        value = str(provider or "").strip().casefold()
        if value not in {"codex", "antigravity"}:
            raise ValueError("Provider không hợp lệ")
        self.provider = value
        self.store = self.agy_store if value == "antigravity" else self.codex_store
        return self._result(f"Đã chuyển sang quản lý {('Antigravity' if value == 'antigravity' else 'Codex')}", self.build_state())

    def reload(self) -> dict[str, Any]:
        return self.build_state(force_quota=True)

    def backup(self) -> dict[str, Any]:
        if not self.store.current_profile():
            raise FileNotFoundError(f"{('Antigravity' if self.provider == 'antigravity' else 'Codex')} hiện không có phiên đăng nhập để backup.")
        record = self.with_app_restart(self.store.create_backup)
        return self._result(f"Đã backup {record.email}", self.build_state())

    def restore(self, record_id: str) -> dict[str, Any]:
        record = self.find_record(record_id)
        if not record:
            raise FileNotFoundError("Bản backup không còn tồn tại.")
        self.with_app_restart(lambda: self.store.restore(record))
        return self._result(f"Đã khôi phục {record.email}", self.build_state())

    def logout(self) -> dict[str, Any]:
        record = self.with_app_restart(self.store.sign_out_safely)
        return self._result(f"Đã đăng xuất và giữ backup {record.email}", self.build_state())

    def delete_backup(self, record_id: str) -> dict[str, Any]:
        record = self.find_record(record_id)
        if not record:
            raise FileNotFoundError("Bản backup không còn tồn tại.")
        self.with_app_restart(lambda: self.store.delete(record))
        return self._result("Đã xóa backup được chọn", self.build_state())

    def open_backup_folder(self) -> bool:
        self.store.backup_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer.exe", str(self.store.backup_dir)])
        return True


def main() -> int:
    import webview

    runtime_override = os.environ.get("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", "")
    runtime_path = Path(runtime_override) if runtime_override else PROJECT_ROOT / "WebView2Runtime"
    if (runtime_path / "msedgewebview2.exe").is_file():
        webview.settings["WEBVIEW2_RUNTIME_PATH"] = str(runtime_path.resolve())

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    api = WebApi()
    webview.create_window(
        APP_NAME,
        str(WEB_DIR / "index.html"),
        js_api=api,
        width=1320,
        height=820,
        min_size=(1060, 680),
        background_color="#E2EAF5",
        text_select=False,
    )
    webview.start(gui="edgechromium", debug=False)
    return 0


def cli(argv: list[str] | None = None) -> int:
    if hasattr(os.sys.stdout, "reconfigure"):
        os.sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--self-check", action="store_true", help="Kiểm tra bộ portable, không đọc phiên hoặc kết nối mạng")
    parser.add_argument("--report", metavar="FILE", help="Ghi báo cáo self-check dạng JSON")
    parser.add_argument("--switch-email", metavar="EMAIL", help="Backup phiên hiện tại rồi chuyển sang backup của EMAIL")
    parser.add_argument("--current-email", action="store_true", help="In email của phiên Codex hiện tại")
    parser.add_argument("--provider", choices=("codex", "antigravity"), default="codex", help="Ứng dụng cần quản lý")
    parser.add_argument("--rainmeter-quota-json", action="store_true", help="Xuất quota các phiên cho skin Rainmeter")
    parser.add_argument("--max-accounts", type=int, default=8, help="Số phiên tối đa khi xuất quota Rainmeter")
    args = parser.parse_args(argv)

    try:
        if args.self_check:
            import webview
            import webview.platforms.winforms
            import webview.platforms.edgechromium
            assets = {name: (WEB_DIR / name).is_file() for name in ("index.html", "styles.css", "app.js")}
            report = {"ok": all(assets.values()), "frozen": FROZEN, "assets": assets,
                      "bundled_webview2": (PROJECT_ROOT / "WebView2Runtime" / "msedgewebview2.exe").is_file(),
                      "backend": "edgechromium", "data_directory": str(DATA_ROOT)}
            output = json.dumps(report, ensure_ascii=False, indent=2)
            if args.report:
                Path(args.report).write_text(output, encoding="utf-8")
            elif sys.stdout is not None:
                print(output)
            return 0 if report["ok"] else 1
        if args.current_email:
            store = AntigravitySessionStore() if args.provider == "antigravity" else SessionStore()
            profile = store.current_profile()
            email = profile["email"] if profile else ""
            if args.report:
                atomic_write(Path(args.report), json.dumps({"email": email}, ensure_ascii=False).encode("utf-8"))
            elif sys.stdout is not None:
                print(email)
            return 0
        if args.rainmeter_quota_json:
            maximum = max(1, min(20, args.max_accounts))
            payload = rainmeter_quota_payload(maximum)
            output = json.dumps(payload, ensure_ascii=False)
            if args.report:
                atomic_write(Path(args.report), output.encode("utf-8"))
            elif sys.stdout is not None:
                print(output)
            return 0
        if args.switch_email:
            result = (
                switch_agy_to_email(args.switch_email)
                if args.provider == "antigravity"
                else switch_to_email(args.switch_email)
            )
            output = json.dumps(result, ensure_ascii=False)
            if args.report:
                atomic_write(Path(args.report), output.encode("utf-8"))
            elif sys.stdout is not None:
                print(output)
            return 0
    except Exception as exc:
        error = json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False)
        if args.report:
            Path(args.report).write_text(error, encoding="utf-8")
        elif sys.stdout is not None:
            print(error)
        return 1
    if sys.stdout is not None:
        parser.print_help()
    return 2


if __name__ == "__main__":
    if len(os.sys.argv) > 1:
        raise SystemExit(cli())
    raise SystemExit(main())
