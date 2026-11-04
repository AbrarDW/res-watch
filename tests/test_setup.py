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
    files = sorted(f.relative_to(tmp_path).as_posix() for f in tmp_path.rglob("*") if f.is_file())
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


# ------------------------------------------------------------ progress output
def run_interactive(tmp_path, monkeypatch, capsys, replies, **flags):
    replies = iter(replies)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))
    p = argparse.ArgumentParser()
    setup_wizard.add_arguments(p)
    argv = ["--output-dir", str(tmp_path / "out"), "--config-dir", str(tmp_path / "cfg"), "--home", str(tmp_path / "home")]
    for k, v in flags.items():
        argv += [f"--{k.replace('_', '-')}", str(v)]
    assert setup_wizard.run(p.parse_args(argv)) == 0
    return capsys.readouterr().out


def headers(out):
    return [line for line in out.splitlines() if line.startswith("Question ")]


def test_question_counter_local(tmp_path, monkeypatch, capsys):
    out = run_interactive(tmp_path, monkeypatch, capsys, ["1", "1", "1"])  # local, linux, manual
    assert headers(out) == ["Question 1 of 3", "Question 2 of 3", "Question 3 of 3"]


def test_question_counter_grows_with_answers(tmp_path, monkeypatch, capsys):
    # vps -> systemd -> caddy (adds domain, https, sub-path) -> domain -> https -> base path -> auth via proxy
    out = run_interactive(tmp_path, monkeypatch, capsys, ["2", "1", "2", "mon.example.com", "1", "", "2"])
    assert headers(out) == ["Question 1 of 3", "Question 2 of 4", "Question 3 of 4", "Question 4 of 7",
                            "Question 5 of 7", "Question 6 of 7", "Question 7 of 7"]


def test_question_counter_both(tmp_path, monkeypatch, capsys):
    # both -> tmux -> ssh tunnel -> proxy auth -> linux -> manual
    out = run_interactive(tmp_path, monkeypatch, capsys, ["3", "4", "1", "2", "1", "1"])
    assert headers(out)[-1] == "Question 6 of 6"


def test_flags_are_not_counted(tmp_path, monkeypatch, capsys):
    out = run_interactive(tmp_path, monkeypatch, capsys, ["4", "1", "2"], where="vps")  # tmux, none, proxy auth
    assert headers(out) == ["Question 1 of 3", "Question 2 of 3", "Question 3 of 3"]


def test_numbered_file_lines_and_quiet_yes_mode(tmp_path, capsys):
    run(tmp_path, where="vps", run="systemd", proxy="nginx", domain="x.example.com", https="have", auth="builtin")
    out = capsys.readouterr().out
    assert "Question" not in out                      # --yes: no prompts or counters
    lines = [l.strip() for l in out.splitlines() if l.strip().startswith("[")]
    assert [l.split()[0] for l in lines] == ["[1/3]", "[2/3]", "[3/3]"]
    assert all(" wrote " in l for l in lines)
    assert "\x1b" not in out                          # no escape codes in CI logs
    run(tmp_path, where="vps", run="systemd", proxy="nginx", domain="x.example.com", https="have", auth="builtin")
    assert "[1/3] unchanged" in capsys.readouterr().out


@pytest.mark.parametrize("v6,expect_active", [("1", True), ("0", False)])
def test_nginx_ipv6_listen_only_when_available(tmp_path, monkeypatch, v6, expect_active):
    monkeypatch.setenv("RESWATCH_IPV6", v6)
    run(tmp_path, where="vps", run="systemd", proxy="nginx", domain="x.example.com", https="have", auth="builtin")
    conf = text(tmp_path, "out/deploy/nginx.conf")
    active = [l.strip() for l in conf.splitlines() if l.strip().startswith("listen [::]")]
    assert bool(active) == expect_active
    assert "listen 80;" in conf and "listen 443 ssl;" in conf


def test_toml_roundtrip_with_and_without_tomllib(tmp_path, monkeypatch):
    """Python 3.10 has no tomllib; the fallback reader must return the same values (types included)."""
    import reswatch.config as cfg
    data = {"host": "127.0.0.1", "port": 8000, "interval": 1.5, "trusted_proxy": False, "auth": "on",
            "q": 'he said "hi" \\o/', "hash": "a # not a comment", "empty": ""}
    path = tmp_path / "c.toml"
    cfg.write_toml(path, data, header="# header\n# second line")
    if cfg._toml is not None:
        assert cfg.read_toml(path) == data
    monkeypatch.setattr(cfg, "_toml", None)
    assert cfg.read_toml(path) == data
