import io
import json
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from vpslib.common import SkillError
from vpslib import verify as verification
from vpslib.remote import rollback
from vpslib.transport import HostPolicy
from package_skill import package, SKILL_FILES


def test_download_cache_is_versioned(tmp_path, cfg, monkeypatch):
    registry = {
        "xray/v26.7.28/windows-amd64": {"name": "Xray-windows-64.zip", "url": "https://example.test/old", "sha256": ""},
        "xray/v26.9.9/windows-amd64": {"name": "Xray-windows-64.zip", "url": "https://example.test/new", "sha256": ""},
    }
    blobs = {}
    import hashlib
    for key, entry in registry.items():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("xray.exe", key.encode())
        blobs[entry["url"]] = buffer.getvalue()
        entry["sha256"] = hashlib.sha256(buffer.getvalue()).hexdigest()
    monkeypatch.setattr(verification, "read_json", lambda _: registry)
    monkeypatch.setattr(verification, "platform_key", lambda: "windows-amd64")
    fetch = Mock(side_effect=lambda url, _: blobs[url])
    monkeypatch.setattr(verification, "fetch", fetch)
    first = verification.core_binary("xray", cfg, tmp_path)
    cfg["server"]["profile"] = "modern"
    second = verification.core_binary("xray", cfg, tmp_path)
    assert first != second and first.read_bytes() != second.read_bytes()
    assert fetch.call_count == 2


def test_checksum_failure_never_extracts(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(verification, "read_json", lambda _: {"xray/v26.7.28/windows-amd64": {"name": "bad.zip", "url": "https://example.test/a", "sha256": "0"*64}})
    monkeypatch.setattr(verification, "platform_key", lambda: "windows-amd64")
    monkeypatch.setattr(verification, "fetch", lambda *a: b"tampered")
    extract = Mock()
    monkeypatch.setattr(verification, "unpack_binary", extract)
    with pytest.raises(SkillError, match="SHA-256"):
        verification.core_binary("xray", cfg, tmp_path)
    extract.assert_not_called()


def test_archive_member_path_cannot_escape(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../xray.exe", b"synthetic")
    assert verification.unpack_binary(buf.getvalue(), "xray.zip", "xray") == b"synthetic"
    assert not list(tmp_path.iterdir())


def test_unknown_ssh_key_is_not_silently_accepted(tmp_path):
    client = Mock()
    key = Mock()
    key.asbytes.return_value = b"test-key"
    with pytest.raises(SkillError, match="Unknown"):
        HostPolicy({"trust_on_first_use": False}, tmp_path / "known_hosts").missing_host_key(client, "example.test", key)
    client.save_host_keys.assert_not_called()


def test_failed_prerequisites_cannot_report_success(tmp_path, cfg, node, monkeypatch):
    ssh = Mock()
    ssh.run.side_effect = SkillError("E_REMOTE", "Synthetic failure")
    monkeypatch.setattr(verification, "fetch", Mock(side_effect=SkillError("E_HTTPS", "Synthetic failure")))
    run = Mock()
    monkeypatch.setattr(verification, "run_core", run)
    state = {"node": node, "sub_id": "synthetic-token", "clash_base_url": "https://example.test/clash/"}
    result = verification.verify(ssh, cfg, state, tmp_path, lambda *_: None)
    assert result["overall"] == "incomplete" and result["shadowrocket"] == "not_tested"
    assert result["xray"]["status"] == "not_tested"
    run.assert_not_called()


@pytest.mark.parametrize("path", ["/etc", "/var/lib/vps-reality-deploy/backups/../state", "/var/lib/vps-reality-deploy/backups/a/b"])
def test_rollback_refuses_outside_managed_snapshot(path):
    ssh = Mock()
    with pytest.raises(SkillError):
        rollback(ssh, path)
    ssh.run.assert_not_called()


def test_package_allowlist_reproducible(tmp_path):
    first, second = package(tmp_path / "a.zip"), package(tmp_path / "b.zip")
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as z:
        names = set(z.namelist())
        assert names == {"vps-reality-deploy/" + p for p in SKILL_FILES} | {"vps-reality-deploy/LICENSE"}
        assert not any("/runtime/" in p or "/analysis/" in p or p.endswith(".png") for p in names)

