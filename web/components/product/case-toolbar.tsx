// ABS-344 — CaseToolbar: the strip that wraps the /app center pane and
// makes "report" and "conversation" two faces of the SAME case workspace.
//
// The spec's core premise is that a purchased report and the follow-up
// conversation about the same property are one workspace, not two routes.
// This toolbar carries:
//   - left:  a segmented `Report · Conversation` toggle. Shown only when
//            the case is report-backed (`showToggle`); a conversation-only
//            case has nothing to toggle to, so the toolbar hides it.
//   - right: the report's Share / Export actions, rendered only while the
//            report face is showing (`view === "report"`) since they act on
//            the report document.
//
// Presentational only — the parent (`chat-shell`) owns the `view` state,
// the report lifecycle, and what each action does. When there's no toggle
// and no report actions to show, the component renders nothing so a plain
// conversation case keeps its original chrome.

"use client";

import { cn } from "@/lib/cn";
import { Mono } from "@/components/mono";

export type CaseView = "report" | "conversation";

type Props = {
  view: CaseView;
  onToggle: (view: CaseView) => void;
  // Whether this case has a report to toggle to. False for conversation-only
  // cases, which hide the segmented control entirely (spec).
  showToggle: boolean;
  // Report-document actions (Share / Export PDF). Rendered on the right only
  // while the report face is showing.
  onShare?: () => void;
  onExport?: () => void;
};

export function CaseToolbar({
  view,
  onToggle,
  showToggle,
  onShare,
  onExport,
}: Props) {
  const showActions = view === "report" && (onShare || onExport);
  // Nothing to show → render nothing so plain conversation cases are
  // visually unchanged.
  if (!showToggle && !showActions) return null;

  return (
    <div
      data-testid="case-toolbar"
      className="border-b border-hair bg-surface flex items-center justify-between gap-3 px-4 sm:px-6 py-2"
    >
      {showToggle ? (
        <div
          role="tablist"
          aria-label="Report or conversation"
          className="inline-flex border border-hair bg-surface-alt"
        >
          <Segment
            active={view === "report"}
            onClick={() => onToggle("report")}
            testId="case-toolbar-report"
          >
            Report
          </Segment>
          <Segment
            active={view === "conversation"}
            onClick={() => onToggle("conversation")}
            testId="case-toolbar-conversation"
          >
            Conversation
          </Segment>
        </div>
      ) : (
        <span />
      )}

      {showActions && (
        <div className="flex items-center gap-1.5">
          {onShare && (
            <Action onClick={onShare} testId="case-toolbar-share">
              Share
            </Action>
          )}
          {onExport && (
            <Action onClick={onExport} testId="case-toolbar-export">
              Export PDF
            </Action>
          )}
        </div>
      )}
    </div>
  );
}

function Segment({
  active,
  onClick,
  testId,
  children,
}: {
  active: boolean;
  onClick: () => void;
  testId: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      data-testid={testId}
      data-active={active ? "true" : "false"}
      onClick={onClick}
      className={cn(
        "cursor-pointer border-none px-3 py-1.5 transition-colors",
        active ? "bg-text" : "bg-transparent",
      )}
    >
      <Mono
        size={10}
        className={active ? "!text-surface" : "!text-text-muted"}
      >
        {children}
      </Mono>
    </button>
  );
}

function Action({
  onClick,
  testId,
  children,
}: {
  onClick: () => void;
  testId: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className="cursor-pointer bg-transparent border border-hair px-2.5 py-1.5 text-text hover:bg-surface-alt transition-colors"
    >
      <Mono size={9.5} muted>
        {children}
      </Mono>
    </button>
  );
}
