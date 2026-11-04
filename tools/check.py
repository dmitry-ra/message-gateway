"""Run the same local quality checks used by CI."""

import subprocess
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.check_output(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root
    )
    for name in set(tracked.decode().split("\0")) - {""}:
        name.encode("ascii")
        (root / name).read_bytes().decode("ascii")
    commands = [
        ["black", "--check", "."],
        ["ruff", "check", "."],
        ["mypy", "src", "tests", "tools"],
        ["python", "-m", "compileall", "-q", "src", "tests", "tools"],
        ["pytest"],
    ]
    for command in commands:
        subprocess.run(command, cwd=root, check=True)


if __name__ == "__main__":
    main()
