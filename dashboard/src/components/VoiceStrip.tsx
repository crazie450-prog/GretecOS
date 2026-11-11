// The voice strip (plan 7.14): the core art as the orb with its standby ring, what the voice is
// doing, the last answer's words and, text first (Phase 2), a box to type a question.
import { type FormEvent, useState } from "react";
import type { Answer, VoiceState } from "../api/types";
import type { Slug } from "../theme/themes";
import { CoreArt, type VoiceLook } from "./CoreArt";

export function voiceLook(voice: VoiceState | null, asking: boolean): VoiceLook {
  if (voice === "listening" || voice === "speaking") return voice;
  if (voice === "thinking" || asking) return "thinking";
  if (voice === "off") return "off";
  return "standby";
}

const SAY: Record<VoiceLook, string> = {
  standby: "Standby · say the wake word, or type",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  off: "Voice off · type a question",
};

export function VoiceStrip(props: {
  slug: Slug;
  look: VoiceLook;
  motion: boolean;
  last: Answer | null;
  glance: boolean;
  onAsk: (text: string) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
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
    <footer className="gos-rail voice-strip" data-testid="voice-strip" data-voice={props.look}>
      <CoreArt slug={props.slug} state={props.look} size={props.glance ? 72 : 60} motion={props.motion} />
      <div className="voice-state">
        <span className="gos-label">{SAY[props.look]}</span>
        {props.last && <span className="last-line">{props.last.text}</span>}
      </div>
      {!props.glance && (
        <form className="ask-form" onSubmit={(e) => void submit(e)}>
          <input
            className="ask-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Ask: recipe for steel, what uses tin, what quests can I do"
            aria-label="Ask"
            maxLength={1000}
            data-testid="ask-input"
          />
          <button type="submit" className="gos-btn primary" disabled={busy || !text.trim()} data-testid="ask-send">
            Ask
          </button>
        </form>
      )}
    </footer>
  );
}
