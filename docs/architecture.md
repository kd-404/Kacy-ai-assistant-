# Architecture

Full diagram: see the architecture artifact linked from the project — or regenerate it from `docs/diagram-source.md` notes below if the link has gone stale.

## 0. The subscription blocker (read first if you're on Azure for Students)

Student subscriptions frequently can't deploy Azure OpenAI models out of the box — 0 TPM quota or a region-policy block (`RequestDisallowedByAzure`) are common, not edge cases.

Fixes, in order of effort:
1. Check Policy → Assignments → "Allowed resource deployment regions" in the Azure portal, and deploy everything into one of the listed regions.
2. Request a quota increase from the Foundry resource's Quotas page (can take 1–2 days).
3. Prefer a non-OpenAI model from the Foundry catalog (Phi-4-mini, Llama-3.1-8B) deployed as serverless Models-as-a-Service — different quota pool, usually easier to get on a student sub. `backend/.env.example` has `FOUNDRY_MODEL_FALLBACK` for exactly this.
4. Last resort: a separate Pay-As-You-Go subscription.

## 1. Component → Azure service map

| Layer | Azure service | Where in this repo |
|---|---|---|
| Wrapper + persona | Foundry Agent Service, agent `instructions` | `backend/app/agent/system_prompt.py` |
| RAG | Azure AI Search (vector index) + embeddings | `backend/app/rag/` |
| Tool calling | Foundry Agent Service + MCP (preview) tool | `backend/app/tools/mcp_*` |
| RBAC / identity | Foundry Agent Identity + Entra ID OBO | `backend/app/identity/rbac.py` (local stand-in until Entra is wired up) |
| Jailbreak defense | Azure AI Content Safety — Prompt Shields | `backend/app/guardrails/prompt_shields.py` |
| PII redaction | Azure AI Language | `backend/app/guardrails/pii_redaction.py` |
| Semantic cache | Azure Managed Redis (optional) | `backend/app/cost/semantic_cache.py` |
| Model router | Foundry Model Router (or the local heuristic here) | `backend/app/cost/model_router.py` |
| Observability + eval | Foundry tracing + Application Insights + eval suite | `backend/app/eval/` |
| Infra | Bicep + `azd` | `infra/`, `azure.yaml` |

## 2. New additions beyond the reference video

- Copilot-style split-pane UI (`frontend/`) — chat + a live activity panel showing tool calls, RAG citations, and draft/approval cards.
- Four agentic actions, each behind a human approval gate: email draft/send, meeting scheduling, document drafting/summarizing, approval-chain workflows (`backend/app/tools/mcp_email`, `mcp_calendar`, `mcp_docs`, `mcp_approval_chain`).

## 3. Local dev vs. production

Local dev: VS Code + the Microsoft Foundry extension, this repo as a normal git checkout. `azd up` / `azd down` from `infra/` to provision and tear down so a student budget isn't burned on idle resources between sessions. See `docs/build-plan.md` for the phase order this was built in.
