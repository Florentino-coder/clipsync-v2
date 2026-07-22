#!/usr/bin/env python3
"""Issue a ClipSync offline license token (Ed25519).

Private key must live OUTSIDE the repo. Set CLIPSYNC_LICENSE_KEY to a path
containing either:
  - 32 raw Ed25519 private key bytes, or
  - a PEM-encoded PKCS8/OpenSSH private key.

Deviation note: repo root ``tools/`` is gitignored, so this CLI lives at
``pc/scripts/issue_license.py`` (tracked) instead of ``tools/issue_license.py``.

Example:
  set CLIPSYNC_LICENSE_KEY=C:\\secrets\\clipsync_ed25519.key
  python pc/scripts/issue_license.py --device-id X --customer cust_001 --days 30
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow running as ``python pc/scripts/issue_license.py`` from repo root or pc/.
_PC_ROOT = Path(__file__).resolve().parents[1]
if str(_PC_ROOT) not in sys.path:
    sys.path.insert(0, str(_PC_ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from clipsync.license import issue_token


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    data = path.read_bytes()
    if b"-----BEGIN" in data:
        key = serialization.load_pem_private_key(data, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise SystemExit("CLIPSYNC_LICENSE_KEY must be an Ed25519 private key")
        return key
    if len(data) == 32:
        return Ed25519PrivateKey.from_private_bytes(data)
    # Allow hex-encoded 32-byte keys in text files.
    text = data.decode("ascii", errors="ignore").strip()
    try:
        raw = bytes.fromhex(text)
    except ValueError as exc:
        raise SystemExit(
            "Private key file must be 32 raw bytes, hex, or PEM Ed25519"
        ) from exc
    if len(raw) != 32:
        raise SystemExit("Hex private key must decode to exactly 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Issue ClipSync license.token")
    parser.add_argument("--device-id", required=True, help="Target device id")
    parser.add_argument("--customer", required=True, help="Customer id label")
    parser.add_argument("--days", type=int, required=True, help="Validity days")
    parser.add_argument(
        "--out",
        default="license.token",
        help="Output path (default: license.token)",
    )
    args = parser.parse_args(argv)

    key_path = os.environ.get("CLIPSYNC_LICENSE_KEY")
    if not key_path:
        print(
            "error: set CLIPSYNC_LICENSE_KEY to the private key file path",
            file=sys.stderr,
        )
        return 2

    private_key = _load_private_key(Path(key_path))
    token = issue_token(
        private_key,
        device_id=args.device_id,
        customer=args.customer,
        days=args.days,
    )
    out_path = Path(args.out)
    out_path.write_text(token + "\n", encoding="utf-8")
    print(f"Wrote {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
