import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import server


def test_episode_list_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_output_dir", tmp_path)
    monkeypatch.setattr(server.storage, "storage_configured", lambda: False)
    (tmp_path / "new_episode.json").write_text(
        '{"title":"New episode","language":"english","turns":[]}',
        encoding="utf-8",
    )

    response = TestClient(server.app).get("/episodes")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()[0]["title"] == "New episode"


def test_episode_list_uses_local_files_when_blob_storage_is_unavailable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(server, "_output_dir", tmp_path)
    monkeypatch.setattr(server.storage, "storage_configured", lambda: True)

    def unavailable(*args, **kwargs):
        raise server.AzureError("storage unavailable")

    monkeypatch.setattr(server.storage, "list_blobs", unavailable)
    monkeypatch.setattr(server.storage, "blob_exists", unavailable)
    (tmp_path / "new_episode.json").write_text(
        '{"title":"New episode","language":"english","turns":[]}',
        encoding="utf-8",
    )
    (tmp_path / "new_episode.mp3").write_bytes(b"audio")

    response = TestClient(server.app).get("/episodes")

    assert response.status_code == 200
    assert response.json()[0]["audio"] == "/audio/new_episode.mp3"


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
