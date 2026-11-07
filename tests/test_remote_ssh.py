"""A remote computer over SSH: the start script starts the real remote service, the tunnel reaches it. A
stand-in for the ssh program runs the script with sh on this computer and forwards the tunnel port, so no
SSH server is needed."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from hertzon.remote.client import RemoteError
from hertzon.remote.cloud import free_port
from hertzon.remote.ssh import SSHHost, SSHSession, split_target, start_script

FAKE_SSH = r'''
import socket, subprocess, sys, threading, json, os
args = sys.argv[1:]
with open(os.environ["FAKE_SSH_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
if os.environ.get("FAKE_SSH_DENY"):
    sys.stderr.write("user@box: Permission denied (publickey).\n")
    sys.exit(255)
if "-N" in args:                                   # ssh -N -L 127.0.0.1:LP:127.0.0.1:RP ... dest
    lp, rp = (lambda p: (int(p[1]), int(p[3])))(args[args.index("-L") + 1].split(":"))
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", lp)); srv.listen()
    def pipe(a, b):
        try:
            while True:
                d = a.recv(65536)
                if not d:
                    break
                b.sendall(d)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try: s.shutdown(socket.SHUT_RDWR)
                except OSError: pass
    while True:
        c, _ = srv.accept()
        try:
            r = socket.create_connection(("127.0.0.1", rp))
        except OSError:
            c.close(); continue
        threading.Thread(target=pipe, args=(c, r), daemon=True).start()
        threading.Thread(target=pipe, args=(r, c), daemon=True).start()
assert args[-1] == "sh -s", args
sys.exit(subprocess.run(["sh", "-s"], stdin=sys.stdin).returncode)
'''


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    if os.name == "nt" or not shutil.which("sh"):
        pytest.skip("needs a POSIX shell")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "fake_ssh.py").write_text(FAKE_SSH, encoding="utf-8")
    ssh = bindir / "ssh"
    ssh.write_text(f"#!/bin/sh\nexec {sys.executable} {bindir / 'fake_ssh.py'} \"$@\"\n", encoding="utf-8")
    ssh.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERTZON_HOME", str(home / ".hertzon"))
    monkeypatch.setenv("FAKE_SSH_LOG", str(tmp_path / "ssh.log"))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    monkeypatch.setenv("PYTHONPATH", root + os.pathsep + os.environ.get("PYTHONPATH", ""))
    return tmp_path


def test_split_target():
    assert split_target("anna@box") == ("anna@box", "")
    assert split_target("anna@box:2222") == ("anna@box", "2222")
    assert split_target("ssh://anna@box.example.org:22") == ("anna@box.example.org", "22")
    assert split_target("cluster") == ("cluster", "")
    assert split_target("anna@[fe80::1]:2222") == ("anna@fe80::1", "2222")
    assert split_target("fe80::1") == ("fe80::1", "")
    for bad in ("", "anna@", "anna@box:ssh"):
        with pytest.raises(RemoteError):
            split_target(bad)


def test_start_scripts_are_valid_shell():
    for start in ("python", "docker"):
        script = start_script(start, "python3 -m hertzon.remote", "0.2.13", 47999)
        subprocess.run(["sh", "-n"], input=script.encode(), check=True)
        assert "47999" in script
    docker = start_script("docker", "", "0.2.13")
    assert "ghcr.io/c8501024/hertzon-solver:0.2.13" in docker and "hertzon-solver:dev" in docker
    assert "-p 127.0.0.1:47820:47820" in docker            # the service is reachable only through the tunnel


def test_ssh_starts_the_service_and_tunnels(fake_ssh):
    port = free_port()
    session = SSHSession("anna@box:2222", key="~/.ssh/id_hz", command=f"{sys.executable} -m hertzon.remote",
                         workdir=fake_ssh / "local", remote_port=port)
    host = SSHHost(session)
    try:
        host.ensure()
        info = host.info()
        assert info["service"] == "hertzon-remote"
        token = host.token
        assert token and token == (fake_ssh / "home" / ".hertzon" / "remote" / "token").read_text().strip()
        log = (fake_ssh / "ssh.log").read_text().splitlines()
        assert '"-p", "2222"' in log[0] and '"IdentitiesOnly=yes"' in log[0] and "anna@box" in log[0]
        assert "StrictHostKeyChecking=accept-new" in log[0] and "BatchMode=yes" in log[0]
        # a dropped tunnel is opened again; the running service is reused (same token, one start)
        session.close()
        assert host.jobs() == []
        assert host.token == token
        pid = (fake_ssh / "home" / ".hertzon" / "ssh-serve.pid").read_text().strip()
        session.close()
        SSHHost(session).ensure()
        assert (fake_ssh / "home" / ".hertzon" / "ssh-serve.pid").read_text().strip() == pid
    finally:
        session.close()
        pidf = fake_ssh / "home" / ".hertzon" / "ssh-serve.pid"
        if pidf.is_file():
            subprocess.run(["kill", pidf.read_text().strip()], check=False)


def test_ssh_login_refused(fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_DENY", "1")
    host = SSHHost(SSHSession("anna@box", workdir=fake_ssh / "local"))
    with pytest.raises(RemoteError, match="Permission denied.*without a password prompt"):
        host.ensure()


def test_ssh_service_does_not_start(fake_ssh):
    bad = SSHSession("anna@box", command="false", workdir=fake_ssh / "local", remote_port=free_port())
    with pytest.raises(RemoteError, match="SSH to anna@box failed: Hertzon not found"):
        bad.ensure()
