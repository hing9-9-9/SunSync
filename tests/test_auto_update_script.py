"""auto-update.sh deploys main only when the switch is on and main has a commit that is
neither deployed nor failed. The test uses a throw-away git remote and stubbed commands."""

import os
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_IDENTITY},
    )
    return result.stdout.strip()


class Server:
    """A fake server: a clone of a remote, a state folder and stubs for systemctl and update.sh."""

    def __init__(self, base: Path) -> None:
        self.remote = base / "remote.git"
        self.work = base / "work"
        self.root = base / "root"
        self.state = base / "state"
        self.switch = base / "autoupdate"
        self.calls_file = base / "calls.log"
        bin_dir = base / "bin"
        self.update_stub = base / "update-stub.sh"
        self.state.mkdir()
        bin_dir.mkdir()

        git(base, "init", "--bare", "-b", "main", str(self.remote))
        git(base, "clone", str(self.remote), str(self.work))
        git(self.work, "checkout", "-b", "main")
        self.push_commit()
        git(base, "clone", str(self.remote), str(self.root))

        systemctl = bin_dir / "systemctl"
        systemctl.write_text('#!/usr/bin/env bash\necho "systemctl $*" >> "$CALLS"\n')
        systemctl.chmod(0o755)
        self.update_stub.write_text(
            "#!/usr/bin/env bash\n"
            'echo "update" >> "$CALLS"\n'
            '[ "${FAKE_UPDATE_FAIL:-}" = 1 ] && exit 1\n'
            'git -C "$SUNSYNC_ROOT" rev-parse origin/main > "$SUNSYNC_STATE_DIR/deployed-commit"\n'
        )
        self.update_stub.chmod(0o755)
        self.bin_dir = bin_dir

    def push_commit(self) -> None:
        (self.work / "file.txt").write_text(os.urandom(8).hex())
        git(self.work, "add", "-A")
        git(self.work, "commit", "-m", "change")
        git(self.work, "push", "origin", "main")

    def run(self, fail_update: bool = False) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
            "CALLS": str(self.calls_file),
            "SUNSYNC_ROOT": str(self.root),
            "SUNSYNC_STATE_DIR": str(self.state),
            "SUNSYNC_AUTOUPDATE_SWITCH": str(self.switch),
            "SUNSYNC_UPDATE_SCRIPT": str(self.update_stub),
            "FAKE_UPDATE_FAIL": "1" if fail_update else "",
        }
        return subprocess.run(
            ["bash", str(DEPLOY / "auto-update.sh")],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def calls(self) -> list[str]:
        return self.calls_file.read_text().splitlines() if self.calls_file.exists() else []

    def remote_head(self) -> str:
        return git(self.work, "rev-parse", "HEAD")


@pytest.fixture
def server(tmp_path: Path) -> Server:
    return Server(tmp_path)


def test_nothing_happens_while_the_switch_is_off(server):
    server.push_commit()
    result = server.run()
    assert result.returncode == 0
    assert server.calls() == []


def test_a_new_commit_is_backed_up_and_deployed(server):
    server.switch.touch()
    result = server.run()
    assert result.returncode == 0
    assert server.calls() == ["systemctl start sunsync-backup.service", "update"]
    assert (server.state / "deployed-commit").read_text().strip() == server.remote_head()


def test_a_deployed_commit_is_not_deployed_again(server):
    server.switch.touch()
    server.run()
    server.run()
    assert server.calls() == ["systemctl start sunsync-backup.service", "update"]


def test_the_next_commit_is_deployed(server):
    server.switch.touch()
    server.run()
    server.push_commit()
    server.run()
    assert server.calls().count("update") == 2
    assert (server.state / "deployed-commit").read_text().strip() == server.remote_head()


def test_a_failed_commit_is_not_tried_again_until_main_changes(server):
    server.switch.touch()
    result = server.run(fail_update=True)
    assert result.returncode != 0
    assert (server.state / "failed-commit").read_text().strip() == server.remote_head()
    assert server.calls().count("systemctl start sunsync-backup.service") == 1

    server.run()  # same commit: skipped, and no new backup is taken
    assert server.calls() == ["systemctl start sunsync-backup.service", "update"]

    server.push_commit()
    result = server.run()  # a newer commit is tried and, now working, clears the failure
    assert result.returncode == 0
    assert not (server.state / "failed-commit").exists()
    assert server.calls().count("update") == 2


@pytest.mark.parametrize("name", ["auto-update.sh", "update.sh", "backup.sh", "install-units.sh"])
def test_scripts_have_valid_bash_syntax(name):
    subprocess.run(["bash", "-n", str(DEPLOY / name)], check=True)
