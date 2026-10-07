from __future__ import annotations

import base64
import getpass
import json
import os
import shlex
import sys
import time
import uuid
from pathlib import Path

import paramiko

from .common import SkillError, fingerprint, private_dir, require


class HostPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, cfg, path):
        self.cfg, self.path = cfg, path

    def missing_host_key(self, client, hostname, key):
        expected = self.cfg.get("host_key_sha256")
        if expected and fingerprint(key) != expected:
            raise SkillError("E_HOST_KEY", "SSH host key does not match the configured fingerprint")
        if not expected and not self.cfg["trust_on_first_use"]:
            raise SkillError("E_HOST_KEY", "Unknown SSH host key; configure its fingerprint or explicitly enable trust_on_first_use")
        client.get_host_keys().add(hostname, key.get_name(), key)
        # A user-supplied known_hosts may live in an existing shared directory.
        # Restrict the file, not its unrelated parent directory.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        client.save_host_keys(str(self.path))
        if os.name != "nt":
            self.path.chmod(0o600)


class SSH:
    """Every command independently verifies uid 0 inside sudo -i's login context.

    Password is sent only after sudo's random prompt; scripts are sent only after
    READY. This also works with passwordless sudo without consuming script input.
    """
    def __init__(self, cfg, runtime):
        self.cfg, self.runtime = cfg, Path(runtime)
        self.client = paramiko.SSHClient()
        self.password = os.environ.get(cfg["password_env"])
        self.sudo_password = os.environ.get(cfg["sudo_password_env"], self.password)
        self.is_root = False
        self.key_fingerprint = None

    def __enter__(self):
        path = Path(self.cfg.get("known_hosts") or self.runtime.parent / "known_hosts").expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self.client.load_host_keys(str(path))
        expected = self.cfg.get("host_key_sha256")
        lookup = self.cfg["host"] if self.cfg["port"] == 22 else f"[{self.cfg['host']}]:{self.cfg['port']}"
        existing = self.client.get_host_keys().lookup(lookup)
        if expected and existing:
            require(all(fingerprint(k) == expected for k in existing.values()), "Saved SSH key conflicts with the supplied fingerprint", "E_HOST_KEY")
        self.client.set_missing_host_key_policy(HostPolicy(self.cfg, path))
        if not self.password and not self.cfg.get("private_key") and sys.stdin.isatty():
            self.password = getpass.getpass("SSH password (empty to use SSH agent): ") or None
            self.sudo_password = os.environ.get(self.cfg["sudo_password_env"], self.password)
        try:
            self.client.connect(hostname=self.cfg["host"], port=self.cfg["port"], username=self.cfg["username"],
                                password=self.password, key_filename=self.cfg.get("private_key"),
                                passphrase=os.environ.get(self.cfg["key_passphrase_env"]),
                                timeout=20, auth_timeout=30, banner_timeout=30,
                                allow_agent=True, look_for_keys=True)
            self.key_fingerprint = fingerprint(self.client.get_transport().get_remote_server_key())
            _, stdout, _ = self.client.exec_command("id -u", timeout=20)
            uid = stdout.read().decode().strip()
            require(uid.isdigit(), "Could not identify the SSH user's uid", "E_SSH")
            self.is_root = uid == "0"
            require(self.run("id -u").strip() == "0", "Administrator privilege verification failed", "E_SUDO")
            return self
        except SkillError:
            self.client.close()
            raise
        except paramiko.BadHostKeyException:
            self.client.close()
            raise SkillError("E_HOST_KEY", "SSH host key changed; verify server identity before updating known_hosts") from None
        except paramiko.AuthenticationException:
            self.client.close()
            raise SkillError("E_SSH_AUTH", "SSH authentication failed; check username and password/key") from None
        except Exception:
            self.client.close()
            raise SkillError("E_SSH", "SSH connection failed; check host, port, routing and SSH availability") from None

    def __exit__(self, *_):
        self.client.close()

    def run(self, script, timeout=180):
        marker = uuid.uuid4().hex
        ready, prompt = ("READY_" + marker).encode(), ("SUDO_" + marker).encode()
        inner = f'test "$(id -u)" = 0 || exit 77; printf %s {shlex.quote(ready.decode())} >&2; exec bash -s'
        command = "bash -c " + shlex.quote(inner)
        if not self.is_root:
            command = "sudo -k -S -p " + shlex.quote(prompt.decode()) + " -i -- " + command
        channel = self.client.get_transport().open_session(timeout=20)
        if self.cfg["request_pty"]:
            channel.get_pty(width=200, height=40)
            command = "stty -echo -icanon; " + command
        channel.exec_command(command)
        output, errors, handshake = bytearray(), bytearray(), bytearray()
        sent_password, sent_script = False, False
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                blocks = []
                if channel.recv_ready():
                    blocks.append((True, channel.recv(65536)))
                if channel.recv_stderr_ready():
                    blocks.append((False, channel.recv_stderr(65536)))
                for is_stdout, data in blocks:
                    if not sent_script:
                        handshake.extend(data)
                        if prompt in handshake and not sent_password:
                            if self.sudo_password is None and sys.stdin.isatty():
                                self.sudo_password = getpass.getpass("sudo password: ")
                            require(self.sudo_password is not None, "sudo needs a password; set the configured sudo_password_env", "E_SUDO")
                            channel.sendall((self.sudo_password + "\n").encode())
                            sent_password = True
                            handshake.clear()
                        if ready in handshake:
                            channel.sendall(("set -euo pipefail\numask 077\n" + script + "\nexit $?\n").encode())
                            channel.shutdown_write()
                            sent_script = True
                            handshake.clear()
                        elif sent_password and prompt in handshake:
                            raise SkillError("E_SUDO", "sudo rejected the supplied password")
                    elif is_stdout:
                        output.extend(data)
                    else:
                        errors.extend(data)
                if len(output) + len(errors) > 16 * 1024 * 1024:
                    raise SkillError("E_REMOTE_OUTPUT", "Remote output exceeded the 16 MiB limit")
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    code = channel.recv_exit_status()
                    if not sent_script:
                        raise SkillError("E_SUDO", "Root login shell could not start; verify sudo rights and request_pty if required")
                    if code != 0:
                        # Never include installer/API output: it can contain credentials.
                        raise SkillError("E_REMOTE", f"Remote operation failed (exit {code}); inspect the relevant protected server log")
                    return output.decode("utf-8", errors="replace").replace("\r\n", "\n")
                time.sleep(0.03)
            raise SkillError("E_TIMEOUT", "Remote command timed out; its outcome is unknown. Reconcile state before retrying")
        finally:
            channel.close()

    def python(self, source, timeout=180):
        delimiter = "PY_" + uuid.uuid4().hex
        return self.run("python3 - <<'" + delimiter + "'\n" + source + "\n" + delimiter, timeout)

    def read_json(self, path):
        source = f"import json,pathlib\np=pathlib.Path({path!r})\nprint(p.read_text() if p.exists() else 'null')"
        return json.loads(self.python(source))

    def write(self, path, data):
        blob = base64.b64encode(data.encode() if isinstance(data, str) else data).decode()
        self.python(f"import base64,os,pathlib\np=pathlib.Path({path!r})\np.parent.mkdir(parents=True,exist_ok=True,mode=0o700)\nt=p.with_name(p.name+'.tmp')\nt.write_bytes(base64.b64decode({blob!r}))\nt.chmod(0o600)\nos.replace(t,p)")
