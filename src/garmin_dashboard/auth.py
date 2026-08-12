"""Helpers for safely packing and restoring the Garmin token directory."""

from __future__ import annotations

import base64
import binascii
import io
import os
import tarfile
from collections.abc import Mapping
from pathlib import Path

TOKEN_DIRECTORY_NAME = "garmin_tokens"


def pack_token_directory(token_dir: Path) -> str:
    """Return a base64-encoded tar archive suitable for a GitHub secret."""
    if not token_dir.is_dir():
        raise FileNotFoundError(f"Token directory does not exist: {token_dir}")

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.add(token_dir, arcname=TOKEN_DIRECTORY_NAME)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def restore_token_archive(encoded: str, destination: Path) -> Path:
    """Decode and safely extract a Garmin token archive into *destination*."""
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("GARMIN_TOKENS_B64 is not valid base64") from exc

    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            for member in archive.getmembers():
                member_path = (destination / member.name).resolve()
                if os.path.commonpath((destination, member_path)) != str(destination):
                    raise ValueError(f"Unsafe path in token archive: {member.name}")
                if member.issym() or member.islnk():
                    raise ValueError(f"Links are not allowed in token archive: {member.name}")
            archive.extractall(destination, filter="data")
    except tarfile.TarError as exc:
        raise ValueError("GARMIN_TOKENS_B64 is not a valid gzip tar archive") from exc

    token_dir = destination / TOKEN_DIRECTORY_NAME
    if not token_dir.is_dir():
        raise ValueError(f"Token archive did not contain a {TOKEN_DIRECTORY_NAME}/ directory")
    return token_dir


def restore_token_from_environment(
    environment: Mapping[str, str] | None = None,
    destination: Path = Path("."),
) -> Path:
    """Restore GARMIN_TOKENS_B64 from an environment mapping."""
    environment = os.environ if environment is None else environment
    encoded = environment.get("GARMIN_TOKENS_B64")
    if not encoded:
        raise ValueError("GARMIN_TOKENS_B64 secret is not set")
    return restore_token_archive(encoded, destination)
