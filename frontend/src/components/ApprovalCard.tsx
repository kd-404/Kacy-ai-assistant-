import type { ActivityItem } from "../types";

interface Props {
  item: ActivityItem;
  onDecide: (id: string, decision: "approved" | "discarded") => void;
}

export default function ApprovalCard({ item, onDecide }: Props) {
  const { draft, status } = item;
  if (!draft) return null;

  return (
    <div className={`approval-card approval-${status}`}>
      <div className="approval-field">
        <span className="approval-label">To</span>
        <span>{draft.to}</span>
      </div>
      <div className="approval-field">
        <span className="approval-label">Subject</span>
        <span>{draft.subject}</span>
      </div>
      <p className="approval-body">{draft.body}</p>

      {status === "pending" ? (
        <div className="approval-actions">
          <button className="btn-primary" onClick={() => onDecide(item.id, "approved")}>
            Approve &amp; send
          </button>
          <button className="btn-ghost" onClick={() => onDecide(item.id, "discarded")}>
            Discard
          </button>
        </div>
      ) : (
        <div className={`approval-status approval-status-${status}`}>
          {status === "approved" ? "Sent" : "Discarded"}
        </div>
      )}
    </div>
  );
}
