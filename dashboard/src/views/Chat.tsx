// Chat (plan 7.14): the conversation as cards; any past card re-opens full size. Needs Clayton's
// board approval (no board yet, D92/D158).
import { type FormEvent, useEffect, useRef, useState } from "react";
import type { Answer, CoreState } from "../api/types";
import { Card } from "../cards/Card";
import type { Card as CardData } from "../cards/types.gen";
import { Button, StatusGlyph } from "../components/core";
import { GLYPH } from "../state/states";

export function Chat(props: {
  answers: Answer[];
  core: CoreState | null;
  onAsk: (text: string) => Promise<void>;
  openCard: (card: CardData) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const asking = props.core?.asking ?? [];
  // A block body: scrollIntoView may return a promise, which React would take for a cleanup.
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [props.answers.length, asking.length]);

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    const q = text.trim();
    if (!q || busy) return;
    setBusy(true);
    try {
      await props.onAsk(q);
      setText("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="view-chat" data-testid="view-chat">
      <div className="view-head">
        <h1 className="page-title">Chat</h1>
        <p className="lede">Every answer as a card, the newest at the bottom. Open any card full size.</p>
      </div>
      <div className="gos-panel chat-log" data-testid="chat-log">
        {props.answers.length === 0 && asking.length === 0 && (
          <p className="empty">{GLYPH.todo} No conversation yet. Ask about a recipe, an item, a machine or a quest.</p>
        )}
        {props.answers.map((a) => (
          <article key={a.id} className="exchange" data-answer={a.id}>
            <p className="question">
              <span className="gos-src">{a.by === "admin" ? "you, from gos" : a.by}</span> "{a.request}"
            </p>
            <div className="reply">
              <p className="answer-text">{a.text}</p>
              <div className="answer-meta">
                <span className="gos-chip">{a.lane}</span>
                {a.checked ? (
                  <StatusGlyph status="ok" glyph={GLYPH.done} label="numbers checked" />
                ) : (
                  <StatusGlyph status="warn" label="numbers not checked" />
                )}
                {a.card && (
                  <Button onClick={() => props.openCard(a.card as CardData)} testId="open-card">
                    Open full size
                  </Button>
                )}
              </div>
              {a.card && <Card card={a.card} compact />}
            </div>
          </article>
        ))}
        {asking.map((q) => (
          <article key={q.id} className="exchange pending">
            <p className="question">"{q.text}"</p>
            <StatusGlyph status="info" glyph={GLYPH.progress} label="Thinking" />
          </article>
        ))}
        <div ref={end} />
      </div>
      <form className="chat-form" onSubmit={(e) => void submit(e)}>
        <input
          className="ask-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask a question"
          aria-label="Ask a question"
          maxLength={1000}
          data-testid="chat-input"
        />
        <Button type="submit" variant="primary" disabled={busy || !text.trim()} testId="chat-send">
          Ask
        </Button>
      </form>
    </div>
  );
}
