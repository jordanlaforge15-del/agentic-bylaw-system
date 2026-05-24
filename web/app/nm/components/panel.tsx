"use client";

import type { ReactNode } from "react";

export function Panel({
  id,
  title,
  meta,
  right,
  children,
  flush,
  className = "",
  style,
}: {
  id?: string;
  title?: string;
  meta?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div className={`nm-panel ${className}`} style={style}>
      <span className="nm-tick-bl" />
      <span className="nm-tick-br" />
      {(title || id || meta || right) && (
        <div className="nm-panel-h">
          {id && <span className="nm-h-id">{id}</span>}
          {title && <span className="nm-h-title">{title}</span>}
          {meta && <span style={{ color: "var(--text-mute)" }}>{meta}</span>}
          {right && <span className="nm-h-meta">{right}</span>}
        </div>
      )}
      <div className={`nm-panel-b ${flush ? "nm-panel-b--flush" : ""}`}>
        {children}
      </div>
    </div>
  );
}
