from unittest.mock import MagicMock, patch

from src.email_notifier import EmailNotifier


class TestEmailNotifier:
    def test_missing_credentials_is_noop(self):
        notifier = EmailNotifier(smtp_user="", smtp_pass="", to_addrs=["a@example.com"])
        assert notifier.send("subject", "body") is False

    def test_missing_recipients_is_noop(self):
        notifier = EmailNotifier(smtp_user="me@example.com", smtp_pass="pw", to_addrs=[])
        assert notifier.send("subject", "body") is False

    def test_defaults_recipient_to_smtp_user(self):
        notifier = EmailNotifier(smtp_user="me@example.com", smtp_pass="pw")
        assert notifier.to_addrs == ["me@example.com"]

    def test_send_success(self):
        notifier = EmailNotifier(
            smtp_user="me@example.com", smtp_pass="pw", to_addrs=["dest@example.com"]
        )
        mock_smtp = MagicMock()
        with patch("src.email_notifier.smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_cls.return_value.__enter__.return_value = mock_smtp
            assert notifier.send("subject", "body") is True

        mock_smtp.login.assert_called_once_with("me@example.com", "pw")
        mock_smtp.sendmail.assert_called_once()
        assert notifier.sent_subjects() == ["subject"]

    def test_send_failure_returns_false(self):
        notifier = EmailNotifier(
            smtp_user="me@example.com", smtp_pass="pw", to_addrs=["dest@example.com"]
        )
        with patch("src.email_notifier.smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_cls.side_effect = Exception("connection refused")
            assert notifier.send("subject", "body") is False
