"""list_emails' thin $select carries ccRecipients and bodyPreview (pa-rrue.14).

Janet's morning brief lists Tom's Sent Items with their To and Cc, and shows the
opening words of his last reply, read with include_body=False. Graph message resource: bodyPreview is
"The first 255 characters of the message body. It is in text format."
https://learn.microsoft.com/en-us/graph/api/resources/message?view=graph-rest-1.0

Asserts on the argument handed to graph.request_paginated, because a mocked
response would contain whatever fields the fixture says regardless of what was
selected.
"""

from unittest.mock import patch

import pytest

from microsoft_mcp.tools import list_emails

PRIOR_THIN = {
    "id",
    "subject",
    "from",
    "toRecipients",
    "receivedDateTime",
    "hasAttachments",
    "conversationId",
    "isRead",
}
PRIOR_FULL = PRIOR_THIN | {"ccRecipients", "body"}


def _selected(include_body: bool) -> set[str]:
    with patch(
        "microsoft_mcp.tools.graph.request_paginated", return_value=iter([])
    ) as mock_paginated:
        list_emails(
            account_id="acct-1", folder="sent", limit=5, include_body=include_body
        )
    mock_paginated.assert_called_once()
    args, kwargs = mock_paginated.call_args
    assert args[0] == "/me/mailFolders/sentitems/messages"
    return set(kwargs["params"]["$select"].split(","))


def test_the_thin_select_carries_body_preview_and_cc():
    selected = _selected(False)
    assert "bodyPreview" in selected
    assert "ccRecipients" in selected


@pytest.mark.parametrize(
    ("include_body", "prior"), [(False, PRIOR_THIN), (True, PRIOR_FULL)]
)
def test_both_selects_keep_their_prior_fields(include_body, prior):
    missing = prior - _selected(include_body)
    assert not missing, f"include_body={include_body} dropped {missing}"
