import base64
import datetime as dt
import pathlib as pl
import subprocess
import tempfile
from typing import Any, Literal

import httpx
from fastmcp import FastMCP
from . import graph, auth, address_resolution

mcp = FastMCP("microsoft-365")

FOLDERS = {
    k.casefold(): v
    for k, v in {
        "inbox": "inbox",
        "sent": "sentitems",
        "drafts": "drafts",
        "deleted": "deleteditems",
        "junk": "junkemail",
        "archive": "archive",
    }.items()
}


def _resolve_folder_id(folder_name: str, account_id: str) -> str | None:
    """Resolve a mail folder display name to its Graph folder ID.

    Searches top-level folders first, then child folders one level deep.
    Graph's `/me/mailFolders/{folder}/messages` endpoint only accepts
    well-known names (inbox, sentitems, ...) or folder IDs — passing a
    custom display name returns ErrorInvalidIdMalformed. Callers should
    use this when the name isn't in FOLDERS.

    Uses request_paginated (not request) — Graph returns only ~10
    folders per page by default, and newly-created folders sort last in
    the default childFolders order, so a non-paginated listing silently
    misses them (mcp-fk1).

    Returns the folder ID, or None if no match is found.
    """
    target = folder_name.lower()

    top_folders = list(graph.request_paginated("/me/mailFolders", account_id))

    # Top-level folders
    for folder in top_folders:
        if folder["displayName"].lower() == target:
            return folder["id"]

    # Child folders, one level deep
    for parent in top_folders:
        children = graph.request_paginated(
            f"/me/mailFolders/{parent['id']}/childFolders", account_id
        )
        for child in children:
            if child["displayName"].lower() == target:
                return child["id"]

    return None


@mcp.tool(name="list_accounts")
def list_accounts() -> list[dict[str, str]]:
    """List all signed-in Microsoft accounts.

    IMPORTANT: Call this first to get the account_id (a UUID string like
    '39c06527-...') required by all other tools. Do NOT use an email
    address as account_id — it must be the exact UUID returned here.
    """
    return [
        {"username": acc.username, "account_id": acc.account_id}
        for acc in auth.list_accounts()
    ]


@mcp.tool(name="authenticate_account")
def authenticate_account() -> dict[str, str]:
    """Authenticate a new Microsoft account using device flow authentication

    Returns authentication instructions and device code for the user to complete authentication.
    The user must visit the URL and enter the code to authenticate their Microsoft account.
    """
    app = auth.get_app()
    flow = app.initiate_device_flow(scopes=auth.SCOPES)

    if "user_code" not in flow:
        error_msg = flow.get("error_description", "Unknown error")
        raise Exception(f"Failed to get device code: {error_msg}")

    verification_url = flow.get(
        "verification_uri",
        flow.get("verification_url", "https://microsoft.com/devicelogin"),
    )

    return {
        "status": "authentication_required",
        "instructions": "To authenticate a new Microsoft account:",
        "step1": f"Visit: {verification_url}",
        "step2": f"Enter code: {flow['user_code']}",
        "step3": "Sign in with the Microsoft account you want to add",
        "step4": "After authenticating, use the 'complete_authentication' tool to finish the process",
        "device_code": flow["user_code"],
        "verification_url": verification_url,
        "expires_in": flow.get("expires_in", 900),
        "_flow_cache": str(flow),
    }


@mcp.tool(name="complete_authentication")
def complete_authentication(flow_cache: str) -> dict[str, str]:
    """Complete the authentication process after the user has entered the device code

    Args:
        flow_cache: The flow data returned from authenticate_account (the _flow_cache field)

    Returns:
        Account information if authentication was successful
    """
    import ast

    try:
        flow = ast.literal_eval(flow_cache)
    except (ValueError, SyntaxError):
        raise ValueError("Invalid flow cache data")

    app = auth.get_app()
    result = app.acquire_token_by_device_flow(flow)

    if "error" in result:
        error_msg = result.get("error_description", result["error"])
        if "authorization_pending" in error_msg:
            return {
                "status": "pending",
                "message": "Authentication is still pending. The user needs to complete the authentication process.",
                "instructions": "Please ensure you've visited the URL and entered the code, then try again.",
            }
        raise Exception(f"Authentication failed: {error_msg}")

    # Save the token cache
    cache = app.token_cache
    if isinstance(cache, auth.msal.SerializableTokenCache) and cache.has_state_changed:
        auth._write_cache(cache.serialize())

    # Get the newly added account
    accounts = app.get_accounts()
    if accounts:
        # Find the account that matches the token we just got
        for account in accounts:
            if (
                account.get("username", "").lower()
                == result.get("id_token_claims", {})
                .get("preferred_username", "")
                .lower()
            ):
                return {
                    "status": "success",
                    "username": account["username"],
                    "account_id": account["home_account_id"],
                    "message": f"Successfully authenticated {account['username']}",
                }
        # If exact match not found, return the last account
        account = accounts[-1]
        return {
            "status": "success",
            "username": account["username"],
            "account_id": account["home_account_id"],
            "message": f"Successfully authenticated {account['username']}",
        }

    return {
        "status": "error",
        "message": "Authentication succeeded but no account was found",
    }


@mcp.tool(name="list_emails")
def list_emails(
    account_id: str,
    folder: str = "inbox",
    limit: int = 10,
    include_body: bool = True,
) -> list[dict[str, Any]]:
    """List emails from specified folder.

    Args:
        account_id: UUID from list_accounts (not an email address)
    """
    key = folder.casefold()
    if key in FOLDERS:
        folder_path = FOLDERS[key]
    else:
        # Custom/user folder — Graph requires the folder ID here, not the
        # display name. Resolve by walking /me/mailFolders (top level +
        # one level of children), matching move_email's pattern.
        folder_id = _resolve_folder_id(folder, account_id)
        if not folder_id:
            raise ValueError(f"Folder '{folder}' not found")
        folder_path = folder_id

    if include_body:
        select_fields = "id,subject,from,toRecipients,ccRecipients,receivedDateTime,hasAttachments,body,conversationId,isRead"
    else:
        select_fields = "id,subject,from,toRecipients,receivedDateTime,hasAttachments,conversationId,isRead"

    params = {
        "$top": min(limit, 100),
        "$select": select_fields,
        "$orderby": "receivedDateTime desc",
    }

    emails = list(
        graph.request_paginated(
            f"/me/mailFolders/{folder_path}/messages",
            account_id,
            params=params,
            limit=limit,
        )
    )

    # Apply X.500 DN → SMTP rewriting in place (pa-jsa6).
    for msg in emails:
        address_resolution.resolve_x500_in_message(msg, account_id)
    return emails


