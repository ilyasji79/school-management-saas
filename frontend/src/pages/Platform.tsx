import { useCallback, useEffect, useState } from "react";
import { api, ApiError, store } from "../api";
import { ErrorBanner, Field } from "../components/ui";

type SchoolRow = { id: string; code: string; name: string; plan: string; status: string; subscription_status: string; database_status: string | null; schema_revision: string | null; last_migration: string | null };
type PlanRow = { code: string; name: string; student_capacity: number | null; price_minor: number | null; currency: string; is_active: boolean };
type MigrationSummary = { head: string; upgraded: number; up_to_date: number; failed: { school: string; error: string }[] };

export default function Platform() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [schools, setSchools] = useState<SchoolRow[]>([]);
  const [plans, setPlans] = useState<PlanRow[]>([]);
  const [summary, setSummary] = useState<MigrationSummary | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const me = await api.platformGet<{ csrf: string }>("/api/platform/auth/me");
      store.set("platformCsrf", me.csrf);
      setSchools(await api.platformGet<SchoolRow[]>("/api/platform/schools"));
      setPlans(await api.platformGet<PlanRow[]>("/api/platform/plans"));
      setAuthed(true);
    } catch (e) { if (e instanceof ApiError && e.status === 401) setAuthed(false); else { setError(e); setAuthed(false); } }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function login(e: React.FormEvent) {
    e.preventDefault(); setError(null); setBusy(true);
    try { const r = await api.platformPost<{ csrf: string }>("/api/platform/auth/login", { email, password }); store.set("platformCsrf", r.csrf); await load(); }
    catch (err) { setError(err); } finally { setBusy(false); }
  }
  async function migrate() {
    setBusy(true); setError(null);
    try { setSummary(await api.platformPost<MigrationSummary>("/api/platform/migrations/run")); await load(); } catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function savePrice(code: string, value: string) {
    try { await api.platformPut(`/api/platform/plans/${code}`, { price_minor: value === "" ? null : Math.round(Number(value) * 100) }); await load(); } catch (e) { setError(e); }
  }

  if (authed === null) return <div className="wrap"><p>Loading…</p></div>;
  if (!authed) return (
    <div className="narrow"><form className="card" onSubmit={login}><h1>Platform console</h1><ErrorBanner error={error} />
      <Field id="pe" label="Email"><input id="pe" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
      <Field id="pp" label="Password"><input id="pp" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
      <button className="btn primary" disabled={busy}>Sign in</button> <a href="#/signin" className="muted">School sign in</a></form></div>);

  return (
    <main className="wrap">
      <h1>Platform console</h1>
      <ErrorBanner error={error} />
      <div className="card"><div className="row"><h2>Schools ({schools.length})</h2><button className="btn small" onClick={migrate} disabled={busy}>Run migrations on all schools</button></div>
        {summary && <div className={`banner ${summary.failed.length ? "err" : "ok"}`}>Schema head {summary.head}: {summary.upgraded} upgraded, {summary.up_to_date} already current, {summary.failed.length} failed.
          {summary.failed.length > 0 && <ul>{summary.failed.map((f) => <li key={f.school}>{f.school}: {f.error}</li>)}</ul>}</div>}
        <table><thead><tr><th>Code</th><th>Name</th><th>Plan</th><th>Status</th><th>Subscription</th><th>Database</th><th>Schema</th><th>Last migration</th></tr></thead><tbody>
          {schools.map((s) => <tr key={s.id}><td>{s.code}</td><td>{s.name}</td><td>{s.plan}</td><td><span className={`badge ${s.status === "active" ? "good" : "bad"}`}>{s.status}</span></td>
            <td>{s.subscription_status}</td><td>{s.database_status ?? "–"}</td><td>{s.schema_revision ?? "–"}</td><td>{s.last_migration ?? "–"}</td></tr>)}</tbody></table></div>
      <div className="card"><h2>Plans</h2><p className="muted">Prices are set here and nowhere else. Leave empty until decided.</p>
        <table><thead><tr><th>Plan</th><th>Capacity</th><th>Annual price ({plans[0]?.currency ?? "INR"})</th></tr></thead><tbody>
          {plans.map((p) => <PlanRowView key={p.code} plan={p} onSave={savePrice} />)}</tbody></table></div>
    </main>
  );
}

function PlanRowView({ plan, onSave }: { plan: PlanRow; onSave: (code: string, v: string) => void }) {
  const [v, setV] = useState(plan.price_minor == null ? "" : String(plan.price_minor / 100));
  return <tr><td>{plan.name}</td><td>{plan.student_capacity ?? "by contract"}</td>
    <td><div className="row"><input aria-label={`${plan.name} annual price`} type="number" min={0} value={v} onChange={(e) => setV(e.target.value)} /><button className="btn small" onClick={() => onSave(plan.code, v)}>Save</button></div></td></tr>;
}
