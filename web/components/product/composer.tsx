// Bottom of the center pane. Suggestion chips on top, a heavy-bordered
// textarea + accent send button below, then a help row in mono caption.
// Enter sends; shift+enter inserts a newline.

"use client";

import { useState } from "react";
import { Mono } from "@/components/mono";
import { SUGGESTED_PROMPTS } from "@/lib/mock";

type Props = {
  onSend: (text: string) => void;
  disabled?: boolean;
};

export function Composer({ onSend, disabled }: Props) {
  const [val, setVal] = useState("");

  const submit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (val.trim() && !disabled) {
      onSend(val);
      setVal("");
    }
  };

  return (
    <div className="border-t border-hair bg-surface px-9 pt-3.5 pb-4.5">
      <div className="flex gap-1.5 mb-2.5 flex-wrap">
        {SUGGESTED_PROMPTS.map((s) => (
          <button
            key={s}
            type="button"
            disabled={disabled}
            onClick={() => onSend(s)}
            className="bg-surface-alt border border-hair text-text font-sans cursor-pointer"
            style={{
              padding: "6px 10px",
              fontSize: 12,
              letterSpacing: "-0.005em",
            }}
          >
            {s}
          </button>
        ))}
      </div>
      <form
        onSubmit={submit}
        className="flex bg-surface"
        style={{ border: "1.5px solid var(--text)" }}
      >
        <textarea
          value={val}
          onChange={(e) => setVal(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) submit(e);
          }}
          placeholder="Ask about this parcel — yard, height, use, density…"
          rows={1}
          disabled={disabled}
          className="flex-1 resize-none bg-transparent text-text font-sans outline-none"
          style={{
            padding: "12px 14px",
            fontSize: 14,
            letterSpacing: "-0.005em",
            minHeight: 44,
          }}
        />
        <button
          type="submit"
          disabled={disabled || !val.trim()}
          className="bg-text text-surface font-sans font-bold cursor-pointer"
          style={{
            padding: "0 22px",
            fontSize: 14,
            letterSpacing: "-0.01em",
            opacity: disabled || !val.trim() ? 0.5 : 1,
            cursor: disabled ? "not-allowed" : "pointer",
          }}
        >
          Send →
        </button>
      </form>
      <div className="flex justify-between items-center mt-2">
        <Mono muted size={9.5}>
          ENTER TO SEND · SHIFT+ENTER FOR NEWLINE
        </Mono>
        <Mono muted size={9.5}>
          NOT LEGAL ADVICE · VERIFY WITH HRM PLANNING
        </Mono>
      </div>
    </div>
  );
}