@mcp.tool(name="get_email")
def get_email(
    email_id: str,
    account_id: str,
    include_body: bool = True,
    body_max_length: int = 50000,
    include_attachments: bool = True,
    body_format: Literal["text", "html"] = "text",
) -> dict[str, Any]:
    """Get email details with size limits

    Args:
        email_id: The email ID
        account_id: The account ID
        include_body: Whether to include the email body (default: True)
        body_max_length: Maximum characters for body content (default: 50000)
        include_attachments: Whether to include attachment metadata (default: True)
        body_format: Body format — 'text' (default, markup-free) or 'html' (raw stored HTML)
    """
    params = {}
    if include_attachments:
        params["$expand"] = "attachments($select=id,name,size,contentType)"

    result = graph.request(
        "GET",
        f"/me/messages/{email_id}",
        account_id,
        params=params,
        prefer_body_text=(body_format == "text"),
    )
    if not result:
        raise ValueError(f"Email with ID {email_id} not found")

    # Truncate body if needed
    if include_body and "body" in result and "content" in result["body"]:
        content = result["body"]["content"]
        if len(content) > body_max_length:
            result["body"]["content"] = (
                content[:body_max_length]
                + f"\n\n[Content truncated - {len(content)} total characters]"
            )
            result["body"]["truncated"] = True
            result["body"]["total_length"] = len(content)
    elif not include_body and "body" in result:
        del result["body"]

    # Remove attachment content bytes to reduce size
    if "attachments" in result and result["attachments"]:
        for attachment in result["attachments"]:
            if "contentBytes" in attachment:
                del attachment["contentBytes"]

    address_resolution.resolve_x500_in_message(result, account_id)
    return result


@mcp.tool(name="create_email_draft")
def create_email_draft(
    account_id: str,
    to: str | list[str],
    subject: str,
    body: str,
    cc: str | list[str] | None = None,
    attachments: str | list[str] | None = None,
    content_type: str = "HTML",
) -> dict[str, Any]:
    """Create an email draft with file path(s) as attachments.

    Args:
        content_type: Body format — 'HTML' (default) or 'Text'.
    """
    to_list = [to] if isinstance(to, str) else to

    message = {
        "subject": subject,
        "body": {"contentType": content_type, "content": body},
        "toRecipients": [{"emailAddress": {"address": addr}} for addr in to_list],
    }

    if cc:
        cc_list = [cc] if isinstance(cc, str) else cc
        message["ccRecipients"] = [
            {"emailAddress": {"address": addr}} for addr in cc_list
        ]

    small_attachments = []
    large_attachments = []

    if attachments:
        # Convert single path to list
        attachment_paths = (
            [attachments] if isinstance(attachments, str) else attachments
        )
        for file_path in attachment_paths:
            path = pl.Path(file_path).expanduser().resolve()
            content_bytes = path.read_bytes()
            att_size = len(content_bytes)
            att_name = path.name

            if att_size < 3 * 1024 * 1024:
                small_attachments.append(
                    {
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": att_name,
                        "contentBytes": base64.b64encode(content_bytes).decode("utf-8"),
                    }
                )
            else:
                large_attachments.append(
                    {
                        "name": att_name,
                        "content_bytes": content_bytes,
                        "content_type": "application/octet-stream",
                    }
                )

    if small_attachments:
        message["attachments"] = small_attachments

    result = graph.request("POST", "/me/messages", account_id, json=message)
    if not result:
        raise ValueError("Failed to create email draft")

    message_id = result["id"]

    for att in large_attachments:
        graph.upload_large_mail_attachment(
            message_id,
            att["name"],
            att["content_bytes"],
            account_id,
            att.get("content_type", "application/octet-stream"),
        )

    return result


@mcp.tool(name="send_email")
def send_email(
    account_id: str,
    to: str | list[str],
    subject: str,
    body: str,
    cc: str | list[str] | None = None,
    attachments: str | list[str] | None = None,
    content_type: str = "HTML",
) -> dict[str, str]:
    """Send an email immediately with file path(s) as attachments.

    Args:
        content_type: Body format — 'HTML' (default) or 'Text'.
    """
    to_list = [to] if isinstance(to, str) else to

    message = {
        "subject": subject,
        "body": {"contentType": content_type, "content": body},
        "toRecipients": [{"emailAddress": {"address": addr}} for addr in to_list],
    }

    if cc:
        cc_list = [cc] if isinstance(cc, str) else cc
        message["ccRecipients"] = [
            {"emailAddress": {"address": addr}} for addr in cc_list
        ]

    # Check if we have large attachments
    has_large_attachments = False
    processed_attachments = []

    if attachments:
        # Convert single path to list
        attachment_paths = (
            [attachments] if isinstance(attachments, str) else attachments
        )
        for file_path in attachment_paths:
            path = pl.Path(file_path).expanduser().resolve()
            content_bytes = path.read_bytes()
            att_size = len(content_bytes)
            att_name = path.name

            processed_attachments.append(
                {
                    "name": att_name,
                    "content_bytes": content_bytes,
                    "content_type": "application/octet-stream",
                    "size": att_size,
                }
            )

            if att_size >= 3 * 1024 * 1024:
                has_large_attachments = True

    if not has_large_attachments and processed_attachments:
        message["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": att["name"],
                "contentBytes": base64.b64encode(att["content_bytes"]).decode("utf-8"),
            }
            for att in processed_attachments
        ]
        graph.request("POST", "/me/sendMail", account_id, json={"message": message})
        return {"status": "sent"}
    elif has_large_attachments:
        # Create draft first, then add large attachments, then send
        # We need to handle large attachments manually here
        to_list = [to] if isinstance(to, str) else to
        message = {
            "subject": subject,
            "body": {"contentType": content_type, "content": body},
            "toRecipients": [{"emailAddress": {"address": addr}} for addr in to_list],
        }
        if cc:
            cc_list = [cc] if isinstance(cc, str) else cc
            message["ccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in cc_list
            ]

        result = graph.request("POST", "/me/messages", account_id, json=message)
        if not result:
            raise ValueError("Failed to create email draft")

        message_id = result["id"]

        for att in processed_attachments:
            if att["size"] >= 3 * 1024 * 1024:
                graph.upload_large_mail_attachment(
                    message_id,
                    att["name"],
                    att["content_bytes"],
                    account_id,
                    att.get("content_type", "application/octet-stream"),
                )
            else:
                small_att = {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": att["name"],
                    "contentBytes": base64.b64encode(att["content_bytes"]).decode(
                        "utf-8"
                    ),
                }
                graph.request(
                    "POST",
                    f"/me/messages/{message_id}/attachments",
                    account_id,
                    json=small_att,
                )

        graph.request("POST", f"/me/messages/{message_id}/send", account_id)
        return {"status": "sent"}
    else:
        graph.request("POST", "/me/sendMail", account_id, json={"message": message})
        return {"status": "sent"}


