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
from openai import BadRequestError

from app.agent.system_prompt import SYSTEM_PROMPT
from app.tools.registry import TOOL_SPECS, dispatch

MAX_TOOL_ROUNDS = 5  # safety stop so a confused model can't loop forever


class ContentFiltered(Exception):
    """
    Azure OpenAI's built-in content filter (on the model deployment itself, separate
    from our Prompt Shields check) refused the request. `flagged` lists which
    filter categories fired, e.g. {"jailbreak": {"filtered": True, "detected": True}}.
    """

    def __init__(self, flagged: dict, raw: object):
        self.flagged = flagged
        self.raw = raw
        super().__init__(f"Azure OpenAI content filter blocked the request: {list(flagged) or 'unknown'}")


def _respond(openai, **kwargs):
    """responses.create, but a content-filter rejection becomes a ContentFiltered with details."""
    try:
        return openai.responses.create(**kwargs)
    except BadRequestError as exc:
        body = exc.body if isinstance(exc.body, dict) else {}
        if body.get("code") != "content_filter" and "content management policy" not in str(exc):
            raise
        # Azure has used two shapes for this: a single innererror.content_filter_result,
        # or a content_filters list with content_filter_results per source. Read both.
        results = [(body.get("innererror") or {}).get("content_filter_result") or {}]
        results += [f.get("content_filter_results") or {} for f in body.get("content_filters") or []]
        flagged = {
            k: v
            for result in results
            for k, v in result.items()
            if isinstance(v, dict) and (v.get("filtered") or v.get("detected"))
        }
        raise ContentFiltered(flagged, exc.body) from exc


class KacyAgent:
    def __init__(self) -> None:
        # Format: https://<resource>.services.ai.azure.com/api/projects/<project>
        self._endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
        self._agent_name = os.environ.get("FOUNDRY_AGENT_NAME", "kacy")
        self._model = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-4.1-mini")
        self._project: AIProjectClient | None = None
        self._openai = None

    @property
    def model(self) -> str:
        return self._model

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

    def run(
        self,
        user_message: str,
        user_email: str,
        thread_id: str | None = None,
        seed_history: list[dict] | None = None,
    ) -> dict:
        """
        Send one message, return the agent's reply plus the conversation id so
        the caller (FastAPI route) can keep the conversation going.

        If the model asks for tools, we run them here as `user_email` (the
        identity the backend verified, not anything the model supplied) and hand
        the results back until the model produces a final answer.
        """
        openai = self._ensure_openai()

        if thread_id:
            conversation_id = thread_id
        elif seed_history:
            # A chat whose first answers came from the semantic cache has no Foundry
            # conversation yet. Start one now and replay what the user already saw,
            # so the model has the context for this follow-up.
            items = [{"type": "message", "role": m["role"], "content": m["content"]} for m in seed_history]
            conversation_id = openai.conversations.create(items=items).id
        else:
            conversation_id = openai.conversations.create().id
        response = _respond(openai, conversation=conversation_id, input=user_message)

        access_denied = False
        tools_called: list[str] = []
        tool_trace: list[dict] = []  # per call: which tool, and whether RBAC denied it
        for _ in range(MAX_TOOL_ROUNDS):
            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                break

            outputs = []
            for call in calls:
                output, denied = dispatch(call.name, call.arguments, user_email)
                tools_called.append(call.name)
                tool_trace.append({"name": call.name, "denied": denied})
                access_denied = access_denied or denied
                outputs.append(
                    {"type": "function_call_output", "call_id": call.call_id, "output": output}
                )
            response = _respond(openai, conversation=conversation_id, input=outputs)

        return {
            "thread_id": conversation_id,
            "run_id": response.id,
            "reply": response.output_text,
            "status": response.status,
            "access_denied": access_denied,
            "tools_called": tools_called,
            "tool_trace": tool_trace,
        }
