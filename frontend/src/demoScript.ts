/**
 * Canned demo conversation so the UI is fully demonstrable without a live
 * backend — matches the three signature scenes from the reference video:
 * grounded RAG answer, a write action behind an approval gate, and RBAC
 * denial. Swap for a real fetch() to the FastAPI backend once Phase 0-3
 * are deployed (see src/api.ts).
 */
import type { ActivityItem, Message } from "./types";

export interface ScriptedTurn {
  trigger: string;
  reply: Message;
  activity: ActivityItem[];
}

export const SCRIPTED_TURNS: ScriptedTurn[] = [
  {
    trigger: "leave",
    reply: {
      id: "r1",
      role: "assistant",
      text: "You get 18 paid vacation days a year, accrued monthly, plus 10 paid sick days available from day one. Unused vacation rolls over up to 5 days.",
    },
    activity: [
      {
        id: "a1",
        kind: "citation",
        title: "Retrieved from company docs",
        detail: "employee-handbook.md — Leave policy",
      },
    ],
  },
  {
    trigger: "email",
    reply: {
      id: "r2",
      role: "assistant",
      text: "Here's a draft — check the activity panel and let me know if you want changes before I send it.",
    },
    activity: [
      {
        id: "a2",
        kind: "tool_call",
        title: "mcp_email.draft_email",
        detail: "Drafting a message to #team",
      },
      {
        id: "a3",
        kind: "approval",
        title: "Email draft",
        detail: "Awaiting your approval",
        status: "pending",
        draft: {
          to: "team@loomandlight.test",
          subject: "Release pushed to next Tuesday",
          body: "Hey team — the v2.4 release is slipping to next Tuesday while we finish QA on the billing changes. No action needed on your end, just flagging the new date.",
        },
      },
    ],
  },
  {
    trigger: "ceo",
    reply: {
      id: "r3",
      role: "assistant",
      text: "I can't share that — salary information is only visible to the employee themselves, their manager, or an exec. You're signed in as an intern, so that one's access denied.",
    },
    activity: [
      {
        id: "a4",
        kind: "denied",
        title: "mcp_hr.get_salary — access denied",
        detail: "RBAC: role=intern, resource=salary:dev@kacy-demo.test",
      },
    ],
  },
];

export const FALLBACK_REPLY: Message = {
  id: "fallback",
  role: "assistant",
  text: "This is a scripted demo build — try asking about the leave policy, asking me to email the team, or asking for the CEO's salary to see the three signature flows.",
};
