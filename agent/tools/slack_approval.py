"""
Slack approval flow for kubectl_patch remediations.

Design: emoji-reaction polling (not interactive buttons)
--------------------------------------------------------
Slack's interactive components (buttons that call a URL on click) require a
publicly reachable callback endpoint and Slack signing-secret validation. For
this demo we use emoji reactions instead:

  1. The agent posts a Block Kit message to #incidents describing the fix.
  2. A human reviews the message and adds:
       ✅  (:white_check_mark:) to APPROVE
       ❌  (:x:)               to REJECT
  3. The agent polls reactions.get every POLL_INTERVAL_SECONDS.
  4. First matching reaction wins. If neither appears within timeout → "timeout".

Trade-offs vs production:
  - No real-time response — polling adds up to POLL_INTERVAL_SECONDS of latency.
  - The bot needs reactions:read scope.
  - A human could add both reactions; first-match-wins logic resolves it.

Production replacement: configure a Slack app with an Interactivity Request URL,
handle button_action payloads, validate with Slack signing secret. Replace
wait_for_approval with a FastAPI endpoint that writes to a shared approval store.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

import structlog

if TYPE_CHECKING:
    from agent.state import ProposedFix

log = structlog.get_logger()

POLL_INTERVAL_SECONDS = 5
APPROVE_EMOJI = "white_check_mark"
REJECT_EMOJI = "x"

ApprovalOutcome = Literal["approved", "rejected", "timeout"]


def post_approval_request(
    client: object,
    channel: str,
    fix: ProposedFix,
    rca: str,
) -> str:
    """Post a Block Kit approval request to Slack and return the message timestamp.

    The message includes:
    - Header: alert name and fix type
    - Fields: namespace, target resource, fix type
    - Code block: the exact command or diff
    - Instructions: which emoji reactions to add

    Parameters
    ----------
    client  : slack_sdk.WebClient
    channel : str   — channel ID or name (e.g. "#incidents")
    fix     : ProposedFix
    rca     : str   — current RCA / rationale text (may be partial at call time)

    Returns
    -------
    str  — Slack message timestamp (ts), used as the polling key
    """
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f":robot_face: KubeSentinel — Approval Required: {fix.type}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Namespace:*\n`{fix.namespace}`"},
                {"type": "mrkdwn", "text": f"*Target:*\n`{fix.target}`"},
                {"type": "mrkdwn", "text": f"*Fix type:*\n`{fix.type}`"},
                {"type": "mrkdwn", "text": f"*Description:*\n{fix.description[:200]}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Proposed command / diff:*\n```{fix.command_or_diff[:1000]}```",
            },
        },
    ]

    if rca:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Diagnosis / RCA:*\n{rca[:500]}",
                },
            }
        )

    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    ":white_check_mark: React with ✅ to *APPROVE*\n"
                    ":x: React with ❌ to *REJECT*\n"
                    "_Approval window: 5 minutes_"
                ),
            },
        }
    )

    fallback_text = (
        f"KubeSentinel approval required: {fix.type} on {fix.namespace}/{fix.target}. "
        "React ✅ to approve or ❌ to reject."
    )

    resp = client.chat_postMessage(  # type: ignore[union-attr]
        channel=channel,
        text=fallback_text,
        blocks=blocks,
        mrkdwn=True,
    )
    message_ts: str = resp["ts"]
    log.info(
        "slack_approval.posted",
        channel=channel,
        message_ts=message_ts,
        fix_type=fix.type,
        target=fix.target,
    )
    return message_ts


def wait_for_approval(
    client: object,
    channel: str,
    message_ts: str,
    timeout_seconds: int = 300,
) -> ApprovalOutcome:
    """Poll for emoji reactions on a Slack message until approved, rejected, or timeout.

    Polls reactions.get every POLL_INTERVAL_SECONDS. Returns as soon as either
    APPROVE_EMOJI or REJECT_EMOJI is present. If neither appears within
    timeout_seconds, returns "timeout".

    Parameters
    ----------
    client          : slack_sdk.WebClient
    channel         : str — channel where the approval message was posted
    message_ts      : str — timestamp returned by post_approval_request
    timeout_seconds : int — maximum wait time (default 300 = 5 minutes)

    Returns
    -------
    "approved" | "rejected" | "timeout"
    """
    deadline = time.monotonic() + timeout_seconds
    elapsed_polls = 0

    while time.monotonic() < deadline:
        try:
            resp = client.reactions_get(  # type: ignore[union-attr]
                channel=channel,
                timestamp=message_ts,
            )
            reactions: list[dict] = (
                resp.get("message", {}).get("reactions", [])
                if isinstance(resp, dict)
                else resp["message"].get("reactions", [])
            )
            names = {r["name"] for r in reactions}

            if APPROVE_EMOJI in names:
                log.info(
                    "slack_approval.approved",
                    channel=channel,
                    message_ts=message_ts,
                    polls=elapsed_polls,
                )
                return "approved"

            if REJECT_EMOJI in names:
                log.info(
                    "slack_approval.rejected",
                    channel=channel,
                    message_ts=message_ts,
                    polls=elapsed_polls,
                )
                return "rejected"

        except Exception as exc:
            log.warning(
                "slack_approval.poll_error",
                error=str(exc),
                polls=elapsed_polls,
            )

        elapsed_polls += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(POLL_INTERVAL_SECONDS, remaining))

    log.warning(
        "slack_approval.timeout",
        channel=channel,
        message_ts=message_ts,
        timeout_seconds=timeout_seconds,
        polls=elapsed_polls,
    )
    return "timeout"
