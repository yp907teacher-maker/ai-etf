from unittest.mock import Mock, patch

from src.line_notifier import MULTICAST_URL, PUSH_URL, LineNotifier


class TestLineNotifier:
    def test_no_token_is_noop(self):
        notifier = LineNotifier(channel_access_token="", target_ids=["U1"])
        assert notifier.send_text("hello") is False
        assert notifier.sent_messages()[-1]["reason"] == "no_credentials"

    def test_no_target_ids_is_noop(self):
        notifier = LineNotifier(channel_access_token="token", target_ids=[])
        assert notifier.send_text("hello") is False

    def test_single_target_uses_push_endpoint(self):
        notifier = LineNotifier(channel_access_token="token", target_ids=["U1"])
        with patch("src.line_notifier.requests.post") as mock_post:
            mock_post.return_value = Mock(raise_for_status=Mock())
            assert notifier.send_text("hello") is True

        called_url = mock_post.call_args.args[0]
        assert called_url == PUSH_URL
        payload = mock_post.call_args.kwargs["json"]
        assert payload["to"] == "U1"

    def test_multiple_targets_uses_multicast_endpoint(self):
        notifier = LineNotifier(channel_access_token="token", target_ids=["U1", "U2"])
        with patch("src.line_notifier.requests.post") as mock_post:
            mock_post.return_value = Mock(raise_for_status=Mock())
            assert notifier.send_text("hello") is True

        called_url = mock_post.call_args.args[0]
        assert called_url == MULTICAST_URL
        payload = mock_post.call_args.kwargs["json"]
        assert payload["to"] == ["U1", "U2"]

    def test_request_failure_returns_false(self):
        import requests

        notifier = LineNotifier(channel_access_token="token", target_ids=["U1"])
        with patch("src.line_notifier.requests.post") as mock_post:
            mock_post.side_effect = requests.RequestException("boom")
            assert notifier.send_text("hello") is False
