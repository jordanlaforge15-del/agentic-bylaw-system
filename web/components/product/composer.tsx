// Bottom of the center pane. Suggestion chips on top, a heavy-bordered
// textarea + accent send button below, then a help row in mono caption.
// Enter sends; shift+enter inserts a newline.
//
// Responsive contract:
//   - Mobile: chips scroll horizontally instead of wrapping (so the
//     row stays one line and doesn't push the textarea below the
//     fold). Help text simplifies to a single mono caption.
//   - The whole composer is sticky to the bottom of its scroll
//     container and translates up by --abs-keyboard-inset (set by
//     useKeyboardInset in app/page.tsx) when the iOS keyboard opens.
//   - Send button shows a "→" only on mobile (more compact) and
//     "Send →" at sm+.

"use client";

import { useRef, useState } from "react";
import { Mono } from "@/components/mono";
import { SUGGESTED_PROMPTS } from "@/lib/mock";

type Props = {
  onSend: (text: string) => void;
  disabled?: boolean;
};

export function Composer({ onSend, disabled }: Props) {
  const [val, setVal] = useState("");
  const [focused, setFocused] = useState(false);
  // Source-of-truth for the submitted text is the DOM, not React state.
  // Mobile-WebKit (and any environment where keydown can race ahead of a
  // pending input-state commit) would otherwise read a stale empty `val`
  // from the closure and silently drop the send — see ABS-26.
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const submit = (e?: React.FormEvent | React.KeyboardEvent) => {
    e?.preventDefault();
    const text = textareaRef.current?.value ?? val;
    if (text.trim() && !disabled) {
      onSend(text);
      setVal("");
    }
  };

  return (
    <div
      className="border-t border-hair bg-surface px-4 sm:px-7 lg:px-9 pt-3 sm:pt-3.5 pb-3 sm:pb-4 lg:pb-4.5 safe-pb sticky bottom-0 z-10"
      style={{
        // Lift the composer above the iOS soft keyboard. Variable is
        // 0 on desktops (and unset → falls back to 0).
        transform: "translateY(calc(-1 * var(--abs-keyboard-inset, 0px)))",
      }}
    >
      {/* Hide chips on mobile when the textarea has focus — gives the
       * keyboard more room and reduces visual noise mid-compose. */}
      <div
        className={`
          ${focused ? "hidden sm:flex" : "flex"}
          gap-1.5 mb-2 sm:mb-2.5
          overflow-x-auto sm:flex-wrap
          -mx-4 px-4 sm:mx-0 sm:px-0
          [scrollbar-width:none] [&::-webkit-scrollbar]:hidden
        `}
      >
        {SUGGESTED_PROMPTS.map((s) => (
          <button
            key={s}
            type="button"
            disabled={disabled}
            onClick={() => onSend(s)}
            className="bg-surface-alt border border-hair text-text font-sans cursor-pointer flex-shrink-0 px-2.5 sm:px-3 py-1.5 text-[11.5px] sm:text-[12px] whitespace-nowrap"
            style={{ letterSpacing: "-0.005em" }}
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
          ref={textareaRef}
          value={val}
          onChange={(e) => setVal(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) submit(e);
          }}
          placeholder="Ask about this parcel — yard, height, use, density…"
          rows={1}
          disabled={disabled}
          className="flex-1 min-w-0 resize-none bg-transparent text-text font-sans outline-none px-3 sm:px-3.5 py-2.5 sm:py-3 text-[14px] min-h-[44px]"
          style={{ letterSpacing: "-0.005em" }}
        />
        <button
          type="submit"
          disabled={disabled || !val.trim()}
          aria-label="Send"
          className="bg-text text-surface font-sans font-bold cursor-pointer px-4 sm:px-5 lg:px-[22px] text-[14px] flex-shrink-0"
          style={{
            letterSpacing: "-0.01em",
            opacity: disabled || !val.trim() ? 0.5 : 1,
            cursor: disabled ? "not-allowed" : "pointer",
          }}
        >
          <span className="sm:hidden">→</span>
          <span className="hidden sm:inline">Send →</span>
        </button>
      </form>
      <div className="flex justify-between items-center mt-1.5 sm:mt-2 gap-2">
        <Mono muted size={9.5} className="hidden sm:block">
          ENTER TO SEND · SHIFT+ENTER FOR NEWLINE
        </Mono>
        <Mono muted size={9} className="sm:hidden">
          NOT LEGAL ADVICE
        </Mono>
        <Mono muted size={9.5} className="hidden sm:block">
          NOT LEGAL ADVICE · VERIFY WITH HRM PLANNING
        </Mono>
      </div>
    </div>
  );
}
