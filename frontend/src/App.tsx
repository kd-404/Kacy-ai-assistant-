import { useState } from "react";
import ActivityPane from "./components/ActivityPane";
import ChatPane from "./components/ChatPane";
import { FALLBACK_REPLY, SCRIPTED_TURNS } from "./demoScript";
import type { ActivityItem, Message } from "./types";

const WELCOME: Message = {
  id: "welcome",
  role: "assistant",
  text: "Hi, I'm Kacy. Ask me about company policy, or try: “draft an email to the team” or “what's the CEO's salary”.",
};

export default function App() {
  const [messages, setMessages] = useState<Message[]>([WELCOME]);
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [busy, setBusy] = useState(false);

  function handleSend(text: string) {
    const userMessage: Message = { id: crypto.randomUUID(), role: "user", text };
    setMessages((prev) => [...prev, userMessage]);
    setBusy(true);

    const turn = SCRIPTED_TURNS.find((t) => text.toLowerCase().includes(t.trigger));

    window.setTimeout(() => {
      setMessages((prev) => [...prev, turn ? turn.reply : FALLBACK_REPLY]);
      if (turn) setActivity((prev) => [...turn.activity, ...prev]);
      setBusy(false);
    }, 500);
  }

  function handleDecide(id: string, decision: "approved" | "discarded") {
    setActivity((prev) =>
      prev.map((item) => (item.id === id ? { ...item, status: decision } : item))
    );
  }

  return (
    <div className="app-shell">
      <ChatPane messages={messages} onSend={handleSend} busy={busy} />
      <ActivityPane items={activity} onDecide={handleDecide} />
    </div>
  );
}
