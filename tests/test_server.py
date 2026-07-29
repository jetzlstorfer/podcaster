import asyncio

import pytest
from fastapi import HTTPException

import server


def test_delete_episode_removes_all_related_local_files(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_output_dir", tmp_path)
    monkeypatch.setattr(server.storage, "storage_configured", lambda: False)
    for suffix in ("json", "mp3", "png", "metadata"):
        (tmp_path / f"my_episode.{suffix}").write_bytes(b"data")
    unrelated = tmp_path / "another_episode.mp3"
    unrelated.write_bytes(b"data")

    response = asyncio.run(server.delete_episode("my_episode.json"))

    assert response.status_code == 204
    assert list(tmp_path.glob("my_episode.*")) == []
    assert unrelated.is_file()


def test_delete_episode_removes_blob_and_local_files(tmp_path, monkeypatch):
    deleted_blobs = []
    monkeypatch.setattr(server, "_output_dir", tmp_path)
    monkeypatch.setattr(server.storage, "storage_configured", lambda: True)
    monkeypatch.setattr(
        server.storage,
        "list_blobs",
        lambda: [
            {"name": "my_episode.json"},
            {"name": "my_episode.mp3"},
            {"name": "other.json"},
            {"name": "folder/my_episode.png"},
        ],
    )
    monkeypatch.setattr(server.storage, "delete_blob", deleted_blobs.append)
    local_image = tmp_path / "my_episode.png"
    local_image.write_bytes(b"data")

    response = asyncio.run(server.delete_episode("my_episode.json"))

    assert response.status_code == 204
    assert deleted_blobs == ["my_episode.json", "my_episode.mp3"]
    assert not local_image.exists()


def test_delete_episode_returns_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_output_dir", tmp_path)
    monkeypatch.setattr(server.storage, "storage_configured", lambda: False)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(server.delete_episode("missing.json"))

    assert exc_info.value.status_code == 404
