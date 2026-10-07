from __future__ import annotations

import gzip
import hashlib
import io
import ipaddress
import json
import os
import platform
import socket
import subprocess
import time
import zipfile
from pathlib import Path
from urllib.parse import quote

import requests
import yaml

from .common import PROFILES, SkillError, private_dir, read_json, require, write_json, write_private
from .delivery import validate_clash, xray_client_config

REGISTRY = Path(__file__).resolve().parents[2] / "references" / "core-assets.json"
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
IP_CHECK_URL = "https://api.ipify.org?format=json"


def http_session():
    session = requests.Session()
    session.trust_env = False
    return session


def fetch(url, limit):
    try:
        with http_session() as session, session.get(url, timeout=(20, 60), stream=True, allow_redirects=True) as response:
            response.raise_for_status()
            require(response.url.startswith("https://"), "HTTPS redirect downgraded", "E_TLS")
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                require(len(data) <= limit, "Download exceeds size limit", "E_DOWNLOAD")
            return bytes(data)
    except requests.RequestException:
        # URL paths include subscription credentials, so never stringify this exception.
        raise SkillError("E_HTTPS", "HTTPS download failed; check certificate trust, ports and network access") from None


def platform_key():
    os_key = {"Windows": "windows", "Linux": "linux", "Darwin": "darwin"}.get(platform.system())
    arch = {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower())
    require(os_key and arch, "Local OS/architecture is unsupported for automatic core download", "E_LOCAL_PLATFORM")
    return os_key + "-" + arch


def unpack_binary(blob, asset_name, core):
    if asset_name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            candidates = [i for i in archive.infolist() if not i.is_dir()
                          and Path(i.filename).name.lower() in ["xray", "xray.exe"]]
            if core == "mihomo":
                candidates = [i for i in archive.infolist() if not i.is_dir()
                              and Path(i.filename).name.lower().startswith("mihomo") and i.filename.endswith(".exe")]
            require(len(candidates) == 1 and candidates[0].file_size < 200 * 1024**2,
                    "Unexpected core archive layout", "E_DOWNLOAD")
            # Read a single member to a fixed destination; never extract paths from the archive.
            return archive.read(candidates[0])
    require(asset_name.endswith(".gz"), "Unsupported core archive", "E_DOWNLOAD")
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as stream:
        raw = stream.read(200 * 1024**2 + 1)
    require(len(raw) <= 200 * 1024**2, "Unpacked binary is too large", "E_DOWNLOAD")
    return raw


