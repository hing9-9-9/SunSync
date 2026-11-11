"""install-units.sh puts the systemd units of deploy/ on the server and enables new timers.

The test uses temporary folders and a stand-in for systemctl."""

import os
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


class Server:
    def __init__(self, base: Path) -> None:
        self.src = base / "src"
        self.dest = base / "etc-systemd"
        self.calls_file = base / "calls.log"
        bin_dir = base / "bin"
        self.src.mkdir()
        self.dest.mkdir()
        bin_dir.mkdir()
        systemctl = bin_dir / "systemctl"
        systemctl.write_text('#!/usr/bin/env bash\necho "systemctl $*" >> "$CALLS"\n')
        systemctl.chmod(0o755)
        self.bin_dir = bin_dir

    def unit(self, name: str, text: str = "[Unit]\n") -> None:
        (self.src / name).write_text(text)

    def run(self, src: Path | None = None) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
            "CALLS": str(self.calls_file),
            "SUNSYNC_UNIT_SRC": str(src or self.src),
            "SUNSYNC_UNIT_DIR": str(self.dest),
        }
        return subprocess.run(
            ["bash", str(DEPLOY / "install-units.sh")],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def calls(self) -> list[str]:
        return self.calls_file.read_text().splitlines() if self.calls_file.exists() else []

    def installed(self) -> list[str]:
        return sorted(p.name for p in self.dest.iterdir())


@pytest.fixture
def server(tmp_path: Path) -> Server:
    return Server(tmp_path)


def test_a_fresh_server_gets_every_unit_and_its_timers_are_started(server):
    server.unit("sunsync-api.service")
    server.unit("sunsync-worker.service")
    server.unit("sunsync-worker.timer")
    server.unit("sunsync-kma-worker.timer")
    result = server.run()
    assert result.returncode == 0, result.stderr
    assert server.installed() == [
        "sunsync-api.service",
        "sunsync-kma-worker.timer",
        "sunsync-worker.service",
        "sunsync-worker.timer",
    ]
    assert server.calls() == [
        "systemctl daemon-reload",
        "systemctl enable --now sunsync-kma-worker.timer",
        "systemctl enable --now sunsync-worker.timer",
    ]


def test_nothing_is_done_when_the_server_is_up_to_date(server):
    server.unit("sunsync-worker.timer")
    server.run()
    server.calls_file.unlink()
    result = server.run()
    assert result.returncode == 0
    assert server.calls() == []
    assert result.stdout == ""


def test_a_changed_unit_is_replaced_and_reloaded_but_not_enabled_again(server):
    server.unit("sunsync-api.service", "[Service]\nUMask=0022\n")
    server.run()
    server.calls_file.unlink()
    server.unit("sunsync-api.service", "[Service]\nUMask=0077\n")
    result = server.run()
    assert "Updated sunsync-api.service" in result.stdout
    assert (server.dest / "sunsync-api.service").read_text() == "[Service]\nUMask=0077\n"
    assert server.calls() == ["systemctl daemon-reload"]


def test_a_timer_that_is_added_later_is_installed_and_started(server):
    server.unit("sunsync-worker.timer")
    server.run()
    server.calls_file.unlink()
    server.unit("sunsync-ai-nowcast-worker.timer")
    result = server.run()
    assert "Installed sunsync-ai-nowcast-worker.timer" in result.stdout
    assert server.calls() == [
        "systemctl daemon-reload",
        "systemctl enable --now sunsync-ai-nowcast-worker.timer",
    ]


def test_the_automatic_update_is_never_turned_on_here(server):
    server.unit("sunsync-autoupdate.service")
    server.unit("sunsync-autoupdate.timer")
    server.run()
    assert "sunsync-autoupdate.timer" in server.installed()
    assert server.calls() == ["systemctl daemon-reload"]


def test_the_real_units_of_the_repository_all_install(server):
    result = server.run(src=DEPLOY)
    assert result.returncode == 0, result.stderr
    expected = sorted(p.name for p in DEPLOY.iterdir() if p.suffix in (".service", ".timer"))
    assert server.installed() == expected
    enabled = [c for c in server.calls() if c.startswith("systemctl enable")]
    assert "systemctl enable --now sunsync-kma-worker.timer" in enabled
    assert "systemctl enable --now sunsync-ai-nowcast-worker.timer" in enabled
    assert not any("autoupdate" in c for c in enabled)


def test_the_update_installs_the_units_before_it_restarts_the_timers():
    script = (DEPLOY / "update.sh").read_text()
    assert script.index("install-units.sh") < script.index("restart sunsync-worker.timer")
    assert "VITE_BUILD_ID" in script
