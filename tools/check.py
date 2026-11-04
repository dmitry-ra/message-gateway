"""Run the same local quality checks used by CI."""

import subprocess
from pathlib import Path


def check_file(path: Path, name: str) -> None:
    name.encode("ascii")
    content = path.read_bytes()
    if name == "docs/demo.gif":
        valid = (
            14 <= len(content) <= 3 * 1024 * 1024
            and content[:6] in (b"GIF87a", b"GIF89a")
            and content[-1:] == b";"
            and 0 < int.from_bytes(content[6:8], "little") <= 1280
            and 0 < int.from_bytes(content[8:10], "little") <= 960
        )
        if not valid:
            raise ValueError("Invalid or oversized owned documentation GIF")
        return
    content.decode("ascii")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.check_output(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root
    )
    for name in set(tracked.decode().split("\0")) - {""}:
        check_file(root / name, name)
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
