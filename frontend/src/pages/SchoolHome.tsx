import { useCallback, useEffect, useState } from "react";
import { api, ApiError, store, type Me, type Timing } from "../api";
import { ErrorBanner, Field, fmtDays } from "../components/ui";

type UserRow = { id: string; email: string; full_name: string; is_active: boolean; roles: string[] };
type Audit = { total: number; items: { id: number; action: string; entity: string; created_at: string }[] };

export default function SchoolHome() {
  const [me, setMe] = useState<Me | null>(null);
  const [tab, setTab] = useState("overview");
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      const m = await api.get<Me>("/api/auth/me");
      store.set("csrf", m.csrf);
      setMe(m);
    } catch (e) {
      if (e instanceof ApiError && (e.status === 401 || e.status === 404)) { location.hash = "#/signin"; return; }
      setError(e);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function logout() {
    try { await api.post("/api/auth/logout"); } catch { /* already signed out */ }
    store.clear();
    location.hash = "#/signin";
  }

  if (!me) return <div className="wrap"><ErrorBanner error={error} />{!error && <p>Loading…</p>}</div>;
  const can = (p: string) => me.permissions.includes(p);
  const tabs = [["overview", "Overview"], ["timing", "School timing", can("settings.read")], ["users", "Users", can("users.read")], ["audit", "Audit log", can("audit.read")]] as const;

  return (
    <>
      <header className="top">
        <span className="brand">{me.school.name}</span>
        <nav aria-label="Sections">{tabs.filter((t) => t[2] !== false).map(([k, label]) => <button key={k} aria-current={tab === k ? "page" : undefined} onClick={() => setTab(k)}>{label}</button>)}</nav>
        <span>{me.user.full_name}</span><button className="btn small" onClick={logout}>Sign out</button>
      </header>
      <main className="wrap">
        {tab === "overview" && <div className="card"><h2>Your account</h2><div className="kv">
          <div>Name</div><div>{me.user.full_name}</div><div>Email</div><div>{me.user.email}</div>
          <div>Roles</div><div>{me.roles.join(", ")}</div><div>School</div><div>{me.school.name} · {me.school.state} · {me.school.board}</div>
          <div>Timezone</div><div>{me.school.timezone}</div><div>Permissions</div><div>{me.permissions.join(", ") || "none"}</div></div></div>}
        {tab === "timing" && <TimingTab />}
        {tab === "users" && <UsersTab canCreate={can("users.create") && can("users.manage_roles")} />}
        {tab === "audit" && <AuditTab />}
      </main>
    </>
  );
}

function TimingTab() {
  const [profiles, setProfiles] = useState<Timing[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { api.get<{ timing_profiles: Timing[] }>("/api/school/settings").then((d) => setProfiles(d.timing_profiles)).catch(setError); }, []);
  return <>
    <ErrorBanner error={error} />
    {profiles?.map((p) => <div className="card" key={p.name}><h2>{p.name}</h2><div className="kv">
      <div>Working days</div><div>{fmtDays(p.working_days)}{p.saturdays_off.length ? ` (Saturdays off: ${p.saturdays_off.join(", ")})` : ""}</div>
      <div>Hours</div><div>{p.start_time} – {p.end_time}</div>
      <div>Sessions</div><div>{p.session_mode === "two" ? `Two (afternoon from ${p.session_split_time})` : "One"}</div>
      <div>Late grace</div><div>{p.late_grace_minutes} minutes</div><div>Half day after</div><div>{p.half_day_after ?? "not set"}</div>
      <div>Breaks</div><div>{p.breaks.map((b) => `${b.name} ${b.start}–${b.end}`).join(" · ") || "none"}</div>
      <div>Periods</div><div>{p.periods.map((b) => `${b.name} ${b.start}–${b.end}`).join(" · ") || "none"}</div></div></div>)}
    {profiles && !profiles.length && <p>No timing profile yet.</p>}
  </>;
}

function UsersTab({ canCreate }: { canCreate: boolean }) {
  const [rows, setRows] = useState<UserRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [form, setForm] = useState({ full_name: "", email: "", password: "", role: "teacher" });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const load = useCallback(() => { api.get<UserRow[]>("/api/school/users").then(setRows).catch(setError); }, []);
  useEffect(load, [load]);

  async function create(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError(null); setNote("");
    try {
      await api.post("/api/school/users", { full_name: form.full_name, email: form.email, password: form.password, roles: [form.role] });
      setNote(`User ${form.email} created.`); setForm({ full_name: "", email: "", password: "", role: "teacher" }); load();
    } catch (err) { setError(err); } finally { setBusy(false); }
  }
  return <>
    <ErrorBanner error={error} />
    {note && <div className="banner ok">{note}</div>}
    <div className="card"><h2>Users</h2>
      <table><thead><tr><th>Name</th><th>Email</th><th>Roles</th><th>Status</th></tr></thead><tbody>
        {rows?.map((u) => <tr key={u.id}><td>{u.full_name}</td><td>{u.email}</td><td>{u.roles.join(", ")}</td><td><span className={`badge ${u.is_active ? "good" : "bad"}`}>{u.is_active ? "active" : "disabled"}</span></td></tr>)}
      </tbody></table></div>
    {canCreate && <form className="card" onSubmit={create}><h2>Add a user</h2><div className="grid2">
      <Field id="un" label="Full name"><input id="un" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required /></Field>
      <Field id="ue" label="Email"><input id="ue" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required /></Field>
      <Field id="up" label="Temporary password" hint="10+ characters, upper and lower case, a digit."><input id="up" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required autoComplete="new-password" /></Field>
      <Field id="ur" label="Role"><select id="ur" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
        {["school_admin", "principal", "teacher", "accountant", "attendance_operator", "social_media_manager"].map((r) => <option key={r}>{r}</option>)}</select></Field></div>
      <button className="btn primary" disabled={busy}>{busy ? "Saving…" : "Create user"}</button></form>}
  </>;
}

function AuditTab() {
  const [data, setData] = useState<Audit | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { api.get<Audit>("/api/school/audit-logs?per_page=50").then(setData).catch(setError); }, []);
  return <><ErrorBanner error={error} /><div className="card"><h2>Audit log {data ? `(${data.total})` : ""}</h2>
    <table><thead><tr><th>When</th><th>Action</th><th>Entity</th></tr></thead><tbody>
      {data?.items.map((a) => <tr key={a.id}><td>{new Date(a.created_at).toLocaleString()}</td><td>{a.action}</td><td>{a.entity}</td></tr>)}</tbody></table></div></>;
}
