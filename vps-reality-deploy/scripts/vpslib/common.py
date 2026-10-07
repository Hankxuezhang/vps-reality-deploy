from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path


class SkillError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 3):
        super().__init__(message)
        self.code, self.message, self.exit_code = code, message, exit_code


def require(condition, message, code="E_CONFIG"):
    if not condition:
        raise SkillError(code, message, 2 if code == "E_CONFIG" else 3)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


_protected_dirs = set()


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)
    elif str(path.resolve()) not in _protected_dirs:
        # NTFS permissions, not chmod's DOS read-only flag, protect local credentials.
        result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        sid = re.search(r"S-1-5-(?:[0-9]+-)*[0-9]+", result.stdout)
        require(sid is not None, "Could not identify the current Windows user for private runtime ACLs", "E_PERMISSIONS")
        result = subprocess.run(["icacls", str(path.resolve()), "/inheritance:r", "/grant:r",
                                 "*" + sid.group() + ":(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        require(result.returncode == 0, "Could not restrict runtime directory permissions; use a user-owned NTFS directory", "E_PERMISSIONS")
        _protected_dirs.add(str(path.resolve()))
    return path


def write_private(path, content):
    """Atomic writes; process umask and private parent also protect temporary files."""
    path = Path(path)
    private_dir(path.parent)
    data = content.encode("utf-8") if isinstance(content, str) else content
    fd, tmp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path, data):
    write_private(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def encoded(data):
    return base64.b64encode(json.dumps(data).encode()).decode("ascii")


def fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def host(value):
    require(isinstance(value, str) and len(value) <= 253, "Invalid host")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        require(bool(re.fullmatch(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value)), "Invalid hostname")
        require(all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", p) for p in value.split(".")), "Invalid DNS labels")
        return value.lower()


def authority(value):
    return f"[{value}]" if ":" in value else value


def port(value, label):
    require(type(value) is int and 1 <= value <= 65535, f"{label} must be an integer from 1 to 65535")
    return value


def reject_unknown(mapping, allowed, label):
    require(isinstance(mapping, dict), f"{label} must be an object")
    unknown = set(mapping) - set(allowed)
    require(not unknown, f"Unknown keys in {label}: {', '.join(sorted(unknown))}")


PROFILES = {
    "tutorial-compatible": {"panel": "v3.8.5", "xray": "v26.7.28", "min_client": "1.0.0", "mlkem": False},
    "modern": {"panel": "v3.8.5", "xray": "v26.9.9", "min_client": "", "mlkem": True},
}
# SHA-256 of the official, tag-pinned installer fetched on 2026-10-06.
INSTALLER_SHA256 = "4e3fe7fe00ef8e904ce6a0e9c36fd8a0c7179fe5e786f23e31801aee84c6347d"


def load_config(path):
    c = read_json(path)
    reject_unknown(c, ["schema_version", "deployment_id", "ssh", "server", "client", "verification"], "root")
    require(c.get("schema_version") == 1, "schema_version must be 1")
    require(bool(re.fullmatch(r"[a-z][a-z0-9-]{1,39}", c.get("deployment_id", ""))), "deployment_id: 2-40 lowercase letters, digits or hyphens, starting with a letter")
    s = c.setdefault("ssh", {})
    reject_unknown(s, ["host", "port", "username", "password_env", "sudo_password_env", "private_key", "key_passphrase_env", "known_hosts", "host_key_sha256", "trust_on_first_use", "request_pty"], "ssh")
    s["host"] = host(s.get("host"))
    s.setdefault("port", 22)
    port(s["port"], "ssh.port")
    require(bool(re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.-]{0,63}", s.get("username", ""))), "Invalid SSH username")
    s.setdefault("password_env", "VPS_SSH_PASSWORD")
    s.setdefault("sudo_password_env", "VPS_SUDO_PASSWORD")
    s.setdefault("key_passphrase_env", "VPS_KEY_PASSPHRASE")
    for k in ["password_env", "sudo_password_env", "key_passphrase_env"]:
        require(bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s[k])), f"{k} must be an environment variable name")
    for k, default in [("trust_on_first_use", True), ("request_pty", False)]:
        s.setdefault(k, default)
        require(type(s[k]) is bool, f"ssh.{k} must be boolean")
    if s.get("host_key_sha256"):
        require(bool(re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", s["host_key_sha256"])), "Invalid SSH host-key fingerprint")
    srv = c.setdefault("server", {})
    reject_unknown(srv, ["public_host", "domain", "panel_port", "proxy_port", "subscription_port", "profile", "reality_targets", "enable_bbr", "firewall"], "server")
    srv["public_host"] = host(srv.get("public_host", s["host"]))
    # The first unattended installer adapter supports public IPv4, optionally with a domain.
    try:
        ip = ipaddress.ip_address(srv["public_host"])
        require(ip.version == 4, "This installer adapter requires a public IPv4 address in server.public_host")
    except ValueError:
        raise SkillError("E_CONFIG", "server.public_host must be the VPS public IPv4; use server.domain for DNS", 2)
    if srv.get("domain"):
        srv["domain"] = host(srv["domain"])
        require(not re.fullmatch(r"[0-9.]+", srv["domain"]), "server.domain must be a DNS name")
    srv.setdefault("proxy_port", 443)
    srv.setdefault("subscription_port", 2096)
    ports = [s["port"], 80, port(srv["proxy_port"], "proxy_port"), port(srv["subscription_port"], "subscription_port")]
    if srv.get("panel_port") is not None:
        ports.append(port(srv["panel_port"], "panel_port"))
    require(len(set(ports)) == len(ports), "SSH, ACME (80), panel, proxy and subscription ports must be distinct")
    srv.setdefault("profile", "tutorial-compatible")
    require(srv["profile"] in PROFILES, "profile must be tutorial-compatible or modern")
    srv.setdefault("reality_targets", ["www.amd.com:443", "www.nvidia.com:443"])
    require(isinstance(srv["reality_targets"], list) and 1 <= len(srv["reality_targets"]) <= 8, "Provide 1-8 REALITY targets")
    for target in srv["reality_targets"]:
        require(isinstance(target, str) and bool(re.fullmatch(r"[A-Za-z0-9.-]+:[0-9]{1,5}", target)), "Target must be DNS-name:port")
        h, p = target.rsplit(":", 1)
        host(h)
        port(int(p), "target port")
    srv.setdefault("enable_bbr", True)
    require(type(srv["enable_bbr"]) is bool, "enable_bbr must be boolean")
    srv.setdefault("firewall", "auto")
    require(srv["firewall"] in ["auto", "external"], "firewall must be auto or external")
    cl = c.setdefault("client", {})
    reject_unknown(cl, ["name", "email", "traffic_gb", "expiry_time"], "client")
    cl.setdefault("name", c["deployment_id"])
    require(isinstance(cl["name"], str) and 1 <= len(cl["name"]) <= 80 and not any(ord(x) < 32 for x in cl["name"]), "Invalid client name")
    cl.setdefault("email", "vps-" + c["deployment_id"])
    require(bool(re.fullmatch(r"[a-z0-9][a-z0-9@._-]{1,100}", cl["email"])), "client.email must be a lowercase unique client identifier")
    cl.setdefault("traffic_gb", 0)
    require(type(cl["traffic_gb"]) in [int, float] and 0 <= cl["traffic_gb"] <= 1000000, "Invalid traffic_gb (GiB; zero means unlimited)")
    cl.setdefault("expiry_time", 0)
    require(type(cl["expiry_time"]) is int and cl["expiry_time"] >= 0, "expiry_time must be Unix milliseconds or zero")
    v = c.setdefault("verification", {})
    reject_unknown(v, ["auto_download", "xray_binary", "mihomo_binary"], "verification")
    v.setdefault("auto_download", True)
    require(type(v["auto_download"]) is bool, "auto_download must be boolean")
    return c


def config_digest(c):
    normalized = {k: c[k] for k in ["deployment_id", "server", "client"]}
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()


def runtime_dir(root, c):
    return private_dir(Path(root).resolve() / c["deployment_id"])


def new_state(c, occupied):
    excluded = set(occupied) | {c["ssh"]["port"], 80, c["server"]["proxy_port"], c["server"]["subscription_port"]}
    panel_port = c["server"].get("panel_port")
    if panel_port is None:
        for _ in range(200):
            candidate = 20000 + secrets.randbelow(30000)
            if candidate not in excluded:
                panel_port = candidate
                break
        require(panel_port is not None, "Could not select a free panel port", "E_PORT")
    return {"schema_version": 1, "deployment_id": c["deployment_id"], "config_digest": config_digest(c), "phase": "planned", "panel_port": panel_port,
            "panel_username": secrets.token_hex(8), "panel_password": secrets.token_urlsafe(24), "web_path": secrets.token_hex(16),
            "profile": c["server"]["profile"], "verification": {"shadowrocket": "not_tested"}}
