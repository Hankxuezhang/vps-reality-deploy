#!/usr/bin/env python3
"""Deterministic allowlisted skill archive; never zip the entire workspace."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "vps-reality-deploy"
SKILL_FILES = [
    "SKILL.md", "agents/openai.yaml", "requirements.txt",
    "references/config.example.json", "references/operations.md", "references/workflow.md",
    "references/compatibility.md", "references/core-assets.json",
    "scripts/vps.py", "scripts/vpslib/__init__.py", "scripts/vpslib/common.py",
    "scripts/vpslib/transport.py", "scripts/vpslib/remote.py", "scripts/vpslib/deploy.py",
    "scripts/vpslib/delivery.py", "scripts/vpslib/verify.py",
]


SOURCE_EXTRAS = [
    "README.md", "LICENSE", ".gitignore", ".gitattributes", "requirements-dev.txt",
    ".github/workflows/test.yml", "tools/package_skill.py",
    "tests/conftest.py", "tests/test_config_delivery.py", "tests/test_deploy_contract.py",
    "tests/test_transport.py", "tests/test_verification_packaging.py", "tests/LIVE_ACCEPTANCE.md", "tests/VALIDATION.md",
]


def package(output=None, source=False):
    output = Path(output or ROOT / "dist" / "vps-reality-deploy.zip")
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [(SKILL / name, "vps-reality-deploy/" + name) for name in SKILL_FILES]
    files.append((ROOT / "LICENSE", "vps-reality-deploy/LICENSE"))
    if source:
        files = [(SKILL / name, "vps-reality-deploy/" + name) for name in SKILL_FILES]
        files.extend((ROOT / name, name) for name in SOURCE_EXTRAS)
    manifest = {}
    for path, _ in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing or symlinked release input: " + path.name)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in sorted(files, key=lambda item: item[1]):
            data = path.read_bytes()
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
            manifest[name] = hashlib.sha256(data).hexdigest()
    output.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    path = package()
    source_path = package(ROOT / "dist" / "vps-reality-deploy-source.zip", source=True)
    print(json.dumps({"archive": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "source_archive": str(source_path), "files": len(SKILL_FILES) + 1}))
