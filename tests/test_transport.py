import re
import time
import pytest
from vpslib.common import SkillError
from vpslib.transport import SSH


class Channel:
    def __init__(self, password_required=False, reject=False, split=False):
        self.required, self.reject, self.split = password_required, reject, split
        self.error_queue, self.out_queue, self.sent = [], [], []
        self.closed, self.done = False, False

    def exec_command(self, command):
        self.command = command
        self.ready = re.search(r"READY_[a-f0-9]+", command).group().encode()
        found = re.search(r"SUDO_[a-f0-9]+", command)
        self.prompt = found.group().encode() if found else b""
        self.error_queue = [self.prompt] if self.required else [self.ready[:10], self.ready[10:]] if self.split else [self.ready]

    def get_pty(self, **_):
        pass

    def sendall(self, value):
        self.sent.append(value)
        if self.required and len(self.sent) == 1:
            self.error_queue = [self.prompt] if self.reject else [self.ready]
        else:
            self.out_queue = [b"0\n"]
            self.done = True

    def shutdown_write(self):
        pass

    def recv_ready(self):
        return bool(self.out_queue)

    def recv_stderr_ready(self):
        return bool(self.error_queue)

    def recv(self, _):
        return self.out_queue.pop(0)

    def recv_stderr(self, _):
        return self.error_queue.pop(0)

    def exit_status_ready(self):
        return self.done

    def recv_exit_status(self):
        return 0

    def close(self):
        self.closed = True


def shell(monkeypatch, tmp_path, cfg, channel, root=False):
    s = SSH(cfg["ssh"], tmp_path)
    s.is_root, s.sudo_password = root, "secret-with-special-$-characters"
    transport = type("Transport", (), {"open_session": lambda self, **kw: channel})()
    s.client = type("Client", (), {"get_transport": lambda self: transport})()
    monkeypatch.setattr(time, "sleep", lambda _: None)
    return s


@pytest.mark.parametrize("root,required,split", [(True,False,False),(False,False,True),(False,True,False)])
def test_password_script_separation(monkeypatch, tmp_path, cfg, root, required, split):
    ch = Channel(required, split=split)
    ssh = shell(monkeypatch, tmp_path, cfg, ch, root)
    assert ssh.run("id -u", timeout=1) == "0\n" and ch.closed
    assert ("sudo -k -S" in ch.command) == (not root)
    if required:
        assert ch.sent[0] == (ssh.sudo_password + "\n").encode() and len(ch.sent) == 2
    else:
        assert len(ch.sent) == 1
    assert b"set -euo pipefail" in ch.sent[-1]
    assert "secret-with-special" not in ch.command


def test_rejected_sudo_never_sends_script(monkeypatch, tmp_path, cfg):
    ch = Channel(True, reject=True)
    ssh = shell(monkeypatch, tmp_path, cfg, ch)
    with pytest.raises(SkillError, match="rejected"):
        ssh.run("dangerous-operation", timeout=1)
    assert len(ch.sent) == 1 and ch.closed

