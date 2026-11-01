import type { ReactNode } from "react";
import { ApiError } from "../api";

export function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

export function ErrorBanner({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error instanceof ApiError ? error : null;
  return (
    <div className="banner err" role="alert">
      {e ? e.message : "Something went wrong."}
      {e && e.fields.length > 0 && (
        <ul>{e.fields.map((f, i) => <li key={i}>{f.field ? `${f.field}: ` : ""}{f.message}</li>)}</ul>
      )}
    </div>
  );
}

export const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const fmtDays = (d: number[]) => d.map((x) => DAYS[x].slice(0, 3)).join(", ");
