"""Blob-storage helpers for generated podcast audio.

The narrator (hosted or in-process) uploads the finished MP3 to a **private**
blob container; the FastAPI backend streams it back to the browser through the
``/audio/<blob>`` proxy. Both sides authenticate with ``DefaultAzureCredential``
(managed identity in-cloud, ``az login`` locally) — there are no connection
strings or account keys.

If ``AZURE_STORAGE_ACCOUNT_URL`` is not configured the app runs in "local file"
mode instead (see :func:`storage_configured`), so local development needs no
storage account.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Iterator

from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential

from podcaster import config

logger = logging.getLogger(__name__)

# One BlobServiceClient per process (it holds a connection pool + cached token).
_service_client = None
_credential = None


def storage_configured() -> bool:
    """True when a blob account URL is configured (cloud / upload mode)."""
    return bool(config.AZURE_STORAGE_ACCOUNT_URL)


def _client():
    global _service_client
    if _service_client is None:
        from azure.storage.blob import BlobServiceClient

        if not config.AZURE_STORAGE_ACCOUNT_URL:
            raise RuntimeError(
                "AZURE_STORAGE_ACCOUNT_URL is not set. Blob upload/download "
                "requires the storage account URL (e.g. "
                "https://<account>.blob.core.windows.net)."
            )
        _service_client = BlobServiceClient(
            account_url=config.AZURE_STORAGE_ACCOUNT_URL,
            credential=_credential_obj(),
        )
    return _service_client


def _credential_obj() -> DefaultAzureCredential:
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def _current_identity_hint() -> str:
    """Best-effort identity hint from the Storage audience access token claims."""
    try:
        token = _credential_obj().get_token("https://storage.azure.com/.default").token
        parts = token.split(".")
        if len(parts) < 2:
            return "identity=unknown"
        payload = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        oid = claims.get("oid") or ""
        appid = claims.get("appid") or ""
        tid = claims.get("tid") or ""
        return f"identity_oid={oid or 'n/a'} appid={appid or 'n/a'} tid={tid or 'n/a'}"
    except Exception:
        return "identity=unavailable"


def upload_bytes(
    data: bytes,
    blob_name: str,
    *,
    container: str | None = None,
    content_type: str = "audio/mpeg",
) -> str:
    """Upload ``data`` to ``container``/``blob_name`` (overwriting) and return the name."""
    from azure.storage.blob import ContentSettings

    container = container or config.AZURE_STORAGE_CONTAINER
    blob = _client().get_blob_client(container=container, blob=blob_name)
    try:
        blob.upload_blob(
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
    except HttpResponseError as exc:
        code = getattr(exc, "error_code", "") or ""
        if code == "AuthorizationFailure":
            raise RuntimeError(
                "Blob upload unauthorized. "
                f"container={container} account_url={config.AZURE_STORAGE_ACCOUNT_URL} "
                f"{_current_identity_hint()}"
            ) from exc
        raise
    logger.info("Uploaded %d bytes to blob %s/%s", len(data), container, blob_name)
    return blob_name


def download_stream(
    blob_name: str,
    *,
    container: str | None = None,
    chunk_size: int = 1024 * 256,
) -> Iterator[bytes]:
    """Yield the blob's bytes in chunks for streaming back to the client."""
    container = container or config.AZURE_STORAGE_CONTAINER
    blob = _client().get_blob_client(container=container, blob=blob_name)
    downloader = blob.download_blob(max_concurrency=1)
    yield from downloader.chunks()


def blob_exists(blob_name: str, *, container: str | None = None) -> bool:
    """Whether ``container``/``blob_name`` exists."""
    container = container or config.AZURE_STORAGE_CONTAINER
    return _client().get_blob_client(container=container, blob=blob_name).exists()
