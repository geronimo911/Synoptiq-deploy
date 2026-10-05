"""Fail-closed real-data smoke-test entrypoint."""
from __future__ import annotations

import os
import sys
import argparse
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from dotenv import dotenv_values
    _root = Path(__file__).resolve().parents[1]
    _values = {}
    for _path in (_root / "backend" / ".env", _root / ".env"):
        for _key, _value in dotenv_values(_path).items():
            if _value is not None and _value.strip():
                _values[_key] = _value
    for _key, _value in _values.items():
        if not os.getenv(_key, "").strip():
            os.environ[_key] = _value
except ImportError:
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real provider and alignment smoke test")
    default_end = (datetime.now(timezone.utc).date() - timedelta(days=5)).isoformat()
    parser.add_argument("--start", default=default_end)
    parser.add_argument("--end", default=default_end)
    parser.add_argument("--ecmwf-source", choices=("aws", "historical", "operational"), default=os.getenv("ECMWF_SOURCE_MODE", "aws"))
    args = parser.parse_args()
    missing = []
    if not os.getenv("EARTHDATA_TOKEN") and not (
        os.getenv("NASA_EARTHDATA_USERNAME") and os.getenv("NASA_EARTHDATA_PASSWORD")
    ):
        missing.append("EARTHDATA_TOKEN (or NASA_EARTHDATA_USERNAME/NASA_EARTHDATA_PASSWORD)")
    if missing:
        print("BLOCKED: missing credentials: " + ", ".join(missing), file=sys.stderr)
        return 2
    command = [
        sys.executable,
        str(Path(__file__).with_name("run_real_training.py")),
        "--start", args.start,
        "--end", args.end,
        "--ecmwf-source", "aws" if args.ecmwf_source == "historical" else args.ecmwf_source,
        "--smoke-only",
    ]
    return subprocess.run(command, cwd=Path(__file__).resolve().parents[1]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
