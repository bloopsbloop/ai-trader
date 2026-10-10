"""Safe cross-platform path handling for frozen test artifact manifests."""
from __future__ import annotations

import hashlib
from pathlib import Path, PureWindowsPath


def resolve_manifest_path(root: Path, manifest_path: str) -> Path:
    """Resolve a Windows- or POSIX-style relative manifest path below root."""
    if not isinstance(manifest_path, str) or not manifest_path:
        raise ValueError("manifest path must be a non-empty string")

    pure = PureWindowsPath(manifest_path)
    if pure.drive or pure.root:
        raise ValueError(f"manifest path must be relative: {manifest_path!r}")

    parts = tuple(part for part in pure.parts if part not in ("", "."))
    if not parts or ".." in parts:
        raise ValueError(f"manifest path escapes fixture root: {manifest_path!r}")

    resolved_root = Path(root).resolve()
    resolved = resolved_root.joinpath(*parts).resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"manifest path escapes fixture root: {manifest_path!r}") from error
    return resolved


def assert_manifest_sha256(root: Path, manifest_path: str, expected: str) -> Path:
    """Read the referenced file and fail when its SHA-256 differs."""
    path = resolve_manifest_path(root, manifest_path)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise AssertionError(
            f"SHA-256 mismatch for {manifest_path!r}: expected {expected}, got {actual}"
        )
    return path
