import base64
import io
import tarfile

import pytest

from garmin_dashboard.auth import pack_token_directory, restore_token_archive


def test_token_archive_round_trip(tmp_path):
    token_dir = tmp_path / "source" / "garmin_tokens"
    token_dir.mkdir(parents=True)
    (token_dir / "oauth1_token.json").write_text('{"token": "test"}', encoding="utf-8")

    encoded = pack_token_directory(token_dir)
    restored = restore_token_archive(encoded, tmp_path / "restored")

    assert (restored / "oauth1_token.json").read_text(encoding="utf-8") == '{"token": "test"}'


def test_restore_rejects_invalid_base64(tmp_path):
    with pytest.raises(ValueError, match="valid base64"):
        restore_token_archive("not base64!", tmp_path)


def test_restore_rejects_path_traversal(tmp_path):
    buffer = io.BytesIO()
    payload = b"should not escape"
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo("../escaped.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    with pytest.raises(ValueError, match="Unsafe path"):
        restore_token_archive(encoded, tmp_path / "destination")

    assert not (tmp_path / "escaped.txt").exists()
