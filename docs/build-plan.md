# Build plan

| Phase | Goal | Status |
|---|---|---|
| 0 | Unblock the Azure subscription — deploy *any* model | not started (needs your Azure login) |
| 1 | Bare wrapper — agent responds in character, no data | scaffolded (`backend/app/agent`) |
| 2 | RAG grounding | scaffolded (`backend/app/rag`, `demo-company/`) |
| 3 | Agent + tools (MCP) | scaffolded (`backend/app/tools`) |
| 4 | Identity, RBAC, guardrails | scaffolded (`backend/app/identity`, `backend/app/guardrails`) |
| 5 | Cost controls | scaffolded (`backend/app/cost`) |
| 6 | Observability + eval gate | scaffolded (`backend/app/eval`, `.github/workflows/eval.yml`) |
| 7 | UI — Copilot-style split pane | scaffolded (`frontend/`) |
| 8 | Deploy + record demo clips | not started |

"Scaffolded" means the code is written and structured correctly but hasn't been run against real Azure resources yet — do Phase 0 first, then come back through each phase and replace the `NotImplementedError` / local-stand-in bits with real Azure calls as each resource gets provisioned.
