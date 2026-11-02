"""Run the wizard non-interactively for several answer sets; only matching files may appear."""
import argparse
from pathlib import Path

import pytest

from reswatch import setup_wizard
from reswatch.config import read_toml


def run(tmp_path, **answers):
    p = argparse.ArgumentParser()
    setup_wizard.add_arguments(p)
    argv = ["--yes", "--output-dir", str(tmp_path / "out"), "--config-dir", str(tmp_path / "cfg"),
            "--home", str(tmp_path / "home")]
    for k, v in answers.items():
        argv += [f"--{k.replace('_', '-')}", str(v)]
    args = p.parse_args(argv)
    assert setup_wizard.run(args) == 0
    files = sorted(str(f.relative_to(tmp_path)) for f in tmp_path.rglob("*") if f.is_file())
    return files, read_toml(tmp_path / "cfg" / "config.toml")


def text(tmp_path, rel):
    return (tmp_path / rel).read_text()


def test_local_manual_writes_only_config(tmp_path):
    files, cfg = run(tmp_path, where="local", os="linux", start="manual")
    assert files == ["cfg/config.toml"]
    assert cfg["host"] == "127.0.0.1" and cfg["port"] == 8000 and cfg["auth"] == "auto"


@pytest.mark.parametrize("os_name,expected", [
    ("linux", "home/.config/systemd/user/reswatch.service"),
    ("macos", "home/Library/LaunchAgents/dev.reswatch.plist"),
])
def test_local_autostart(tmp_path, os_name, expected):
    files, _ = run(tmp_path, where="local", os=os_name, start="autostart", port=8123)
    assert files == ["cfg/config.toml", expected]
    body = text(tmp_path, expected)
    assert "8123" in body and "127.0.0.1" in body and "reswatch" in body


def test_local_autostart_windows_prints_task_only(tmp_path, capsys):
    files, _ = run(tmp_path, where="local", os="windows", start="autostart")
    assert files == ["cfg/config.toml"]
    assert "schtasks /Create" in capsys.readouterr().out


def test_local_docker(tmp_path, capsys):
    files, _ = run(tmp_path, where="local", os="linux", start="docker")
    assert files == ["cfg/config.toml", "out/.dockerignore", "out/Dockerfile"]
    out = capsys.readouterr().out
    assert "--pid=host" in out and "-p 127.0.0.1:8000:8000" in out
    assert not (tmp_path / "out/deploy").exists()


def test_vps_systemd_caddy_subpath(tmp_path):
    files, cfg = run(tmp_path, where="vps", run="systemd", proxy="caddy", domain="mon.example.com",
                     https="docs", base_path="/reswatch", auth="builtin")
    assert files == ["cfg/config.toml", "out/deploy/Caddyfile", "out/deploy/reswatch.service"]
    svc = text(tmp_path, "out/deploy/reswatch.service")
    assert "--base-path /reswatch" in svc and "--trusted-proxy" in svc and "--auth on" in svc
    assert "--host 127.0.0.1" in svc
    caddy = text(tmp_path, "out/deploy/Caddyfile")
    assert "mon.example.com {" in caddy and "handle /reswatch*" in caddy
    assert cfg["auth"] == "on" and cfg["trusted_proxy"] is True and cfg["base_path"] == "/reswatch"


def test_vps_compose_traefik(tmp_path):
    files, _ = run(tmp_path, where="vps", run="compose", proxy="traefik", domain="m.example.org",
                   https="have", auth="proxy")
    assert files == ["cfg/config.toml", "out/.dockerignore", "out/Dockerfile", "out/docker-compose.yml"]
    comp = text(tmp_path, "out/docker-compose.yml")
    assert "pid: host" in comp and "Host(`m.example.org`)" in comp
    assert 'RESWATCH_AUTH: "off"' in comp and "127.0.0.1:8000:8000" in comp


@pytest.mark.parametrize("proxy,fname,needle", [
    ("nginx", "nginx.conf", "proxy_set_header Upgrade $http_upgrade"),
    ("apache", "apache.conf", "upgrade=websocket"),
    ("cloudflare", "cloudflared.yml", "service: http://127.0.0.1:8000"),
])
def test_vps_proxies_have_websocket_support(tmp_path, proxy, fname, needle):
    files, _ = run(tmp_path, where="vps", run="systemd", proxy=proxy, domain="x.example.com", https="have",
                   auth="builtin")
    assert f"out/deploy/{fname}" in files
    assert needle in text(tmp_path, f"out/deploy/{fname}")


def test_vps_tmux_ssh_tunnel_needs_no_files(tmp_path, capsys):
    files, cfg = run(tmp_path, where="vps", run="tmux", proxy="none", auth="builtin")
    assert files == ["cfg/config.toml"]
    out = capsys.readouterr().out
    assert "ssh -N -L 8000:127.0.0.1:8000" in out and "tmux new" in out
    assert cfg["trusted_proxy"] is False


def test_both_generates_local_files_and_server_command(tmp_path, capsys):
    files, cfg = run(tmp_path, where="both", run="compose", proxy="caddy", domain="m.example.com", https="have",
                     auth="builtin", os="linux", start="autostart")
    assert files == ["cfg/config.toml", "home/.config/systemd/user/reswatch.service"]
    out = capsys.readouterr().out
    assert "./setup.sh --where vps" in out and "--proxy caddy" in out
    assert cfg["host"] == "127.0.0.1"


def test_rerun_uses_previous_answers(tmp_path):
    run(tmp_path, where="local", os="macos", start="manual")
    files, cfg = run(tmp_path)  # no flags: previous answers are the defaults
    assert cfg["setup_os"] == "macos" and cfg["setup_where"] == "local"


def test_yes_without_required_answer_fails(tmp_path):
    with pytest.raises(SystemExit):
        run(tmp_path, where="vps", run="systemd", proxy="nginx")  # no domain
