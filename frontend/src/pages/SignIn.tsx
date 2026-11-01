import { useState } from "react";
import { api, ApiError, store, type Me } from "../api";
import { ErrorBanner, Field } from "../components/ui";

export default function SignIn() {
  const [code, setCode] = useState(store.get("schoolCode"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      store.set("schoolCode", code.trim().toLowerCase());
      const res = await api.post<{ csrf: string }>("/api/auth/login", { email, password });
      store.set("csrf", res.csrf);
      location.hash = "#/school";
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError("Could not sign in.", 0, "error"));
    } finally { setBusy(false); }
  }

  return (
    <div className="narrow">
      <form className="card" onSubmit={submit} noValidate>
        <h1>School sign in</h1>
        <p className="muted">Enter your school code, then your email and password.</p>
        <ErrorBanner error={error} />
        <Field id="code" label="School code"><input id="code" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="organization" required /></Field>
        <Field id="email" label="Email"><input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required /></Field>
        <Field id="password" label="Password"><input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /></Field>
        <button className="btn primary" disabled={busy || !code || !email || !password}>{busy ? "Signing in…" : "Sign in"}</button>
        <p className="muted">New school? <a href="#/register">Register your school</a> · <a href="#/platform">Platform console</a></p>
      </form>
    </div>
  );
}
export type { Me };
