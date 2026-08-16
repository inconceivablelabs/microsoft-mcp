import httpx
import time
from typing import Any, Iterator
from .auth import get_token

BASE_URL = "https://graph.microsoft.com/v1.0"
# 15 x 320 KiB = 4,915,200 bytes
UPLOAD_CHUNK_SIZE = 15 * 320 * 1024

_client = httpx.Client(timeout=30.0, follow_redirects=True)


def _raise_with_graph_detail(response: httpx.Response) -> None:
    """Raise HTTPStatusError carrying Graph's structured error detail, on 4xx only.

    Graph returns the actionable part of an error in the response BODY, not the
    status line. httpx's bare raise_for_status() discards it, which makes two
    errors with different fixes look identical — notably the transcript 403s,
    where error.code is "AccessDenied" for both and only innerError.code
    distinguishes GraphAccessToTranscriptsDisabled (tenant Graph access off) from
    SpeakerAttributionNotAllowed (attribution off; the same request succeeds
    unattributed). Microsoft's reference directs callers to branch on
    innerError.code because message text is subject to change:
    https://learn.microsoft.com/en-us/graph/api/calltranscript-get?view=graph-rest-1.0

    Returns silently when there is no detail to add (non-4xx, non-JSON body, or a
    body with no error code) so the caller's own raise_for_status() still runs.
    """
    if not (400 <= response.status_code < 500):
        return

    try:
        error_info = response.json().get("error", {})
        code = error_info.get("code", "")
        message = error_info.get("message", "")
        inner_code = (error_info.get("innerError") or {}).get("code", "")
    except Exception:
        return

    if not code:
        return

    detail = f" — {code}: {message}"
    if inner_code:
        detail += f" (innerError: {inner_code})"

    raise httpx.HTTPStatusError(
        f"{response.status_code}{detail}",
        request=response.request,
        response=response,
    )


def request(
    method: str,
    path: str,
    account_id: str | None = None,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    data: bytes | None = None,
    max_retries: int = 3,
    prefer_body_text: bool = False,
) -> dict[str, Any] | None:
    headers = {
        "Authorization": f"Bearer {get_token(account_id)}",
    }

    if method == "GET":
        if (
            prefer_body_text
            or "$search" in (params or {})
            or "body" in (params or {}).get("$select", "")
        ):
            headers["Prefer"] = 'outlook.body-content-type="text"'
    else:
        headers["Content-Type"] = (
            "application/json" if json is not None else "application/octet-stream"
        )

    if params and (
        "$search" in params
        or "contains(" in params.get("$filter", "")
        or "/any(" in params.get("$filter", "")
    ):
        headers["ConsistencyLevel"] = "eventual"
        params.setdefault("$count", "true")

    retry_count = 0
    while retry_count <= max_retries:
        try:
            response = _client.request(
                method=method,
                url=f"{BASE_URL}{path}",
                headers=headers,
                params=params,
                json=json,
                content=data,
            )

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "5"))
                if retry_count < max_retries:
                    time.sleep(min(retry_after, 60))
                    retry_count += 1
                    continue

            if response.status_code >= 500 and retry_count < max_retries:
                wait_time = (2**retry_count) * 1
                time.sleep(wait_time)
                retry_count += 1
                continue

            _raise_with_graph_detail(response)
            response.raise_for_status()

            if response.content:
                return response.json()
            return None

        except httpx.HTTPStatusError as e:
            if retry_count < max_retries and e.response.status_code >= 500:
                wait_time = (2**retry_count) * 1
                time.sleep(wait_time)
                retry_count += 1
                continue
            raise

    return None


def request_text(
    path: str,
    account_id: str | None = None,
    accept: str = "text/vtt",
    max_retries: int = 3,
) -> str:
    """Make a GET request that returns raw text (not JSON).

    Used for endpoints like transcript content that return text/vtt or text/plain.
    """
    headers = {
        "Authorization": f"Bearer {get_token(account_id)}",
        "Accept": accept,
    }

    retry_count = 0
    while retry_count <= max_retries:
        try:
            response = _client.request(
                method="GET",
                url=f"{BASE_URL}{path}",
                headers=headers,
            )

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "5"))
                if retry_count < max_retries:
                    time.sleep(min(retry_after, 60))
                    retry_count += 1
                    continue

            if response.status_code >= 500 and retry_count < max_retries:
                wait_time = (2**retry_count) * 1
                time.sleep(wait_time)
                retry_count += 1
                continue

            _raise_with_graph_detail(response)
            response.raise_for_status()

            if not response.text:
                raise ValueError(f"Empty response from {path}")
            return response.text

        except httpx.HTTPStatusError as e:
            if retry_count < max_retries and e.response.status_code >= 500:
                wait_time = (2**retry_count) * 1
                time.sleep(wait_time)
                retry_count += 1
                continue
            raise

    raise ValueError("Failed to fetch text content after all retries")