@mcp.tool(name="update_email")
def update_email(
    email_id: str,
    account_id: str,
    updates: dict[str, Any] | None = None,
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """Update email properties (isRead, categories, flag, etc.)

    Args:
        email_id: The email ID to update
        account_id: The account ID
        updates: Arbitrary property updates to pass to the Graph API
            (e.g. {"isRead": true, "flag": {"flagStatus": "flagged"}})
        categories: List of category names to assign to the email
            (e.g. ["Blue category", "Red category"]).
            Overrides any 'categories' key in updates if both are provided.
    """
    body: dict[str, Any] = dict(updates) if updates else {}
    if categories is not None:
        body["categories"] = categories

    if not body:
        raise ValueError("Nothing to update: provide updates and/or categories")

    result = graph.request("PATCH", f"/me/messages/{email_id}", account_id, json=body)
    if not result:
        raise ValueError(f"Failed to update email {email_id} - no response")
    return result


@mcp.tool(name="delete_email")
def delete_email(email_id: str, account_id: str) -> dict[str, str]:
    """Delete an email"""
    graph.request("DELETE", f"/me/messages/{email_id}", account_id)
    return {"status": "deleted"}


@mcp.tool(name="move_email")
def move_email(
    email_id: str, destination_folder: str, account_id: str
) -> dict[str, Any]:
    """Move email to another folder.

    Searches top-level folders first, then child folders (one level deep)
    if not found at the top level. This supports subfolders like
    Inbox/Action Required without requiring the caller to specify the path.
    """
    folder_path = FOLDERS.get(destination_folder.casefold(), destination_folder)

    folder_id = _resolve_folder_id(folder_path, account_id)
    if not folder_id:
        raise ValueError(f"Folder '{destination_folder}' not found")

    payload = {"destinationId": folder_id}
    result = graph.request(
        "POST", f"/me/messages/{email_id}/move", account_id, json=payload
    )
    if not result:
        raise ValueError("Failed to move email - no response from server")
    if "id" not in result:
        raise ValueError(f"Failed to move email - unexpected response: {result}")
    return {"status": "moved", "new_id": result["id"]}


@mcp.tool(name="reply_to_email")
def reply_to_email(
    account_id: str,
    email_id: str,
    body: str,
    draft_only: bool = False,
    content_type: str = "HTML",
) -> dict[str, str]:
    """Reply to an email (sender only).

    Args:
        account_id: Microsoft account ID
        email_id: ID of the email to reply to
        body: Reply body text
        draft_only: If True, create a draft reply instead of sending immediately
        content_type: Body format — 'HTML' (default) or 'Text'.
    """
    if draft_only:
        endpoint = f"/me/messages/{email_id}/createReply"
        result = graph.request("POST", endpoint, account_id)
        if result and "id" in result:
            draft_id = result["id"]
            # Read the draft to get the original thread content
            draft = graph.request(
                "GET",
                f"/me/messages/{draft_id}",
                account_id,
                params={"$select": "body"},
            )
            # Prepend new content to the existing thread
            original_body = ""
            if draft and "body" in draft:
                original_body = draft["body"].get("content", "")
            combined = f"{body}<br><br>{original_body}" if original_body else body
            graph.request(
                "PATCH",
                f"/me/messages/{draft_id}",
                account_id,
                json={"body": {"contentType": content_type, "content": combined}},
            )
            return {"status": "draft_created", "draft_id": draft_id}
        raise ValueError("Failed to create reply draft")
    endpoint = f"/me/messages/{email_id}/reply"
    payload = {"message": {"body": {"contentType": content_type, "content": body}}}
    graph.request("POST", endpoint, account_id, json=payload)
    return {"status": "sent"}


@mcp.tool(name="reply_all_email")
def reply_all_email(
    account_id: str,
    email_id: str,
    body: str,
    draft_only: bool = False,
    content_type: str = "HTML",
) -> dict[str, str]:
    """Reply to all recipients of an email.

    Args:
        account_id: Microsoft account ID
        email_id: ID of the email to reply to
        body: Reply body text
        draft_only: If True, create a draft reply instead of sending immediately
        content_type: Body format — 'HTML' (default) or 'Text'.
    """
    if draft_only:
        endpoint = f"/me/messages/{email_id}/createReplyAll"
        result = graph.request("POST", endpoint, account_id)
        if result and "id" in result:
            draft_id = result["id"]
            # Read the draft to get the original thread content
            draft = graph.request(
                "GET",
                f"/me/messages/{draft_id}",
                account_id,
                params={"$select": "body"},
            )
            # Prepend new content to the existing thread
            original_body = ""
            if draft and "body" in draft:
                original_body = draft["body"].get("content", "")
            combined = f"{body}<br><br>{original_body}" if original_body else body
            graph.request(
                "PATCH",
                f"/me/messages/{draft_id}",
                account_id,
                json={"body": {"contentType": content_type, "content": combined}},
            )
            return {"status": "draft_created", "draft_id": draft_id}
        raise ValueError("Failed to create reply-all draft")
    endpoint = f"/me/messages/{email_id}/replyAll"
    payload = {"message": {"body": {"contentType": content_type, "content": body}}}
    graph.request("POST", endpoint, account_id, json=payload)
    return {"status": "sent"}


@mcp.tool(name="forward_email")
def forward_email(
    account_id: str,
    email_id: str,
    to_recipients: list[str],
    comment: str = "",
    draft_only: bool = False,
    content_type: str = "HTML",
) -> dict[str, str]:
    """Forward an email to one or more recipients. Preserves all attachments.

    Args:
        account_id: Microsoft account ID
        email_id: ID of the email to forward
        to_recipients: List of email addresses to forward to
        comment: Optional message to include with the forward
        draft_only: If True, create a draft forward instead of sending immediately
        content_type: Body format for comment — 'HTML' (default) or 'Text'.
    """
    recipients = [{"emailAddress": {"address": addr}} for addr in to_recipients]
    if draft_only:
        endpoint = f"/me/messages/{email_id}/createForward"
        result = graph.request("POST", endpoint, account_id)
        if result and "id" in result:
            draft_id = result["id"]
            patch_payload: dict[str, Any] = {"toRecipients": recipients}
            if comment:
                # Read the draft to preserve the original forwarded content
                draft = graph.request(
                    "GET",
                    f"/me/messages/{draft_id}",
                    account_id,
                    params={"$select": "body"},
                )
                original_body = ""
                if draft and "body" in draft:
                    original_body = draft["body"].get("content", "")
                combined = (
                    f"{comment}<br><br>{original_body}" if original_body else comment
                )
                patch_payload["body"] = {
                    "contentType": content_type,
                    "content": combined,
                }
            graph.request(
                "PATCH",
                f"/me/messages/{draft_id}",
                account_id,
                json=patch_payload,
            )
            return {"status": "draft_created", "draft_id": draft_id}
        raise ValueError("Failed to create forward draft")
    endpoint = f"/me/messages/{email_id}/forward"
    payload: dict[str, Any] = {
        "comment": comment,
        "toRecipients": recipients,
    }
    graph.request("POST", endpoint, account_id, json=payload)
    return {"status": "sent"}


@mcp.tool(name="list_events")
def list_events(
    account_id: str,
    days_ahead: int = 7,
    days_back: int = 0,
    include_details: bool = True,
) -> list[dict[str, Any]]:
    """List calendar events within a date range, including individual instances of recurring events.

    This is the primary tool for finding calendar events. Uses the calendarView
    endpoint which correctly expands recurring series into individual occurrences.

    Args:
        account_id: UUID from list_accounts (not an email address)
        days_ahead: Number of days forward to search (default 7)
        days_back: Number of days backward to search (default 0)
    """
    now = dt.datetime.now(dt.timezone.utc)
    start = (now - dt.timedelta(days=days_back)).isoformat()
    end = (now + dt.timedelta(days=days_ahead)).isoformat()

    params = {
        "startDateTime": start,
        "endDateTime": end,
        "$orderby": "start/dateTime",
        "$top": 100,
    }

    if include_details:
        params["$select"] = (
            "id,subject,start,end,location,body,attendees,organizer,isAllDay,recurrence,onlineMeeting,seriesMasterId"
        )
    else:
        params["$select"] = "id,subject,start,end,location,organizer,seriesMasterId"

    # Use calendarView to get recurring event instances
    events = list(
        graph.request_paginated("/me/calendarView", account_id, params=params)
    )

    return events


@mcp.tool(name="get_event")
def get_event(event_id: str, account_id: str) -> dict[str, Any]:
    """Get full event details"""
    result = graph.request("GET", f"/me/events/{event_id}", account_id)
    if not result:
        raise ValueError(f"Event with ID {event_id} not found")
    return result


@mcp.tool(name="create_event")
def create_event(
    account_id: str,
    subject: str,
    start: str,
    end: str,
    location: str | None = None,
    body: str | None = None,
    attendees: str | list[str] | None = None,
    timezone: str = "UTC",
    is_online_meeting: bool = False,
    online_meeting_provider: str = "teamsForBusiness",
    body_content_type: str = "Text",
) -> dict[str, Any]:
    """Create a calendar event.

    Args:
        account_id: UUID from list_accounts (not an email address)
        timezone: IANA timezone (e.g. 'America/Chicago'). Defaults to UTC.
        is_online_meeting: If True, creates a Teams online meeting link.
        online_meeting_provider: Meeting provider (default: 'teamsForBusiness').
        body_content_type: Body format — 'Text' (default) or 'HTML'.
    """
    event = {
        "subject": subject,
        "start": {"dateTime": start, "timeZone": timezone},
        "end": {"dateTime": end, "timeZone": timezone},
    }

    if location:
        event["location"] = {"displayName": location}

    if body:
        event["body"] = {"contentType": body_content_type, "content": body}

    if attendees:
        attendees_list = [attendees] if isinstance(attendees, str) else attendees
        event["attendees"] = [
            {"emailAddress": {"address": a}, "type": "required"} for a in attendees_list
        ]

    if is_online_meeting:
        event["isOnlineMeeting"] = True
        event["onlineMeetingProvider"] = online_meeting_provider

    result = graph.request("POST", "/me/events", account_id, json=event)
    if not result:
        raise ValueError("Failed to create event")
    return result


@mcp.tool(name="update_event")
def update_event(
    event_id: str,
    updates: dict[str, Any],
    account_id: str,
    body_content_type: str = "Text",
) -> dict[str, Any]:
    """Update event properties.

    Args:
        body_content_type: Body format — 'Text' (default) or 'HTML'.
    """
    formatted_updates = {}

    if "subject" in updates:
        formatted_updates["subject"] = updates["subject"]
    if "start" in updates:
        formatted_updates["start"] = {
            "dateTime": updates["start"],
            "timeZone": updates.get("timezone", "UTC"),
        }
    if "end" in updates:
        formatted_updates["end"] = {
            "dateTime": updates["end"],
            "timeZone": updates.get("timezone", "UTC"),
        }
    if "location" in updates:
        formatted_updates["location"] = {"displayName": updates["location"]}
    if "body" in updates:
        formatted_updates["body"] = {
            "contentType": body_content_type,
            "content": updates["body"],
        }

    result = graph.request(
        "PATCH", f"/me/events/{event_id}", account_id, json=formatted_updates
    )
    return result or {"status": "updated"}


@mcp.tool(name="delete_event")
def delete_event(
    account_id: str, event_id: str, send_cancellation: bool = True
) -> dict[str, str]:
    """Delete or cancel a calendar event"""
    if send_cancellation:
        graph.request("POST", f"/me/events/{event_id}/cancel", account_id, json={})
    else:
        graph.request("DELETE", f"/me/events/{event_id}", account_id)
    return {"status": "deleted"}


@mcp.tool(name="respond_event")
def respond_event(
    account_id: str,
    event_id: str,
    response: str = "accept",
    message: str | None = None,
) -> dict[str, str]:
    """Respond to event invitation (accept, decline, tentativelyAccept)"""
    payload: dict[str, Any] = {"sendResponse": True}
    if message:
        payload["comment"] = message

    graph.request("POST", f"/me/events/{event_id}/{response}", account_id, json=payload)
    return {"status": response}


@mcp.tool(name="check_availability")
def check_availability(
    account_id: str,
    start: str,
    end: str,
    attendees: str | list[str] | None = None,
) -> dict[str, Any]:
    """Check calendar availability for scheduling"""
    from microsoft_mcp.auth import get_account_email

    schedules = [get_account_email(account_id)]
    if attendees:
        attendees_list = [attendees] if isinstance(attendees, str) else attendees
        schedules.extend(attendees_list)

    payload = {
        "schedules": schedules,
        "startTime": {"dateTime": start, "timeZone": "UTC"},
        "endTime": {"dateTime": end, "timeZone": "UTC"},
        "availabilityViewInterval": 30,
    }

    result = graph.request("POST", "/me/calendar/getSchedule", account_id, json=payload)
    if not result:
        raise ValueError("Failed to check availability")
    return result


@mcp.tool(name="find_meeting_times")
def find_meeting_times(
    account_id: str,
    attendees: str | list[str],
    duration_minutes: int = 30,
    start: str | None = None,
    end: str | None = None,
    max_candidates: int = 5,
    min_attendee_percentage: float = 100,
    timezone: str = "Central Standard Time",
) -> dict[str, Any]:
    """Find available meeting times for a group of attendees.

    Uses Microsoft Graph's findMeetingTimes to suggest optimal slots
    based on all attendees' calendar availability. Returns compact
    ranked suggestions instead of raw schedule data.

    Args:
        account_id: Microsoft account ID
        attendees: Email address(es) of attendees
        duration_minutes: Meeting duration in minutes (default 30)
        start: Start of search window (ISO datetime, default: now)
        end: End of search window (ISO datetime, default: 2 weeks from now)
        max_candidates: Maximum number of suggestions to return (default 5)
        min_attendee_percentage: Minimum % of attendees that must be available (default 100)
        timezone: Timezone for the search window (default Central Standard Time)
    """
    attendees_list = [attendees] if isinstance(attendees, str) else attendees

    payload: dict[str, Any] = {
        "attendees": [
            {
                "emailAddress": {"address": email},
                "type": "required",
            }
            for email in attendees_list
        ],
        "meetingDuration": f"PT{duration_minutes}M",
        "maxCandidates": max_candidates,
        "minimumAttendeePercentage": min_attendee_percentage,
        "isOrganizerOptional": False,
        "returnSuggestionReasons": True,
    }

    if start or end:
        timeslot: dict[str, Any] = {}
        if start:
            timeslot["start"] = {"dateTime": start, "timeZone": timezone}
        if end:
            timeslot["end"] = {"dateTime": end, "timeZone": timezone}
        payload["timeConstraint"] = {"timeslots": [timeslot]}

    result = graph.request("POST", "/me/findMeetingTimes", account_id, json=payload)
    if not result:
        raise ValueError("Failed to find meeting times")
    return result


# --- Online Meetings ---


@mcp.tool(name="list_online_meetings")
def list_online_meetings(
    account_id: str,
    filter_join_url: str | None = None,
    filter_join_meeting_id: str | None = None,
) -> list[dict[str, Any]]:
    """Find a Teams online meeting by join URL or meeting ID.

    The Graph API requires one of the filter parameters — bare listing
    is not supported. Use filter_join_url (from a calendar event's
    onlineMeeting.joinUrl) or filter_join_meeting_id.

    Args:
        account_id: UUID from list_accounts (not an email address)
        filter_join_url: Filter by exact join URL (from calendar event's onlineMeeting.joinUrl)
        filter_join_meeting_id: Filter by join meeting ID (the numeric meeting ID)
    """
    if not filter_join_url and not filter_join_meeting_id:
        raise ValueError(
            "Either filter_join_url or filter_join_meeting_id is required. "
            "The Graph API does not support listing all online meetings."
        )

    if filter_join_url:
        # Embed filter directly in path to prevent httpx from double-encoding
        # the percent-encoded characters in the Teams join URL
        path = f"/me/onlineMeetings?$filter=JoinWebUrl eq '{filter_join_url}'"
        result = graph.request("GET", path, account_id)
    else:
        params = {
            "$filter": f"joinMeetingIdSettings/joinMeetingId eq '{filter_join_meeting_id}'"
        }
        result = graph.request("GET", "/me/onlineMeetings", account_id, params=params)

    if not result:
        return []
    return result.get("value", [])


@mcp.tool(name="get_online_meeting")
def get_online_meeting(meeting_id: str, account_id: str) -> dict[str, Any]:
    """Get full details of a Teams online meeting.

    Args:
        meeting_id: The online meeting ID
        account_id: UUID from list_accounts (not an email address)
    """
    result = graph.request("GET", f"/me/onlineMeetings/{meeting_id}", account_id)
    if not result:
        raise ValueError(f"Online meeting with ID {meeting_id} not found")
    return result


# --- Transcripts ---


@mcp.tool(name="list_transcripts")
def list_transcripts(meeting_id: str, account_id: str) -> list[dict[str, Any]]:
    """List transcripts for a Teams online meeting.

    Args:
        meeting_id: The online meeting ID (from list_online_meetings or get_online_meeting)
        account_id: UUID from list_accounts (not an email address)

    Returns:
        List of transcript metadata objects (id, createdDateTime, contentCorrelationId).
        Returns empty list if no transcripts exist or transcription was not enabled.
    """
    result = graph.request(
        "GET", f"/me/onlineMeetings/{meeting_id}/transcripts", account_id
    )
    if not result:
        return []
    return result.get("value", [])


@mcp.tool(name="get_transcript_content")
def get_transcript_content(
    meeting_id: str,
    transcript_id: str,
    account_id: str,
    content_format: str = "text/vtt",
    max_length: int | None = None,
) -> str:
    """Get the text content of a meeting transcript.

    Args:
        meeting_id: The online meeting ID
        transcript_id: The transcript ID (from list_transcripts)
        account_id: UUID from list_accounts (not an email address)
        content_format: Content format — "text/vtt" (with timestamps) or "text/plain" (plain text).
            Defaults to "text/vtt".
        max_length: Maximum character length to return. None means no truncation (default).
    """
    content = graph.request_text(
        f"/me/onlineMeetings/{meeting_id}/transcripts/{transcript_id}/content",
        account_id,
        accept=content_format,
    )

    if max_length and len(content) > max_length:
        content = content[
            :max_length
        ] + "\n\n[Content truncated at {} characters]".format(max_length)

    return content


# --- AI Insights ---


def _user_id_from_account(account_id: str) -> str:
    """Extract Azure AD object ID from MSAL home_account_id.

    MSAL home_account_id format is '{oid}.{tid}' where oid is the user's
    Azure AD object ID and tid is the tenant ID.
    """
    if "." not in account_id:
        raise ValueError(
            f"Invalid account_id format: expected '{{oid}}.{{tid}}', got '{account_id}'"
        )
    return account_id.split(".")[0]


@mcp.tool(name="list_ai_insights")
def list_ai_insights(meeting_id: str, account_id: str) -> list[dict[str, Any]]:
    """List Copilot AI-generated insights for a Teams online meeting.

    Returns insight metadata (id, createdDateTime, etc.). Use get_ai_insight
    to retrieve full content including meeting notes and action items.

    Requires Microsoft 365 Copilot or Teams Premium license.

    Args:
        meeting_id: The online meeting ID (from list_online_meetings or get_online_meeting)
        account_id: UUID from list_accounts (not an email address)
    """
    user_id = _user_id_from_account(account_id)
    result = graph.request(
        "GET",
        f"/copilot/users/{user_id}/onlineMeetings/{meeting_id}/aiInsights",
        account_id,
    )
    if not result:
        return []
    return result.get("value", [])


@mcp.tool(name="get_ai_insight")
def get_ai_insight(meeting_id: str, insight_id: str, account_id: str) -> dict[str, Any]:
    """Get full Copilot AI insight for a meeting, including notes and action items.

    Returns the complete insight with meetingNotes (title, text, subpoints),
    actionItems (title, text, ownerDisplayName), and viewpoint.mentionEvents
    (speaker, timestamp, utterance).

    Requires Microsoft 365 Copilot or Teams Premium license.

    Args:
        meeting_id: The online meeting ID
        insight_id: The AI insight ID (from list_ai_insights)
        account_id: UUID from list_accounts (not an email address)
    """
    user_id = _user_id_from_account(account_id)
    result = graph.request(
        "GET",
        f"/copilot/users/{user_id}/onlineMeetings/{meeting_id}/aiInsights/{insight_id}",
        account_id,
    )
    if not result:
        raise ValueError(f"AI insight with ID {insight_id} not found")
    return result


@mcp.tool(name="list_contacts")
def list_contacts(account_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """List contacts"""
    params = {"$top": min(limit, 100)}

    contacts = list(
        graph.request_paginated("/me/contacts", account_id, params=params, limit=limit)
    )

    return contacts


@mcp.tool(name="get_contact")
def get_contact(contact_id: str, account_id: str) -> dict[str, Any]:
    """Get contact details"""
    result = graph.request("GET", f"/me/contacts/{contact_id}", account_id)
    if not result:
        raise ValueError(f"Contact with ID {contact_id} not found")
    return result


@mcp.tool(name="create_contact")
def create_contact(
    account_id: str,
    given_name: str,
    surname: str | None = None,
    email_addresses: str | list[str] | None = None,
    phone_numbers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a new contact"""
    contact: dict[str, Any] = {"givenName": given_name}

    if surname:
        contact["surname"] = surname

    if email_addresses:
        email_list = (
            [email_addresses] if isinstance(email_addresses, str) else email_addresses
        )
        contact["emailAddresses"] = [
            {"address": email, "name": f"{given_name} {surname or ''}".strip()}
            for email in email_list
        ]

    if phone_numbers:
        if "business" in phone_numbers:
            contact["businessPhones"] = [phone_numbers["business"]]
        if "home" in phone_numbers:
            contact["homePhones"] = [phone_numbers["home"]]
        if "mobile" in phone_numbers:
            contact["mobilePhone"] = phone_numbers["mobile"]

    result = graph.request("POST", "/me/contacts", account_id, json=contact)
    if not result:
        raise ValueError("Failed to create contact")
    return result


@mcp.tool(name="update_contact")
def update_contact(
    contact_id: str, updates: dict[str, Any], account_id: str
) -> dict[str, Any]:
    """Update contact information"""
    result = graph.request(
        "PATCH", f"/me/contacts/{contact_id}", account_id, json=updates
    )
    return result or {"status": "updated"}


@mcp.tool(name="delete_contact")
def delete_contact(contact_id: str, account_id: str) -> dict[str, str]:
    """Delete a contact"""
    graph.request("DELETE", f"/me/contacts/{contact_id}", account_id)
    return {"status": "deleted"}


@mcp.tool(name="list_files")
def list_files(
    account_id: str, path: str = "/", limit: int = 50
) -> list[dict[str, Any]]:
    """List files and folders in OneDrive"""
    endpoint = (
        "/me/drive/root/children"
        if path == "/"
        else f"/me/drive/root:/{path}:/children"
    )
    params = {
        "$top": min(limit, 100),
        "$select": "id,name,size,lastModifiedDateTime,folder,file,@microsoft.graph.downloadUrl",
    }

    items = list(
        graph.request_paginated(endpoint, account_id, params=params, limit=limit)
    )

    return [
        {
            "id": item["id"],
            "name": item["name"],
            "type": "folder" if "folder" in item else "file",
            "size": item.get("size", 0),
            "modified": item.get("lastModifiedDateTime"),
            "download_url": item.get("@microsoft.graph.downloadUrl"),
        }
        for item in items
    ]


@mcp.tool(name="get_file")
def get_file(file_id: str, account_id: str, download_path: str) -> dict[str, Any]:
    """Download a file from OneDrive to local path"""
    metadata = graph.request("GET", f"/me/drive/items/{file_id}", account_id)
    if not metadata:
        raise ValueError(f"File with ID {file_id} not found")

    download_url = metadata.get("@microsoft.graph.downloadUrl")
    if not download_url:
        raise ValueError("No download URL available for this file")

    # Stream to disk so large attachments don't pull into memory. We use a
    # fresh httpx.stream rather than the module-level client in graph.py
    # because its 30s default is too tight for big OneDrive downloads, and
    # the download URL is pre-authenticated (no need for Graph auth headers).
    # pa-r9ej: this previously shelled out to curl, which broke when the
    # container image lacked curl (raised FileNotFoundError uncaught).
    with httpx.stream(
        "GET", download_url, follow_redirects=True, timeout=60.0
    ) as response:
        response.raise_for_status()
        with open(download_path, "wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)

    return {
        "path": download_path,
        "name": metadata.get("name", "unknown"),
        "size_mb": round(metadata.get("size", 0) / (1024 * 1024), 2),
        "mime_type": metadata.get("file", {}).get("mimeType") if metadata else None,
    }


@mcp.tool(name="create_file")
def create_file(
    onedrive_path: str, local_file_path: str, account_id: str
) -> dict[str, Any]:
    """Upload a local file to OneDrive"""
    path = pl.Path(local_file_path).expanduser().resolve()
    data = path.read_bytes()
    result = graph.upload_large_file(
        f"/me/drive/root:/{onedrive_path}:", data, account_id
    )
    if not result:
        raise ValueError(f"Failed to create file at path: {onedrive_path}")
    return result


@mcp.tool(name="update_file")
def update_file(file_id: str, local_file_path: str, account_id: str) -> dict[str, Any]:
    """Update OneDrive file content from a local file"""
    path = pl.Path(local_file_path).expanduser().resolve()
    data = path.read_bytes()
    result = graph.upload_large_file(f"/me/drive/items/{file_id}", data, account_id)
    if not result:
        raise ValueError(f"Failed to update file with ID: {file_id}")
    return result


@mcp.tool(name="delete_file")
def delete_file(file_id: str, account_id: str) -> dict[str, str]:
    """Delete a file or folder"""
    graph.request("DELETE", f"/me/drive/items/{file_id}", account_id)
    return {"status": "deleted"}


def _extract_office_xml_text(file_bytes: bytes, mime_type: str) -> str | None:
    """Extract text from Office XML formats (docx/xlsx/pptx) using stdlib only."""
    import io
    import xml.etree.ElementTree as ET
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            if "wordprocessingml" in mime_type:
                ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                doc_xml = zf.read("word/document.xml")
                root = ET.fromstring(doc_xml)
                texts = [t.text for t in root.iter(f"{{{ns}}}t") if t.text]
                return " ".join(texts) if texts else None
            elif "spreadsheetml" in mime_type:
                ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                try:
                    ss_xml = zf.read("xl/sharedStrings.xml")
                    root = ET.fromstring(ss_xml)
                    texts = []
                    for si in root.findall(f"{{{ns}}}si"):
                        parts = [t.text for t in si.iter(f"{{{ns}}}t") if t.text]
                        texts.append("".join(parts))
                    return "\n".join(texts) if texts else None
                except KeyError:
                    return None
            elif "presentationml" in mime_type:
                ns_a = "http://schemas.openxmlformats.org/drawingml/2006/main"
                texts = []
                for name in sorted(zf.namelist()):
                    if name.startswith("ppt/slides/slide") and name.endswith(".xml"):
                        slide_xml = zf.read(name)
                        root = ET.fromstring(slide_xml)
                        slide_texts = [
                            t.text for t in root.iter(f"{{{ns_a}}}t") if t.text
                        ]
                        if slide_texts:
                            texts.append(" ".join(slide_texts))
                return "\n\n".join(texts) if texts else None
    except (zipfile.BadZipFile, ET.ParseError, KeyError):
        return None
    return None


def _extract_text_content(content_bytes: bytes, content_type: str) -> str | None:
    """Extract readable text from attachment bytes when possible.

    Returns extracted text for text/* and Office XML formats, None for binary.
    """
    try:
        ct = content_type.lower()
        if ct.startswith("text/"):
            return content_bytes.decode("utf-8", errors="replace")

        office_types = {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
        if ct in office_types:
            return _extract_office_xml_text(content_bytes, ct)

        if ct == "application/pdf":
            return _extract_pdf_text(content_bytes)
    except Exception:
        pass
    return None


def _extract_pdf_text(content_bytes: bytes) -> str | None:
    """Extract text from PDF using pdftotext (poppler-utils) with layout preservation."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
        tmp.write(content_bytes)
        tmp.flush()
        result = subprocess.run(
            ["pdftotext", "-layout", tmp.name, "-"],
            capture_output=True,
            timeout=60,
        )
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace")
    return None


_MAX_INLINE_CHARS = 50_000


@mcp.tool(name="get_attachment")
def get_attachment(
    email_id: str,
    attachment_id: str,
    account_id: str,
    save_path: str | None = None,
) -> dict[str, Any]:
    """Download an email attachment and return its content.

    By default, returns the raw bytes base64-encoded in ``content_bytes_b64``
    so callers in separate processes/containers can decode and use the file
    themselves. Pass ``save_path`` to also write the decoded bytes to a local
    file on the server; when set, the resolved path is echoed back as
    ``saved_to``.

    For text-readable formats (text/*, docx, xlsx, pptx, pdf), extracted text
    is also returned in a ``content`` key (truncated to the inline limit).
    Binary files omit the ``content`` key.
    """
    result = graph.request(
        "GET", f"/me/messages/{email_id}/attachments/{attachment_id}", account_id
    )

    if not result:
        raise ValueError("Attachment not found")

    if "contentBytes" not in result:
        raise ValueError("Attachment content not available")

    content_b64 = result["contentBytes"]
    content_bytes = base64.b64decode(content_b64)
    content_type = result.get("contentType", "application/octet-stream")

    response: dict[str, Any] = {
        "name": result.get("name", "unknown"),
        "content_type": content_type,
        "size": result.get("size", 0),
        "content_bytes_b64": content_b64,
    }

    if save_path is not None:
        path = pl.Path(save_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content_bytes)
        response["saved_to"] = str(path)

    extracted = _extract_text_content(content_bytes, content_type)
    if extracted is not None:
        if len(extracted) > _MAX_INLINE_CHARS:
            extracted = (
                extracted[:_MAX_INLINE_CHARS]
                + f"\n\n[Content truncated — showing first {_MAX_INLINE_CHARS}"
                f" of {len(extracted)} characters]"
            )
        response["content"] = extracted

    return response


@mcp.tool(name="search_files")
def search_files(
    query: str,
    account_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Search for files in OneDrive using the modern search API."""
    items = list(graph.search_query(query, ["driveItem"], account_id, limit))

    return [
        {
            "id": item["id"],
            "name": item["name"],
            "type": "folder" if "folder" in item else "file",
            "size": item.get("size", 0),
            "modified": item.get("lastModifiedDateTime"),
            "download_url": item.get("@microsoft.graph.downloadUrl"),
        }
        for item in items
    ]


@mcp.tool(name="search_emails")
def search_emails(
    query: str,
    account_id: str,
    limit: int = 50,
    folder: str | None = None,
) -> list[dict[str, Any]]:
    """Search emails using the modern search API."""
    if folder:
        # For folder-specific search, use the traditional endpoint
        folder_path = FOLDERS.get(folder.casefold(), folder)
        endpoint = f"/me/mailFolders/{folder_path}/messages"

        params = {
            "$search": f'"{_kql_escape_quotes(query)}"',
            "$top": min(limit, 100),
            "$select": "id,subject,from,toRecipients,receivedDateTime,hasAttachments,body,conversationId,isRead,internetMessageId",
        }

        results = list(
            graph.request_paginated(endpoint, account_id, params=params, limit=limit)
        )
        for msg in results:
            address_resolution.resolve_x500_in_message(msg, account_id)
        return results

    results = list(graph.search_query(query, ["message"], account_id, limit))
    for msg in results:
        address_resolution.resolve_x500_in_message(msg, account_id)
    return results


def _kql_escape_quotes(value: str) -> str:
    """Escape double quotes for inclusion in a KQL phrase literal.

    Microsoft Graph $search uses KQL syntax where phrases are quoted strings.
    A literal double-quote inside a phrase must be backslash-escaped.
    """
    return value.replace('"', '\\"')


@mcp.tool(name="search_contacts")
def search_contacts(
    query: str,
    account_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Search the user's PERSONAL address book (/me/contacts) only.

    For the organization directory (CB-internal employees, GAL contacts),
    use search_directory instead. Uses traditional search since
    unified_search doesn't support contacts.
    """
    params = {
        "$search": f'"{_kql_escape_quotes(query)}"',
        "$top": min(limit, 100),
    }

    contacts = list(
        graph.request_paginated("/me/contacts", account_id, params=params, limit=limit)
    )

    return contacts


def _normalize_people_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a /me/people response row to the search_directory shape."""
    scored = row.get("scoredEmailAddresses") or []
    email = scored[0].get("address") if scored else None
    person_type_obj = row.get("personType") or {}
    return {
        "name": row.get("displayName"),
        "email": email,
        "job_title": row.get("jobTitle"),
        "department": row.get("department"),
        "person_type": person_type_obj.get("subclass"),
        "source": "people",
    }


def _normalize_users_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a /users response row to the search_directory shape."""
    return {
        "name": row.get("displayName"),
        "email": row.get("mail") or row.get("userPrincipalName"),
        "job_title": row.get("jobTitle"),
        "department": row.get("department"),
        "person_type": None,
        "source": "users",
    }


@mcp.tool(name="search_directory")
def search_directory(
    query: str,
    account_id: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Search the organization directory for people by name or email.

    Returns relevance-ranked correspondents first (via /me/people), falling
    back to full directory search (via /users) for non-correspondents.

    Distinct from search_contacts, which only searches the user's personal
    address book (/me/contacts).
    """
    people_response = graph.request(
        "GET",
        "/me/people",
        account_id,
        params={
            "$search": f'"{_kql_escape_quotes(query)}"',
            "$top": min(limit, 100),
            "$select": "displayName,scoredEmailAddresses,jobTitle,department,personType",
        },
    )
    people_rows = (people_response or {}).get("value", [])
    if people_rows:
        return [_normalize_people_row(r) for r in people_rows]

    users_response = graph.request(
        "GET",
        "/users",
        account_id,
        params={
            "$search": f'"displayName:{_kql_escape_quotes(query)}" OR "mail:{_kql_escape_quotes(query)}"',
            "$top": min(limit, 100),
            "$select": "id,displayName,mail,userPrincipalName,jobTitle,department",
        },
    )
    users_rows = (users_response or {}).get("value", [])
    return [_normalize_users_row(r) for r in users_rows]


@mcp.tool(name="unified_search")
def unified_search(
    query: str,
    account_id: str,
    entity_types: list[str] | None = None,
    limit: int = 50,
) -> dict[str, list[dict[str, Any]]]:
    """Search across multiple Microsoft 365 resources using the modern search API

    entity_types can include: 'message', 'drive', 'driveItem', 'list', 'listItem', 'site'
    If not specified, searches across messages and files. Use list_events to find calendar events.
    """
    if not entity_types:
        entity_types = ["message", "driveItem"]

    results = {entity_type: [] for entity_type in entity_types}

    items = list(graph.search_query(query, entity_types, account_id, limit))

    for item in items:
        resource_type = item.get("@odata.type", "").split(".")[-1]

        if resource_type == "message":
            results.setdefault("message", []).append(item)
        elif resource_type == "event":
            results.setdefault("event", []).append(item)
        elif resource_type in ["driveItem", "file", "folder"]:
            results.setdefault("driveItem", []).append(item)
        else:
            results.setdefault("other", []).append(item)

    return {k: v for k, v in results.items() if v}
