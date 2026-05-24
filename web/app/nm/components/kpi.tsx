"use client";

import type { ReactNode } from "react";

export function KPI({
  label,
  value,
  unit,
  tone,
  hint,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  tone?: string;
  hint?: string;
}) {
  return (
    <div className={`nm-kpi ${tone ? `nm-kpi--${tone}` : ""}`}>
      <div className="nm-kpi__lbl">{label}</div>
      <div className="nm-kpi__val">
        {value}
        {unit && <span className="nm-kpi__unit">{unit}</span>}
      </div>
      {hint && <div className="nm-kpi__hint">{hint}</div>}
    </div>
  );
}
