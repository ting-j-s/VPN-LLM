"""Tests for LLM Client module."""

import os
import pytest
from unittest.mock import patch, MagicMock

from src.llm.llm_client import LLMClient, LLMClientError, create_llm_client


class TestLLMClientConfig:
    """Test LLM client configuration."""

    def test_reads_from_env_vars(self):
        """Test that client reads config from environment variables."""
        with patch.dict(os.environ, {
            "LLM_API_KEY": "test-key-123",
            "LLM_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL": "gpt-4",
        }):
            client = LLMClient()
            assert client.api_key == "test-key-123"
            assert client.base_url == "https://api.example.com/v1"
            assert client.model == "gpt-4"

    def test_missing_api_key_raises_error(self):
        """Test that missing API key raises clear error."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(LLMClientError, match="LLM_API_KEY"):
                LLMClient()

    def test_missing_base_url_uses_default(self):
        """Test that missing base URL uses default value."""
        env = {"LLM_API_KEY": "test-key", "LLM_MODEL": "gpt-4"}
        with patch.dict(os.environ, env, clear=True):
            client = LLMClient()
            assert client.base_url == "https://api.openai.com/v1"

    def test_missing_model_uses_default(self):
        """Test that missing model uses default value."""
        env = {"LLM_API_KEY": "test-key", "LLM_BASE_URL": "https://api.example.com/v1"}
        with patch.dict(os.environ, env, clear=True):
            client = LLMClient()
            assert client.model == "gpt-4"

    def test_only_api_key_is_required(self):
        """Test that only API key is required, others have defaults."""
        with patch.dict(os.environ, {"LLM_API_KEY": "test-key"}, clear=True):
            client = LLMClient()
            assert client.api_key == "test-key"
            assert client.base_url == "https://api.openai.com/v1"
            assert client.model == "gpt-4"

    def test_explicit_config_overrides_env(self):
        """Test that explicit config parameters override environment."""
        with patch.dict(os.environ, {
            "LLM_API_KEY": "env-key",
            "LLM_BASE_URL": "https://env.com/v1",
            "LLM_MODEL": "env-model",
        }):
            client = LLMClient(
                api_key="override-key",
                base_url="https://override.com/v1",
                model="override-model",
            )
            assert client.api_key == "override-key"
            assert client.base_url == "https://override.com/v1"
            assert client.model == "override-model"


class TestLLMClientAsk:
    """Test LLM client ask() method."""

    @patch("requests.post")
    def test_successful_call(self, mock_post):
        """Test successful API call."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {"message": {"content": "Hello, how can I help you?"}}
            ]
        }
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL": "gpt-4",
        }):
            client = LLMClient()
            response = client.ask("Hello")

            assert response == "Hello, how can I help you?"
            mock_post.assert_called_once()

    @patch("requests.post")
    def test_timeout_error(self, mock_post):
        """Test that timeout raises LLMClientError."""
        import requests
        mock_post.side_effect = requests.Timeout("Connection timed out")

        with patch.dict(os.environ, {
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL": "gpt-4",
        }):
            client = LLMClient(timeout=5.0)
            with pytest.raises(LLMClientError, match="timed out"):
                client.ask("Hello")

    @patch("requests.post")
    def test_connection_error(self, mock_post):
        """Test that connection error raises LLMClientError."""
        import requests
        mock_post.side_effect = requests.ConnectionError("Failed to connect")

        with patch.dict(os.environ, {
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL": "gpt-4",
        }):
            client = LLMClient()
            with pytest.raises(LLMClientError, match="connection failed"):
                client.ask("Hello")

    @patch("requests.post")
    def test_401_unauthorized(self, mock_post):
        """Test that 401 raises LLMClientError."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Invalid API key"
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {
            "LLM_API_KEY": "bad-key",
            "LLM_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL": "gpt-4",
        }):
            client = LLMClient()
            with pytest.raises(LLMClientError, match="Invalid API key"):
                client.ask("Hello")

    @patch("requests.post")
    def test_429_rate_limit_retries(self, mock_post):
        """Test that 429 retries with backoff."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limited"
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL": "gpt-4",
        }):
            client = LLMClient(max_retries=2)
            with pytest.raises(LLMClientError, match="Rate limited"):
                client.ask("Hello")

            # Should have tried 3 times (initial + 2 retries)
            assert mock_post.call_count == 3

    @patch("requests.post")
    def test_500_server_error_retries(self, mock_post):
        """Test that 5xx errors retry with backoff."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal server error"
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL": "gpt-4",
        }):
            client = LLMClient(max_retries=2)
            with pytest.raises(LLMClientError, match="server error after"):
                client.ask("Hello")

            assert mock_post.call_count == 3

    @patch("requests.post")
    def test_empty_choices_raises_error(self, mock_post):
        """Test that empty choices raises LLMClientError."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": []}
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL": "gpt-4",
        }):
            client = LLMClient()
            with pytest.raises(LLMClientError, match="no choices"):
                client.ask("Hello")

    @patch("requests.post")
    def test_response_content_stripped(self, mock_post):
        """Test that response content is stripped of whitespace."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {"message": {"content": "  Hello, world!  \n\n"}}
            ]
        }
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL": "gpt-4",
        }):
            client = LLMClient()
            response = client.ask("Hello")
            assert response == "Hello, world!"


class TestLLMClientRepr:
    """Test LLM client repr."""

    def test_repr_format(self):
        """Test repr output format."""
        with patch.dict(os.environ, {
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL": "gpt-4",
        }):
            client = LLMClient()
            r = repr(client)
            assert "LLMClient" in r
            assert "gpt-4" in r
            assert "api.example.com" in r


class TestCreateLLMClient:
    """Test create_llm_client factory function."""

    def test_creates_client_with_defaults(self):
        """Test factory creates client with env var defaults."""
        with patch.dict(os.environ, {
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL": "gpt-4",
        }):
            client = create_llm_client()
            assert isinstance(client, LLMClient)
            assert client.model == "gpt-4"

    def test_creates_client_with_overrides(self):
        """Test factory creates client with parameter overrides."""
        with patch.dict(os.environ, {
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL": "gpt-4",
        }):
            client = create_llm_client(
                api_key="override",
                base_url="https://override.com",
                model="override-model",
            )
            assert client.api_key == "override"
            assert client.base_url == "https://override.com"
            assert client.model == "override-model"