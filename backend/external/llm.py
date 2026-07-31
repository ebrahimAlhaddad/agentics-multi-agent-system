"""OpenAI configuration and lifecycle.

One provider, and no wrapper around it. The Agents SDK owns every model call —
the tool loop, tool dispatch, handoffs, streaming and per-request retries are
all its job

What this service does own is the client the SDK reaches for:

* the key and the model name, read once from settings,
* the retry policy, which is why a client is registered rather than just a key,
* failing at startup rather than mid-run when either is wrong.

This abstraction is to simplify model/provider swap in the future
"""

from typing import Optional

from agents import set_default_openai_client
from openai import AsyncOpenAI

from external.base import ExternalService
from settings import settings
from logger import logger
from exceptions.exceptions import ExternalServiceException


class LLMService(ExternalService):
    def __init__(self):
        self.client: Optional[AsyncOpenAI] = None
        self.model: Optional[str] = None

    # ------------------------------------------------------------- lifecycle

    async def startup(self) -> None:
        if not settings.OPENAI_API_KEY:
            raise ExternalServiceException("OPENAI_API_KEY is required", "LLMService")

        self.model = settings.model_name
        logger.info(f"Starting up LLM Service: openai / {self.model}")

        self.client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            max_retries=settings.MAX_LLM_RETRIES,
        )
        set_default_openai_client(self.client)

    async def sanity_check(self) -> None:
        """One real call, so a bad key fails at startup rather than mid-run.

        Retrieving the model rather than completing against it: this is a real
        authenticated request, so a wrong key still fails here, and it also
        catches a model name that does not exist — which a completion would
        too, but this one costs nothing and generates no tokens.
        """
        try:
            await self.client.models.retrieve(self.model)
            logger.info(f"LLM sanity check passed (openai / {self.model})")
        except Exception as e:
            logger.error(f"LLM sanity check failed: {e}")
            raise ExternalServiceException(str(e), "LLMService")

    async def shutdown(self) -> None:
        if self.client is not None:
            await self.client.close()
        self.client = None


llm_service = LLMService()
