import type { ActivityItem } from "../types";
import ApprovalCard from "./ApprovalCard";

interface Props {
  items: ActivityItem[];
  onDecide: (id: string, decision: "approved" | "discarded") => void;
}

const KIND_LABEL: Record<ActivityItem["kind"], string> = {
  citation: "Retrieved",
  tool_call: "Tool call",
  approval: "Needs approval",
  denied: "Access denied",
};

export default function ActivityPane({ items, onDecide }: Props) {
  return (
    <aside className="activity-pane">
      <header className="pane-header">
        <div className="pane-title">Activity</div>
        <div className="pane-subtitle">What Kacy is doing, as it happens</div>
      </header>

      <div className="activity-list">
        {items.length === 0 && (
          <p className="activity-empty">Tool calls, citations, and drafts will show up here.</p>
        )}
        {items.map((item) => (
          <div key={item.id} className={`activity-item activity-${item.kind}`}>
            <div className="activity-kind">{KIND_LABEL[item.kind]}</div>
            <div className="activity-title">{item.title}</div>
            <div className="activity-detail">{item.detail}</div>
            {item.kind === "approval" && <ApprovalCard item={item} onDecide={onDecide} />}
          </div>
        ))}
      </div>
    </aside>
  );
}
