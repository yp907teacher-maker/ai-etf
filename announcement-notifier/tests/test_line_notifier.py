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


class TestSendAnnouncement:
    def test_no_image_sends_text_only_message(self):
        notifier = LineNotifier(channel_access_token="token", target_ids=["U1"])
        with patch("src.line_notifier.requests.post") as mock_post:
            mock_post.return_value = Mock(raise_for_status=Mock())
            assert notifier.send_announcement("hello", image_url=None) is True

        payload = mock_post.call_args.kwargs["json"]
        assert payload["messages"] == [{"type": "text", "text": "hello"}]

    def test_invalid_image_url_is_dropped(self):
        notifier = LineNotifier(channel_access_token="token", target_ids=["U1"])
        with patch("src.line_notifier.requests.post") as mock_post:
            mock_post.return_value = Mock(raise_for_status=Mock())
            notifier.send_announcement("hello", image_url="http://example.com/a.jpg")

        payload = mock_post.call_args.kwargs["json"]
        assert payload["messages"] == [{"type": "text", "text": "hello"}]

    def test_valid_image_url_prepends_image_message(self):
        notifier = LineNotifier(channel_access_token="token", target_ids=["U1"])
        image_url = "https://example.com/a.jpg"
        with patch("src.line_notifier.requests.post") as mock_post:
            mock_post.return_value = Mock(raise_for_status=Mock())
            assert notifier.send_announcement("hello", image_url=image_url) is True

        payload = mock_post.call_args.kwargs["json"]
        assert payload["messages"][0] == {
            "type": "image",
            "originalContentUrl": image_url,
            "previewImageUrl": image_url,
        }
        assert payload["messages"][1] == {"type": "text", "text": "hello"}

    def test_image_send_failure_falls_back_to_text_only(self):
        import requests

        notifier = LineNotifier(channel_access_token="token", target_ids=["U1"])
        image_url = "https://example.com/a.jpg"
        with patch("src.line_notifier.requests.post") as mock_post:
            mock_post.side_effect = [requests.RequestException("boom"), Mock(raise_for_status=Mock())]
            assert notifier.send_announcement("hello", image_url=image_url) is True

        assert mock_post.call_count == 2
        retry_payload = mock_post.call_args.kwargs["json"]
        assert retry_payload["messages"] == [{"type": "text", "text": "hello"}]
