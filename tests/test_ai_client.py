"""Tests for AI client abstraction."""

from unittest.mock import MagicMock, patch

import pytest

from backend.ai_client import call_ai, call_ai_json, strip_code_fences


class TestStripCodeFences:
    def test_strips_json_fence(self):
        assert strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_strips_plain_fence(self):
        assert strip_code_fences("```\n[1, 2]\n```") == "[1, 2]"

    def test_noop_on_clean_json(self):
        assert strip_code_fences('{"a": 1}') == '{"a": 1}'

    def test_strips_whitespace(self):
        assert strip_code_fences("  \n```json\n{}\n```\n  ") == "{}"


class TestCallAI:
    @patch("backend.ai_client.get_client")
    def test_successful_call(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello world")]
        mock_client.messages.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = call_ai([{"role": "user", "content": "Hi"}])
        assert result == "Hello world"

    @patch("backend.ai_client.get_client")
    def test_streaming_for_large_max_tokens(self, mock_get_client):
        mock_client = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.get_final_text.return_value = "streamed response"
        mock_client.messages.stream.return_value = mock_stream
        mock_get_client.return_value = mock_client

        result = call_ai([{"role": "user", "content": "Hi"}], max_tokens=20000)
        assert result == "streamed response"
        mock_client.messages.stream.assert_called_once()

    def test_unsupported_provider(self):
        with patch("backend.ai_client.AI_PROVIDER", "openai"):
            with pytest.raises(NotImplementedError, match="openai"):
                call_ai([{"role": "user", "content": "Hi"}])

    def test_no_api_key(self):
        """get_client() raises when no API key is set."""
        import backend.ai_client as mod
        saved = mod._client
        try:
            mod._client = None
            with patch.object(mod, "ANTHROPIC_API_KEY", ""):
                with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
                    mod.get_client()
        finally:
            mod._client = saved


class TestCallAIJSON:
    @patch("backend.ai_client.call_ai")
    def test_parses_json(self, mock_call):
        mock_call.return_value = '{"key": "value"}'
        result = call_ai_json([{"role": "user", "content": "test"}])
        assert result == {"key": "value"}

    @patch("backend.ai_client.call_ai")
    def test_strips_fences_before_parse(self, mock_call):
        mock_call.return_value = '```json\n[1, 2, 3]\n```'
        result = call_ai_json([{"role": "user", "content": "test"}])
        assert result == [1, 2, 3]

    @patch("backend.ai_client.call_ai")
    def test_recovers_truncated_array(self, mock_call):
        mock_call.return_value = '[{"a": 1}, {"b": 2},'
        result = call_ai_json([{"role": "user", "content": "test"}])
        assert result == [{"a": 1}, {"b": 2}]

    @patch("backend.ai_client.call_ai")
    def test_raises_on_unparseable(self, mock_call):
        mock_call.return_value = "not json at all"
        with pytest.raises(RuntimeError, match="could not parse JSON"):
            call_ai_json([{"role": "user", "content": "test"}])
