import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LIB_SH = REPO_ROOT / "scripts" / "gcp" / "bench" / "lib.sh"
REMOTE_SETUP_SH = REPO_ROOT / "scripts" / "gcp" / "bench" / "remote-setup.sh"


def run_bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_image_family_matches_candidate_architecture():
    result = run_bash(
        f"""
        set -euo pipefail
        source {LIB_SH}
        image_family_for_machine n4-standard-8
        printf '\\n'
        image_family_for_machine c4-standard-16
        printf '\\n'
        image_family_for_machine c4a-standard-8
        """
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "ubuntu-2404-lts-amd64",
        "ubuntu-2404-lts-amd64",
        "ubuntu-2404-lts-arm64",
    ]


def test_remote_setup_installs_openssl_development_headers():
    setup_script = REMOTE_SETUP_SH.read_text()

    assert "libssl-dev" in setup_script
