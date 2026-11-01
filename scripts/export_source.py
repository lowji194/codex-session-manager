"""Export only public source; never include session stores or local configuration."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("main.py", "README.md", "requirements.txt", "requirements-build.txt", "codex-session-manager.spec", ".gitignore", "config.example.json", "start-standalone.cmd")
GLOBS = ("web/*.html", "web/*.css", "web/*.js", "scripts/*.py", "scripts/*.ps1", "scripts/*.md", "tests/test_*.py", ".github/workflows/*.yml")

def export(destination: Path) -> dict:
    paths = {ROOT / name for name in FILES}
    for pattern in GLOBS:
        paths.update(ROOT.glob(pattern))
    secrets = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|GOCSPX-[A-Za-z0-9_-]{20,}")
    data = {}
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Invalid source input: {path.name}")
        content = path.read_bytes()
        if secrets.search(content.decode("utf-8")):
            raise ValueError(f"Potential embedded credential in {path.name}")
        data[path.relative_to(ROOT).as_posix()] = content
    data["start.cmd"] = data["start-standalone.cmd"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in data.items():
            archive.writestr("codex-session-manager/" + name, content)
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".zip.sha256").write_text(f"{checksum}  {destination.name}\n", encoding="ascii")
    return {"archive": str(destination), "files": len(data), "sha256": checksum, "user_data_included": False}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    print(json.dumps(export(parser.parse_args().destination), ensure_ascii=False, indent=2))
