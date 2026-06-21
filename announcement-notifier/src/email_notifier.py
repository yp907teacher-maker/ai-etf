"""Email notifier — sends announcement alerts via SMTP.

Environment variables expected:
  SMTP_HOST          (default: smtp.gmail.com)
  SMTP_PORT          (default: 587)
  SMTP_USER          sender email address
  SMTP_PASS          app password / API key
  ANNOUNCE_EMAIL_TO  comma-separated recipient list (defaults to SMTP_USER)

All methods are no-ops (log only) when credentials are missing, so the
system degrades gracefully during development / testing.
"""
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


class EmailNotifier:
    def __init__(
        self,
        smtp_host: Optional[str] = None,
        smtp_port: Optional[int] = None,
        smtp_user: Optional[str] = None,
        smtp_pass: Optional[str] = None,
        to_addrs: Optional[List[str]] = None,
    ):
        self.smtp_host = smtp_host or os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(smtp_port or os.environ.get("SMTP_PORT", 587))
        self.smtp_user = smtp_user or os.environ.get("SMTP_USER", "")
        self.smtp_pass = smtp_pass or os.environ.get("SMTP_PASS", "")
        if to_addrs is not None:
            self.to_addrs = to_addrs
        else:
            raw = os.environ.get("ANNOUNCE_EMAIL_TO", "")
            self.to_addrs = [a.strip() for a in raw.split(",") if a.strip()]
            if not self.to_addrs and self.smtp_user:
                self.to_addrs = [self.smtp_user]
        self._sent: List[Dict] = []  # audit log for testing

    def send(self, subject: str, body: str) -> bool:
        if not self.smtp_user or not self.smtp_pass or not self.to_addrs:
            log.warning("SMTP credentials/recipients not set — email not sent: %s", subject)
            self._sent.append({"subject": subject, "sent": False, "reason": "no_credentials"})
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.smtp_user
        msg["To"] = ", ".join(self.to_addrs)
        msg.attach(MIMEText(body, "plain", "utf-8"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(self.smtp_user, self.smtp_pass)
                smtp.sendmail(self.smtp_user, self.to_addrs, msg.as_string())
            log.info("Email sent: %s → %s", subject, self.to_addrs)
            self._sent.append({"subject": subject, "sent": True})
            return True
        except Exception as exc:
            log.error("Failed to send email '%s': %s", subject, exc)
            self._sent.append({"subject": subject, "sent": False, "error": str(exc)})
            return False

    # ── test helpers ──────────────────────────────────────────────────────────

    def sent_subjects(self) -> List[str]:
        return [e["subject"] for e in self._sent]
