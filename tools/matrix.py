"""Run local compatibility checks in disposable Python 3.12/3.13 environments."""

import os
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    for version in ["3.12", "3.13"]:
        with tempfile.TemporaryDirectory(prefix="gateway-python-" + version + "-") as directory:
            environment = dict(os.environ, UV_PROJECT_ENVIRONMENT=directory + "/env")
            subprocess.run(
                ["uv", "sync", "--locked", "--python", version],
                cwd=root,
                env=environment,
                check=True,
            )
            subprocess.run(
                ["uv", "run", "--locked", "--python", version, "python", "tools/check.py"],
                cwd=root,
                env=environment,
                check=True,
            )


if __name__ == "__main__":
    main()
