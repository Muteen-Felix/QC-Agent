"""Build a platform-stable environment fingerprint from doctor snapshots."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def environment_fingerprint(
    python_version: str,
    node_major: str,
    k6_pin: str,
    pip_freeze: str,
) -> str:
    """Return the first 12 SHA-256 hex characters for the shared environment snapshot."""
    packages = [line for line in pip_freeze.replace("\r", "").split("\n") if line]
    packages.sort(
        key=lambda line: (
            line.translate(_ASCII_LOWER).encode("utf-8"),
            line.encode("utf-8"),
        )
    )
    payload = (
        python_version.strip()
        + "node"
        + node_major.strip()
        + "k6"
        + k6_pin.strip()
        + "\n".join(packages)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--node-major", required=True)
    parser.add_argument("--k6-pin", required=True)
    parser.add_argument("--pip-freeze-file", required=True, type=Path)
    args = parser.parse_args()
    freeze = args.pip_freeze_file.read_text(encoding="utf-8-sig")
    print(environment_fingerprint(args.python_version, args.node_major, args.k6_pin, freeze))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