def request_paginated(
    path: str,
    account_id: str | None = None,
    params: dict[str, Any] | None = None,
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Make paginated requests following @odata.nextLink"""
    items_returned = 0
    next_link = None

    while True:
        if next_link:
            result = request("GET", next_link.replace(BASE_URL, ""), account_id)
        else:
            result = request("GET", path, account_id, params=params)

        if not result:
            break

        if "value" in result:
            for item in result["value"]:
                if limit and items_returned >= limit:
                    return
                yield item
                items_returned += 1

        next_link = result.get("@odata.nextLink")
        if not next_link:
            break


def download_raw(
    path: str, account_id: str | None = None, max_retries: int = 3
) -> bytes:
    headers = {"Authorization": f"Bearer {get_token(account_id)}"}

    retry_count = 0
    while retry_count <= max_retries:
        try:
            response = _client.get(f"{BASE_URL}{path}", headers=headers)

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "5"))
                if retry_count < max_retries:
                    time.sleep(min(retry_after, 60))
                    retry_count += 1
                    continue

            if response.status_code >= 500 and retry_count < max_retries:
                wait_time = (2**retry_count) * 1
                time.sleep(wait_time)
                retry_count += 1
                continue

            _raise_with_graph_detail(response)
            response.raise_for_status()
            return response.content

        except httpx.HTTPStatusError as e:
            if retry_count < max_retries and e.response.status_code >= 500:
                wait_time = (2**retry_count) * 1
                time.sleep(wait_time)
                retry_count += 1
                continue
            raise

    raise ValueError("Failed to download file after all retries")


def _do_chunked_upload(
    upload_url: str,
    data: bytes,
    headers: dict[str, str],
) -> dict[str, Any]:
    """Internal helper for chunked uploads"""
    file_size = len(data)

    for i in range(0, file_size, UPLOAD_CHUNK_SIZE):
        chunk_start = i
        chunk_end = min(i + UPLOAD_CHUNK_SIZE, file_size)
        chunk = data[chunk_start:chunk_end]

        chunk_headers = headers.copy()
        chunk_headers["Content-Length"] = str(len(chunk))
        chunk_headers["Content-Range"] = (
            f"bytes {chunk_start}-{chunk_end - 1}/{file_size}"
        )

        retry_count = 0
        while retry_count <= 3:
            try:
                response = _client.put(upload_url, content=chunk, headers=chunk_headers)

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "5"))
                    if retry_count < 3:
                        time.sleep(min(retry_after, 60))
                        retry_count += 1
                        continue

                response.raise_for_status()

                if response.status_code in (200, 201):
                    return response.json()
                break

            except httpx.HTTPStatusError as e:
                if retry_count < 3 and e.response.status_code >= 500:
                    time.sleep((2**retry_count) * 1)
                    retry_count += 1
                    continue
                raise

    raise ValueError("Upload completed but no final response received")


def create_upload_session(
    path: str,
    account_id: str | None = None,
    item_properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an upload session for large files"""
    payload = {"item": item_properties or {}}
    result = request("POST", f"{path}/createUploadSession", account_id, json=payload)
    if not result:
        raise ValueError("Failed to create upload session")
    return result


def upload_large_file(
    path: str,
    data: bytes,
    account_id: str | None = None,
    item_properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upload a large file using upload sessions"""
    file_size = len(data)

    if file_size <= UPLOAD_CHUNK_SIZE:
        result = request("PUT", f"{path}/content", account_id, data=data)
        if not result:
            raise ValueError("Failed to upload file")
        return result

    session = create_upload_session(path, account_id, item_properties)
    upload_url = session["uploadUrl"]

    headers = {"Authorization": f"Bearer {get_token(account_id)}"}
    return _do_chunked_upload(upload_url, data, headers)


def create_mail_upload_session(
    message_id: str,
    attachment_item: dict[str, Any],
    account_id: str | None = None,
) -> dict[str, Any]:
    """Create an upload session for large mail attachments"""
    result = request(
        "POST",
        f"/me/messages/{message_id}/attachments/createUploadSession",
        account_id,
        json={"AttachmentItem": attachment_item},
    )
    if not result:
        raise ValueError("Failed to create mail attachment upload session")
    return result


def upload_large_mail_attachment(
    message_id: str,
    name: str,
    data: bytes,
    account_id: str | None = None,
    content_type: str = "application/octet-stream",
) -> dict[str, Any]:
    """Upload a large mail attachment using upload sessions"""
    file_size = len(data)

    attachment_item = {
        "attachmentType": "file",
        "name": name,
        "size": file_size,
        "contentType": content_type,
    }

    session = create_mail_upload_session(message_id, attachment_item, account_id)
    upload_url = session["uploadUrl"]

    headers = {"Authorization": f"Bearer {get_token(account_id)}"}
    return _do_chunked_upload(upload_url, data, headers)


def search_query(
    query: str,
    entity_types: list[str],
    account_id: str | None = None,
    limit: int = 50,
    fields: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Use the modern /search/query API endpoint"""
    payload = {
        "requests": [
            {
                "entityTypes": entity_types,
                "query": {"queryString": query},
                "size": min(limit, 25),
                "from": 0,
            }
        ]
    }

    if fields:
        payload["requests"][0]["fields"] = fields

    items_returned = 0

    while True:
        result = request("POST", "/search/query", account_id, json=payload)

        if not result or "value" not in result:
            break

        for response in result["value"]:
            if "hitsContainers" in response:
                for container in response["hitsContainers"]:
                    if "hits" in container:
                        for hit in container["hits"]:
                            if limit and items_returned >= limit:
                                return
                            resource = hit["resource"]
                            # Search API returns the resource ID as hitId
                            # on the hit container, not inside the resource.
                            # Inject it so results match the list endpoint shape.
                            if "id" not in resource and "hitId" in hit:
                                resource["id"] = hit["hitId"]
                            yield resource
                            items_returned += 1

        if "@odata.nextLink" in result:
            break

        has_more = False
        for response in result.get("value", []):
            for container in response.get("hitsContainers", []):
                if container.get("moreResultsAvailable"):
                    has_more = True
                    break

        if not has_more:
            break

        payload["requests"][0]["from"] += payload["requests"][0]["size"]
