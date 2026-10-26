"""
Thin wrapper around Azure AI Foundry Agent Service.

Kept deliberately small: this is the one place that talks to Foundry, so the
rest of the app (routes, tools, guardrails) never imports the Azure SDK
directly. Swap FOUNDRY_MODEL_DEPLOYMENT for FOUNDRY_MODEL_FALLBACK in .env if
your subscription can't get Azure OpenAI quota (see docs/architecture.md).
"""
from __future__ import annotations

import os

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from app.agent.system_prompt import SYSTEM_PROMPT


class KacyAgent:
    def __init__(self) -> None:
        self._endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
        self._model = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-4o-mini")
        self._client: AIProjectClient | None = None
        self._agent_id: str | None = None

    def _ensure_client(self) -> AIProjectClient:
        if self._client is None:
            self._client = AIProjectClient(
                endpoint=self._endpoint,
                credential=DefaultAzureCredential(),
            )
        return self._client

    def ensure_agent(self, tools: list[dict] | None = None) -> str:
        """Create (or reuse) the Kacy agent definition in Foundry Agent Service."""
        client = self._ensure_client()
        if self._agent_id:
            return self._agent_id

        agent = client.agents.create_agent(
            model=self._model,
            name="kacy",
            instructions=SYSTEM_PROMPT,
            tools=tools or [],
        )
        self._agent_id = agent.id
        return self._agent_id

    def run(self, user_message: str, thread_id: str | None = None) -> dict:
        """
        Send one message, return the agent's reply plus the thread id so the
        caller (FastAPI route) can keep the conversation going.

        NOTE: this is the integration point — fill in once FOUNDRY_PROJECT_ENDPOINT
        is real. Left unimplemented on purpose rather than faked, so a run
        against an unconfigured .env fails loudly instead of returning fake data.
        """
        client = self._ensure_client()
        agent_id = self.ensure_agent()

        thread = (
            client.agents.threads.get(thread_id)
            if thread_id
            else client.agents.threads.create()
        )
        client.agents.messages.create(thread_id=thread.id, role="user", content=user_message)
        run = client.agents.runs.create_and_process(thread_id=thread.id, agent_id=agent_id)

        messages = client.agents.messages.list(thread_id=thread.id)
        latest = next(iter(messages), None)
        reply_text = latest.content[0].text.value if latest and latest.content else ""

        return {
            "thread_id": thread.id,
            "run_id": run.id,
            "reply": reply_text,
            "status": run.status,
        }
