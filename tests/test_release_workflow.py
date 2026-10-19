"""Stable release history must not be truncated by opt-in build tags."""

import re
import subprocess
import sys
import textwrap
from pathlib import Path


def test_stable_history_keeps_stable_and_alias_tags(tmp_path):
    workflow = (Path(__file__).parents[1] / ".github/workflows/release.yml").read_text()
    section = workflow.split("- name: Exclude build prereleases from stable history\n", 1)[1].split("\n      - ", 1)[0]
    program = re.search(r"^          python - <<'PY'\n(.*?)^          PY$", section, re.M | re.S)
    assert program is not None

    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True)  # noqa: S603, S607

    git("init", "--quiet")
    git(
        "-c",
        "user.name=Release test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--quiet",
        "--allow-empty",
        "--no-gpg-sign",
        "-m",
        "initial",
    )
    # Disposable fixture tags must not inherit workstation signing requirements.
    for tag in ("v2.0.0", "latest", "v3.0.0-rc.4.capabilities", "v3.0.0-beta.1"):
        git("-c", "tag.gpgSign=false", "tag", tag)
    # The executed program belongs to this repository, not external input.
    subprocess.run([sys.executable, "-c", textwrap.dedent(program[1])], cwd=tmp_path, check=True)  # noqa: S603
    assert git("tag", "--list").splitlines() == ["latest", "v2.0.0"]
