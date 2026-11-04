"""Check queue and shared authentication invariants in isolated source copies."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MUTATIONS = {
    "busy delivery": (
        "target.state != Readiness.READY or not target.queue",
        "target.state == Readiness.UNKNOWN or not target.queue",
    ),
    "batching setting ignored": (
        "self.batch_limit if target.batching else 1",
        "self.batch_limit",
    ),
    "FIFO reversed": (
        "messages = [target.queue.popleft() for _ in range(count)]",
        "messages = [target.queue.pop() for _ in range(count)]",
    ),
    "shared token bypass": (
        "if self.token is not None and not hmac.compare_digest(",
        "if False and not hmac.compare_digest(",
    ),
}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    for name, mutation in [("control", None), *MUTATIONS.items()]:
        with tempfile.TemporaryDirectory(prefix="gateway-mutation-") as directory:
            copy = Path(directory)
            shutil.copytree(
                root / "src", copy / "src", ignore=shutil.ignore_patterns("__pycache__")
            )
            shutil.copytree(
                root / "tests", copy / "tests", ignore=shutil.ignore_patterns("__pycache__")
            )
            if mutation is not None:
                relative = (
                    "interfaces/server.py" if name == "shared token bypass" else "core/service.py"
                )
                path = copy / "src/message_gateway" / relative
                source = path.read_text()
                before, after = mutation
                if source.count(before) != 1:
                    raise RuntimeError("Mutation anchor changed: " + name)
                path.write_text(source.replace(before, after))
            environment = dict(os.environ, PYTHONPATH=str(copy / "src") + os.pathsep + str(copy))
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "--import-mode=importlib",
                    (
                        "tests/integration/test_server.py"
                        if name == "shared token bypass"
                        else "tests/unit/test_service.py"
                    ),
                ],
                cwd=copy,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            expected = 0 if mutation is None else 1
            if result.returncode != expected or (
                mutation is not None and "FAILED" not in result.stdout
            ):
                raise RuntimeError(name + "\n" + result.stdout + result.stderr)
            print(name + ": verified")


if __name__ == "__main__":
    main()
