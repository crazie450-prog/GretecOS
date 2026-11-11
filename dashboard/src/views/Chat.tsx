// Chat (plan 7.14): the conversation as cards; any past card re-opens full size. Work handed to
// the deep lane (WO-2.4) shows under the question that started it, from queued to its result.
// Needs Clayton's board approval (no board yet, D92/D158).
import { type FormEvent, useEffect, useRef, useState } from "react";
import type { Answer, CoreState, DeepTask } from "../api/types";
import { Card } from "../cards/Card";
import type { Card as CardData } from "../cards/types.gen";
import { Button, StatusGlyph } from "../components/core";
import { deepLook, matchTasks } from "../state/deep";
import { GLYPH } from "../state/states";

/** A deep-lane task's state, its latest step while it works, and its result or error. */
export function DeepResult({ task }: { task: DeepTask }) {
  const look = deepLook(task.status);
  const step = task.progress.length ? task.progress[task.progress.length - 1] : null;
  return (
    <div className="deep-result" data-deep={task.id} data-status={task.status}>
      <StatusGlyph status={look.status} glyph={look.glyph} label={look.label} />
      {task.status === "running" && step && <p className="gos-src">{step}</p>}
      {task.text && (
        <>
          <p className="deep-text">{task.text}</p>
          <StatusGlyph status="warn" label="numbers not checked" />
        </>
      )}
      {task.error && <p className="gos-sub">{task.error}</p>}
    </div>
  );
}

type Entry = { kind: "answer"; at: number; answer: Answer } | { kind: "task"; at: number; task: DeepTask };

export function Chat(props: {
  answers: Answer[];
  deep: DeepTask[];
  core: CoreState | null;
  onAsk: (text: string) => Promise<void>;
  openCard: (card: CardData) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const asking = props.core?.asking ?? [];
  const { byAnswer, loose } = matchTasks(props.answers, props.deep);
  const entries: Entry[] = [
    ...props.answers.map((a): Entry => ({ kind: "answer", at: a.asked_ts, answer: a })),
    ...loose.map((t): Entry => ({ kind: "task", at: t.created, task: t })),
  ].sort((a, b) => a.at - b.at);
  // A block body: scrollIntoView may return a promise, which React would take for a cleanup.
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [entries.length, asking.length]);

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
        {entries.length === 0 && asking.length === 0 && (
          <p className="empty">{GLYPH.todo} No conversation yet. Ask about a recipe, an item, a machine or a quest.</p>
        )}
        {entries.map((e) => {
          if (e.kind === "task") {
            return (
              <article key={e.task.id} className="exchange" data-task={e.task.id}>
                <p className="question">
                  <span className="gos-src">deep lane, {e.task.source}</span> "{e.task.request}"
                </p>
                <DeepResult task={e.task} />
              </article>
            );
          }
          const a = e.answer;
          const task = byAnswer.get(a.id);
          return (
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
                {task && <DeepResult task={task} />}
              </div>
            </article>
          );
        })}
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
