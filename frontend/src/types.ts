export type Role = "user" | "assistant";

export interface Message {
  id: string;
  role: Role;
  text: string;
}

export type ActivityKind = "citation" | "tool_call" | "approval" | "denied";

export interface ActivityItem {
  id: string;
  kind: ActivityKind;
  title: string;
  detail: string;
  status?: "pending" | "approved" | "discarded" | "done";
  draft?: { to: string; subject: string; body: string };
}
