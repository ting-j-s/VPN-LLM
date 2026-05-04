"""LLM Client Module.

Provides a generic LLM API client for OpenAI-style APIs.
Used for code generation, test generation, and error analysis.
"""

import os
import time
from typing import Optional

import requests

from ..common.errors import VPNError
from ..common.logger import get_logger


logger = get_logger(__name__)


class LLMClientError(VPNError):
    """LLM client error."""
    pass


class LLMClient:
    """Generic LLM API client compatible with OpenAI-style APIs.

    Reads configuration from environment variables:
        LLM_API_KEY: API key for authentication
        LLM_BASE_URL: Base URL for the API (e.g., https://api.openai.com/v1)
        LLM_MODEL: Model name to use (e.g., gpt-4, gpt-3.5-turbo)

    Usage:
        client = LLMClient()
        response = client.ask("Hello, how are you?")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 60.0,
        max_retries: int = 3,
    ):
        """Initialize LLM client.

        Args:
            api_key: API key. If None, reads from LLM_API_KEY env var.
            base_url: Base URL. If None, reads from LLM_BASE_URL env var.
            model: Model name. If None, reads from LLM_MODEL env var.
            timeout: Request timeout in seconds.
            max_retries: Maximum number of retry attempts.

        Raises:
            LLMClientError: If required environment variables are missing.
        """
        self.api_key = api_key or os.environ.get("LLM_API_KEY")
        self.base_url = base_url or os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        self.model = model or os.environ.get("LLM_MODEL", "gpt-4")
        self.timeout = timeout
        self.max_retries = max_retries

        self._validate_config()

    def _validate_config(self) -> None:
        """Validate that required configuration is present.

        Raises:
            LLMClientError: If required config is missing.
        """
        missing = []
        if not self.api_key:
            missing.append("LLM_API_KEY")
        if not self.base_url:
            missing.append("LLM_BASE_URL")
        if not self.model:
            missing.append("LLM_MODEL")

        if missing:
            raise LLMClientError(
                f"Missing required environment variables: {', '.join(missing)}. "
                f"Please set these environment variables before using the LLM client."
            )

    def ask(self, prompt: str, retry_count: int = 0) -> str:
        """Send a prompt to the LLM and return the response.

        Args:
            prompt: The prompt to send to the LLM.
            retry_count: Current retry attempt (internal use).

        Returns:
            The LLM's response as a string.

        Raises:
            LLMClientError: If the API call fails after all retries.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
        }

        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"

        logger.info(f"LLM request to {endpoint} with model {self.model}")

        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
        except requests.Timeout:
            raise LLMClientError(f"LLM request timed out after {self.timeout}s")
        except requests.ConnectionError as e:
            raise LLMClientError(f"LLM connection failed: {e}")

        if response.status_code == 200:
            try:
                data = response.json()
                choices = data.get("choices", [])
                if choices and len(choices) > 0:
                    message = choices[0].get("message", {})
                    content = message.get("content", "")
                    logger.info(f"LLM response received: {len(content)} chars")
                    return content.strip()
                else:
                    raise LLMClientError("LLM response had no choices")
            except (ValueError, KeyError) as e:
                raise LLMClientError(f"Failed to parse LLM response: {e}")

        elif response.status_code == 401:
            raise LLMClientError("Invalid API key. Please check your LLM_API_KEY.")

        elif response.status_code == 429:
            # Rate limited - try to retry with backoff
            if retry_count < self.max_retries:
                wait_time = 2 ** retry_count
                logger.warning(f"Rate limited, retrying in {wait_time}s...")
                time.sleep(wait_time)
                return self.ask(prompt, retry_count + 1)
            raise LLMClientError("Rate limited by LLM API after retries")

        elif response.status_code >= 500:
            # Server error - try to retry
            if retry_count < self.max_retries:
                wait_time = 2 ** retry_count
                logger.warning(f"LLM server error, retrying in {wait_time}s...")
                time.sleep(wait_time)
                return self.ask(prompt, retry_count + 1)
            raise LLMClientError(f"LLM server error after {self.max_retries} retries")

        else:
            raise LLMClientError(f"LLM API error ({response.status_code}): {response.text}")

    def __repr__(self) -> str:
        return f"LLMClient(model={self.model}, base_url={self.base_url})"


def create_llm_client(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 60.0,
) -> LLMClient:
    """Create an LLM client with the given or environment-based config.

    Args:
        api_key: Optional override for LLM_API_KEY.
        base_url: Optional override for LLM_BASE_URL.
        model: Optional override for LLM_MODEL.
        timeout: Request timeout in seconds.

    Returns:
        LLMClient instance.

    Raises:
        LLMClientError: If required config is missing.
    """
    return LLMClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=timeout,
    )