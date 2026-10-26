import { FormEvent, useState } from "react";
import type { Message } from "../types";

interface Props {
  messages: Message[];
  onSend: (text: string) => void;
  busy: boolean;
}

export default function ChatPane({ messages, onSend, busy }: Props) {
  const [draft, setDraft] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    setDraft("");
  }

  return (
    <section className="chat-pane">
      <header className="pane-header">
        <div className="kacy-mark" aria-hidden="true">K</div>
        <div>
          <div className="pane-title">Kacy</div>
          <div className="pane-subtitle">Loom &amp; Light — internal assistant</div>
        </div>
      </header>

      <div className="message-list">
        {messages.map((m) => (
          <div key={m.id} className={`message message-${m.role}`}>
            <div className="message-bubble">{m.text}</div>
          </div>
        ))}
        {busy && (
          <div className="message message-assistant">
            <div className="message-bubble message-typing">
              <span /><span /><span />
            </div>
          </div>
        )}
      </div>

      <form className="composer" onSubmit={handleSubmit}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask about leave policy, draft an email, check a salary…"
          aria-label="Message Kacy"
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}