def core_binary(core, c, directory):
    supplied = c["verification"].get(core + "_binary")
    if supplied:
        p = Path(supplied).expanduser().resolve()
        require(p.is_file(), "Configured local core binary does not exist", "E_LOCAL_CORE")
        return p
    require(c["verification"]["auto_download"], "Core auto-download disabled and no binary path supplied", "E_LOCAL_CORE")
    v = PROFILES[c["server"]["profile"]]["xray"] if core == "xray" else "v1.19.30"
    key = core + "/" + v + "/" + platform_key()
    entry = read_json(REGISTRY).get(key)
    require(entry is not None, "No pinned core artifact for this platform; supply an audited local binary", "E_LOCAL_PLATFORM")
    cache = private_dir(directory / "tools" / (core + "-" + v))
    archive_path = cache / entry["name"]
    blob = archive_path.read_bytes() if archive_path.exists() else fetch(entry["url"], 150 * 1024**2)
    require(hashlib.sha256(blob).hexdigest() == entry["sha256"], "Core archive SHA-256 mismatch", "E_CHECKSUM")
    if not archive_path.exists():
        write_private(archive_path, blob)
    raw = unpack_binary(blob, entry["name"], core)
    output = cache / (core + "-" + v + (".exe" if os.name == "nt" else ""))
    # Rebuild from the checked archive even if a previous executable exists.
    write_private(output, raw)
    if os.name != "nt":
        output.chmod(0o700)
    return output


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_core(core, binary, config, directory, expected_ip):
    port = config["inbounds"][0]["port"] if core == "xray" else config["mixed-port"]
    config_path = directory / (core + "-test.json")
    write_json(config_path, config)  # JSON is also valid YAML for Mihomo.
    log_path = directory / (core + "-test.log")
    args = [str(binary), "run", "-config", str(config_path)] if core == "xray" else [str(binary), "-d", str(directory), "-f", str(config_path)]
    check_args = [str(binary), "run", "-test", "-config", str(config_path)] if core == "xray" else [str(binary), "-t", "-d", str(directory), "-f", str(config_path)]
    write_private(log_path, "")
    with log_path.open("ab") as log:
        try:
            syntax = subprocess.run(check_args, stdout=log, stderr=log, timeout=45, creationflags=CREATE_FLAGS)
            require(syntax.returncode == 0, f"{core} rejected the test config; inspect the private log", "E_CORE_CONFIG")
            process = subprocess.Popen(args, stdout=log, stderr=log, creationflags=CREATE_FLAGS)
        except (OSError, subprocess.TimeoutExpired):
            raise SkillError("E_LOCAL_CORE", f"{core} could not start or validate its config") from None
        try:
            deadline = time.monotonic() + 30
            ready = False
            while time.monotonic() < deadline:
                require(process.poll() is None, f"{core} exited before its SOCKS listener was ready", "E_CORE_START")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        ready = True
                        break
                except OSError:
                    time.sleep(0.2)
            require(ready, f"{core} SOCKS listener timed out", "E_CORE_START")
            with http_session() as session:
                session.proxies = {"http": f"socks5h://127.0.0.1:{port}", "https": f"socks5h://127.0.0.1:{port}"}
                try:
                    response = session.get(IP_CHECK_URL, timeout=(20, 45))
                    response.raise_for_status()
                    actual = str(ipaddress.ip_address(response.json()["ip"]))
                except (requests.RequestException, ValueError, KeyError):
                    raise SkillError("E_PROXY_TEST", f"{core} could not complete the HTTPS request through the node") from None
            require(actual == expected_ip, f"{core} egress IP differs from the VPS reference", "E_PROXY_EGRESS")
            return {"status": "passed", "https_via_proxy": "passed", "egress_ip": actual}
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def verify(ssh, c, state, directory, progress):
    checks = state.setdefault("verification", {})
    checks.update({"overall": "pending", "shadowrocket": "not_tested", "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    try:
        raw_ip = ssh.run("curl -4 --noproxy '*' --fail --silent --show-error --connect-timeout 15 --max-time 30 'https://api.ipify.org?format=json'", timeout=40)
        expected_ip = str(ipaddress.ip_address(json.loads(raw_ip)["ip"]))
        checks["vps_egress_reference"] = expected_ip
    except (SkillError, ValueError, KeyError):
        checks["vps_egress_reference"] = "unavailable"
        expected_ip = None
    matching = None
    try:
        url = state["clash_base_url"] + quote(state["sub_id"], safe="")
        text = fetch(url, 2 * 1024**2).decode("utf-8-sig")
        _, matching = validate_clash(text, state["node"])
        write_private(directory / "native-clash.yaml", text)
        checks["clash_subscription"] = {"status": "passed", "https_certificate": "verified", "node_fields": "matched"}
    except (SkillError, UnicodeError) as exc:
        checks["clash_subscription"] = {"status": "failed", "code": getattr(exc, "code", "E_SUBSCRIPTION")}
    for core in ["xray", "mihomo"]:
        progress(f"Verifying real HTTPS traffic through {core}.")
        if expected_ip is None or (core == "mihomo" and matching is None):
            checks[core] = {"status": "not_tested", "reason": "prerequisite_failed"}
            continue
        try:
            binary = core_binary(core, c, directory)
            local_port = free_port()
            if core == "xray":
                cfg = xray_client_config(state["node"], local_port)
            else:
                # Use the actual subscribed node with no direct route or fallback.
                cfg = {"mixed-port": local_port, "bind-address": "127.0.0.1", "allow-lan": False,
                       "mode": "rule", "log-level": "warning", "ipv6": False,
                       "proxies": [matching], "proxy-groups": [{"name": "VERIFY", "type": "select", "proxies": [matching["name"]]}],
                       "rules": ["MATCH,VERIFY"]}
            checks[core] = run_core(core, binary, cfg, directory, expected_ip)
        except SkillError as exc:
            checks[core] = {"status": "failed", "code": exc.code}
    checks["overall"] = "passed" if all(checks.get(k, {}).get("status") == "passed" for k in ["clash_subscription", "xray", "mihomo"]) else "incomplete"
    write_json(directory / "state.json", state)
    return checks
