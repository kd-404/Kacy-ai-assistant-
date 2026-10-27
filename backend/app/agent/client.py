"""
Thin wrapper around Azure AI Foundry Agent Service (azure-ai-projects 2.x).

Kept deliberately small: this is the one place that talks to Foundry, so the
rest of the app (routes, tools, guardrails) never imports the Azure SDK
directly.

How the pieces map (new Foundry projects, SDK 2.x):
  - The agent ("kacy") lives in Foundry as a versioned prompt agent. The portal
    Agents page and publish_version() below both create versions of it.
  - A conversation holds the multi-turn history. We expose its id to the rest
    of the app as "thread_id" so main.py and the frontend don't change.
  - Each user message is one `responses.create` call against that conversation,
    routed to the agent by name.
"""
from __future__ import annotations

import os

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
from azure.identity import DefaultAzureCredential

from app.agent.system_prompt import SYSTEM_PROMPT
from app.tools.registry import TOOL_SPECS, dispatch

MAX_TOOL_ROUNDS = 5  # safety stop so a confused model can't loop forever


class KacyAgent:
    def __init__(self) -> None:
        # Format: https://<resource>.services.ai.azure.com/api/projects/<project>
        self._endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
        self._agent_name = os.environ.get("FOUNDRY_AGENT_NAME", "kacy")
        self._model = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-4.1-mini")
        self._project: AIProjectClient | None = None
        self._openai = None

    def _ensure_project(self) -> AIProjectClient:
        if self._project is None:
            self._project = AIProjectClient(
                endpoint=self._endpoint,
                credential=DefaultAzureCredential(),
            )
        return self._project

    def _ensure_openai(self):
        """OpenAI-style client pre-bound to the Kacy agent."""
        if self._openai is None:
            self._openai = self._ensure_project().get_openai_client(agent_name=self._agent_name)
        return self._openai

    def publish_version(self) -> str:
        """
        Push SYSTEM_PROMPT from this repo to Foundry as a new version of the agent.
        Not called per request; run it by hand (or from CI) when the prompt changes.
        """
        agent = self._ensure_project().agents.create_version(
            agent_name=self._agent_name,
            definition=PromptAgentDefinition(
                model=self._model,
                instructions=SYSTEM_PROMPT,
                tools=[
                    FunctionTool(
                        name=spec["name"],
                        description=spec["description"],
                        parameters=spec["parameters"],
                        strict=True,
                    )
                    for spec in TOOL_SPECS
                ],
            ),
        )
        return f"{agent.name} v{agent.version}"

    def run(self, user_message: str, user_email: str, thread_id: str | None = None) -> dict:
        """
        Send one message, return the agent's reply plus the conversation id so
        the caller (FastAPI route) can keep the conversation going.

        If the model asks for tools, we run them here as `user_email` (the
        identity the backend verified, not anything the model supplied) and hand
        the results back until the model produces a final answer.
        """
        openai = self._ensure_openai()

        conversation_id = thread_id or openai.conversations.create().id
        response = openai.responses.create(conversation=conversation_id, input=user_message)

        access_denied = False
        for _ in range(MAX_TOOL_ROUNDS):
            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                break

            outputs = []
            for call in calls:
                output, denied = dispatch(call.name, call.arguments, user_email)
                access_denied = access_denied or denied
                outputs.append(
                    {"type": "function_call_output", "call_id": call.call_id, "output": output}
                )
            response = openai.responses.create(conversation=conversation_id, input=outputs)

        return {
            "thread_id": conversation_id,
            "run_id": response.id,
            "reply": response.output_text,
            "status": response.status,
            "access_denied": access_denied,
        }
