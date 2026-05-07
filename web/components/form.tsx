// Form atoms — Field, TextArea, Select. All share the heavy-bordered
// "blueprint" input style: 1.5px solid text border, sharp corners,
// uppercase mono label above. Used by /login and /signup.

"use client";

import { Mono } from "./mono";

const inputClass =
  "px-3.5 py-3 bg-surface text-text font-sans text-[14px] outline-none tracking-[-0.005em] " +
  "border-[1.5px] border-text focus:border-accent-ink";

type FieldProps = {
  label: string;
  type?: string;
  placeholder?: string;
  value: string;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  hint?: string;
  required?: boolean;
  autoComplete?: string;
};

export function Field({
  label,
  type = "text",
  placeholder,
  value,
  onChange,
  hint,
  required,
  autoComplete,
}: FieldProps) {
  return (
    <label className="flex flex-col gap-1.5">
      <Mono muted size={10}>
        {label}
      </Mono>
      <input
        type={type}
        placeholder={placeholder}
        value={value}
        onChange={onChange}
        required={required}
        autoComplete={autoComplete}
        className={inputClass}
      />
      {hint && <span className="text-[11.5px] text-text-muted">{hint}</span>}
    </label>
  );
}

type TextAreaProps = {
  label: string;
  placeholder?: string;
  value: string;
  onChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
  rows?: number;
  required?: boolean;
};

export function TextArea({
  label,
  placeholder,
  value,
  onChange,
  rows = 4,
  required,
}: TextAreaProps) {
  return (
    <label className="flex flex-col gap-1.5">
      <Mono muted size={10}>
        {label}
      </Mono>
      <textarea
        placeholder={placeholder}
        value={value}
        onChange={onChange}
        rows={rows}
        required={required}
        className={`${inputClass} resize-y`}
      />
    </label>
  );
}

type SelectProps = {
  label: string;
  options: string[];
  value: string;
  onChange: (e: React.ChangeEvent<HTMLSelectElement>) => void;
};

export function Select({ label, options, value, onChange }: SelectProps) {
  return (
    <label className="flex flex-col gap-1.5">
      <Mono muted size={10}>
        {label}
      </Mono>
      <select value={value} onChange={onChange} className={inputClass}>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}
