"""Tests for get_file OneDrive download (pa-r9ej).

Previously shelled out to curl via subprocess. When the microsoft-mcp
child container lacks curl, the call raises FileNotFoundError which the
except clause (CalledProcessError only) does not catch. Replaced with
native httpx.stream, which streams to disk without loading into memory.
"""

import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import httpx
import pytest

from microsoft_mcp.tools import get_file


def _metadata(
    *, with_download_url: bool = True, name: str = "test.txt", size: int = 1024
) -> dict:
    meta = {
        "id": "file-123",
        "name": name,
        "size": size,
        "file": {"mimeType": "text/plain"},
    }
    if with_download_url:
        meta["@microsoft.graph.downloadUrl"] = "https://download.example/abc"
    return meta


@contextmanager
def _fake_stream_response(chunks: list[bytes]):
    """Build a context-manager replacement for httpx.stream(...) returning chunks."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.iter_bytes = MagicMock(return_value=iter(chunks))
    yield response


def test_happy_path_writes_bytes_and_returns_metadata(tmp_path):
    download_path = str(tmp_path / "out.txt")
    expected_bytes = b"hello onedrive"

    with (
        patch(
            "microsoft_mcp.tools.graph.request",
            return_value=_metadata(name="out.txt", size=len(expected_bytes)),
        ),
        patch(
            "microsoft_mcp.tools.httpx.stream",
            return_value=_fake_stream_response([b"hello ", b"onedrive"]),
        ),
    ):
        result = get_file(
            file_id="file-123",
            account_id="acct-1",
            download_path=download_path,
        )

    assert os.path.exists(download_path)
    with open(download_path, "rb") as f:
        assert f.read() == expected_bytes

    assert result["path"] == download_path
    assert result["name"] == "out.txt"
    assert result["mime_type"] == "text/plain"
    assert "size_mb" in result


def test_no_download_url_raises_value_error(tmp_path):
    download_path = str(tmp_path / "out.txt")

    with patch(
        "microsoft_mcp.tools.graph.request",
        return_value=_metadata(with_download_url=False),
    ):
        with pytest.raises(ValueError, match="No download URL"):
            get_file(
                file_id="file-123",
                account_id="acct-1",
                download_path=download_path,
            )


def test_file_not_found_raises_value_error(tmp_path):
    download_path = str(tmp_path / "out.txt")

    with patch("microsoft_mcp.tools.graph.request", return_value=None):
        with pytest.raises(ValueError, match="not found"):
            get_file(
                file_id="missing-id",
                account_id="acct-1",
                download_path=download_path,
            )


def test_http_error_during_stream_propagates_clean_exception(tmp_path):
    """An HTTP error during the download must surface as a meaningful Python
    exception — not a subprocess.CalledProcessError (the curl-era failure
    mode) and not a bare FileNotFoundError from missing curl."""
    download_path = str(tmp_path / "out.txt")

    @contextmanager
    def failing_stream(*_args, **_kwargs):
        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )
        yield response

    with (
        patch("microsoft_mcp.tools.graph.request", return_value=_metadata()),
        patch("microsoft_mcp.tools.httpx.stream", side_effect=failing_stream),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            get_file(
                file_id="file-123",
                account_id="acct-1",
                download_path=download_path,
            )


def test_no_subprocess_or_curl_invoked(tmp_path):
    """Regression guard: get_file must NOT shell out. If curl is missing
    from the runtime image, the original implementation raised
    FileNotFoundError; the httpx replacement removes that fragility."""
    download_path = str(tmp_path / "out.txt")

    with (
        patch(
            "microsoft_mcp.tools.graph.request",
            return_value=_metadata(),
        ),
        patch(
            "microsoft_mcp.tools.httpx.stream",
            return_value=_fake_stream_response([b"data"]),
        ),
        patch("microsoft_mcp.tools.subprocess.run") as mock_run,
    ):
        get_file(
            file_id="file-123",
            account_id="acct-1",
            download_path=download_path,
        )

    mock_run.assert_not_called()


def test_large_file_streams_without_loading_into_memory(tmp_path):
    """Multiple chunks must be written sequentially. Confirms we use the
    iter_bytes loop, not response.content."""
    download_path = str(tmp_path / "big.bin")
    chunks = [b"A" * 1024, b"B" * 1024, b"C" * 1024]

    with (
        patch(
            "microsoft_mcp.tools.graph.request",
            return_value=_metadata(name="big.bin", size=3072),
        ),
        patch(
            "microsoft_mcp.tools.httpx.stream",
            return_value=_fake_stream_response(chunks),
        ),
    ):
        get_file(
            file_id="file-123",
            account_id="acct-1",
            download_path=download_path,
        )

    with open(download_path, "rb") as f:
        data = f.read()
    assert data == b"".join(chunks)
    assert len(data) == 3072
