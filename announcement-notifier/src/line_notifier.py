"""LINE Messaging API notifier.

LINE Notify was discontinued by LINE on 2025-03-31, so this uses the
Messaging API instead: a LINE Official Account (created for free via the
LINE Developers Console) pushes messages to one or more user/group IDs
that have added the bot as a friend.

Environment variables expected:
  LINE_CHANNEL_ACCESS_TOKEN   long-lived channel access token
  LINE_TARGET_IDS             comma-separated user/group/room IDs to push to

All methods are no-ops (log only) when credentials are missing, so the
system degrades gracefully during development / testing.
"""
import logging
import os
from typing import Dict, List, Optional

import requests

log = logging.getLogger(__name__)

PUSH_URL = "https://api.line.me/v2/bot/message/push"
MULTICAST_URL = "https://api.line.me/v2/bot/message/multicast"
_MAX_TEXT_LEN = 5000  # LINE text message length limit


class LineNotifier:
    def __init__(
        self,
        channel_access_token: Optional[str] = None,
        target_ids: Optional[List[str]] = None,
    ):
        self.token = channel_access_token or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        if target_ids is not None:
            self.target_ids = target_ids
        else:
            raw = os.environ.get("LINE_TARGET_IDS", "")
            self.target_ids = [t.strip() for t in raw.split(",") if t.strip()]
        self._sent: List[Dict] = []  # audit log for testing

    def send_text(self, text: str) -> bool:
        if not self.token or not self.target_ids:
            log.warning("LINE credentials/target not set — message not sent")
            self._sent.append({"text": text, "sent": False, "reason": "no_credentials"})
            return False

        text = text[:_MAX_TEXT_LEN]
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if len(self.target_ids) > 1:
            url = MULTICAST_URL
            payload = {"to": self.target_ids, "messages": [{"type": "text", "text": text}]}
        else:
            url = PUSH_URL
            payload = {"to": self.target_ids[0], "messages": [{"type": "text", "text": text}]}

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            resp.raise_for_status()
            log.info("LINE message sent to %d target(s)", len(self.target_ids))
            self._sent.append({"text": text, "sent": True})
            return True
        except requests.RequestException as exc:
            log.error("Failed to send LINE message: %s", exc)
            self._sent.append({"text": text, "sent": False, "error": str(exc)})
            return False

    # ── test helpers ──────────────────────────────────────────────────────────

    def sent_messages(self) -> List[Dict]:
        return list(self._sent)
