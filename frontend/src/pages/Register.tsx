import { useEffect, useState } from "react";
import { api, ApiError, store, type Interval, type Meta } from "../api";
import { DAYS, ErrorBanner, Field } from "../components/ui";

type Form = {
  name: string; code: string; state: string; board: string; timezone: string;
  adminName: string; adminEmail: string; password: string; plan: string;
  workingDays: number[]; saturdaysOff: number[]; start: string; end: string;
  sessionMode: "single" | "two"; split: string; grace: string; halfDay: string;
  breaks: Interval[]; periods: Interval[];
};

const empty: Form = {
  name: "", code: "", state: "", board: "", timezone: "Asia/Kolkata", adminName: "", adminEmail: "", password: "", plan: "",
  workingDays: [], saturdaysOff: [], start: "", end: "", sessionMode: "single", split: "", grace: "", halfDay: "",
  breaks: [], periods: [],
};
const STEPS = ["School", "Administrator and plan", "School timing", "Review"];

function IntervalEditor({ label, items, onChange }: { label: string; items: Interval[]; onChange: (v: Interval[]) => void }) {
  const set = (i: number, patch: Partial<Interval>) => onChange(items.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  return (
    <div className="field">
      <span className="lbl">{label}</span>
      {items.map((it, i) => (
        <div className="list-edit" key={i}>
          <input aria-label={`${label} ${i + 1} name`} value={it.name} placeholder="Name" onChange={(e) => set(i, { name: e.target.value })} />
          <input aria-label={`${label} ${i + 1} start`} type="time" value={it.start} onChange={(e) => set(i, { start: e.target.value })} />
          <input aria-label={`${label} ${i + 1} end`} type="time" value={it.end} onChange={(e) => set(i, { end: e.target.value })} />
          <button type="button" className="btn small" onClick={() => onChange(items.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <div><button type="button" className="btn small" onClick={() => onChange([...items, { name: "", start: "", end: "" }])}>Add {label.toLowerCase()}</button></div>
    </div>
  );
}

export default function Register() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [form, setForm] = useState<Form>(empty);
  const [step, setStep] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const up = (patch: Partial<Form>) => { setProblems([]); setForm((f) => ({ ...f, ...patch })); };

  useEffect(() => { api.get<Meta>("/api/meta").then(setMeta).catch(setError); }, []);

  function check(s: number): string[] {
    const p: string[] = [];
    if (s === 0) {
      if (form.name.trim().length < 2) p.push("Enter the school name.");
      if (!/^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$/.test(form.code.trim().toLowerCase())) p.push("School code: 3-30 letters, digits or hyphens.");
      if (!form.state) p.push("Choose the state or union territory.");
      if (!form.board) p.push("Choose the board.");
    }
    if (s === 1) {
      if (form.adminName.trim().length < 2) p.push("Enter the administrator's name.");
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.adminEmail)) p.push("Enter a valid email.");
      if (form.password.length < 10 || !/[a-z]/.test(form.password) || !/[A-Z]/.test(form.password) || !/\d/.test(form.password))
        p.push("Password needs 10+ characters with upper case, lower case and a digit.");
      if (!form.plan) p.push("Choose a plan.");
    }
    if (s === 2) {
      if (!form.workingDays.length) p.push("Choose at least one working day.");
      if (!form.start || !form.end) p.push("Enter the school start and end times.");
      else if (form.end <= form.start) p.push("End time must be after the start time.");
      if (form.sessionMode === "two" && !form.split) p.push("Enter the time that splits the two sessions.");
      if (form.grace === "" || Number(form.grace) < 0) p.push("Enter the late grace in minutes (0 if none).");
    }
    return p;
  }

  function next() {
    const p = check(step);
    setProblems(p);
    if (!p.length) setStep(step + 1);
  }

  async function submit() {
    setBusy(true); setError(null);
    const interval = (i: Interval) => ({ name: i.name, start: i.start, end: i.end });
    try {
      const res = await api.post<{ code: string }>("/api/schools/register", {
        school: { name: form.name.trim(), code: form.code.trim().toLowerCase(), state: form.state, board: form.board, timezone: form.timezone },
        admin: { full_name: form.adminName.trim(), email: form.adminEmail.trim(), password: form.password },
        plan_code: form.plan,
        timing: {
          name: "Regular timing", working_days: form.workingDays, saturdays_off: form.workingDays.includes(5) ? form.saturdaysOff : [],
          start_time: form.start, end_time: form.end, session_mode: form.sessionMode,
          session_split_time: form.sessionMode === "two" ? form.split : null, late_grace_minutes: Number(form.grace),
          half_day_after: form.halfDay || null, breaks: form.breaks.map(interval), periods: form.periods.map(interval),
        },
      });
      store.set("schoolCode", res.code);
      setDone(res.code);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError("Could not register.", 0, "error"));
    } finally { setBusy(false); }
  }

  const toggle = (list: number[], n: number) => (list.includes(n) ? list.filter((x) => x !== n) : [...list, n].sort());

  if (done) {
    return (
      <div className="narrow"><div className="card">
        <h1>School created</h1>
        <div className="banner ok">Your workspace <strong>{done}</strong> is ready. Sign in with the administrator email and password you entered.</div>
        <a className="btn primary" href="#/signin">Go to sign in</a>
      </div></div>
    );
  }

  return (
    <div className="wrap">
      <h1>Register your school</h1>
      <p className="muted">Each school gets its own private database. Nothing about school hours is assumed: you enter yours below.</p>
      <div className="steps" aria-label="Progress">{STEPS.map((s, i) => <span key={s} className={i === step ? "on" : ""}>{i + 1}. {s}</span>)}</div>
      <ErrorBanner error={error} />
      {problems.length > 0 && <div className="banner err" role="alert"><ul>{problems.map((p) => <li key={p}>{p}</li>)}</ul></div>}

      <div className="card">
        {step === 0 && <>
          <Field id="name" label="School name"><input id="name" value={form.name} onChange={(e) => up({ name: e.target.value })} /></Field>
          <div className="grid2">
            <Field id="code" label="School code" hint="Used to sign in (letters, digits, hyphens)."><input id="code" value={form.code} onChange={(e) => up({ code: e.target.value })} /></Field>
            <Field id="tz" label="Timezone"><input id="tz" value={form.timezone} onChange={(e) => up({ timezone: e.target.value })} /></Field>
            <Field id="state" label="State / union territory"><select id="state" value={form.state} onChange={(e) => up({ state: e.target.value })}><option value="">Choose…</option>{meta?.states.map((s) => <option key={s}>{s}</option>)}</select></Field>
            <Field id="board" label="Education board"><select id="board" value={form.board} onChange={(e) => up({ board: e.target.value })}><option value="">Choose…</option>{meta?.boards.map((s) => <option key={s}>{s}</option>)}</select></Field>
          </div>
        </>}

        {step === 1 && <>
          <div className="grid2">
            <Field id="aname" label="Administrator name"><input id="aname" value={form.adminName} onChange={(e) => up({ adminName: e.target.value })} /></Field>
            <Field id="aemail" label="Administrator email"><input id="aemail" type="email" value={form.adminEmail} onChange={(e) => up({ adminEmail: e.target.value })} /></Field>
          </div>
          <Field id="apw" label="Password" hint="At least 10 characters with upper case, lower case and a digit."><input id="apw" type="password" value={form.password} onChange={(e) => up({ password: e.target.value })} autoComplete="new-password" /></Field>
          <Field id="plan" label="Plan"><select id="plan" value={form.plan} onChange={(e) => up({ plan: e.target.value })}>
            <option value="">Choose…</option>
            {meta?.plans.map((p) => <option key={p.code} value={p.code}>{p.name} – {p.student_capacity ? `up to ${p.student_capacity} students` : "capacity by contract"}{p.price_minor != null ? ` – ${(p.price_minor / 100).toLocaleString()} ${p.currency}/year` : " – price not set yet"}</option>)}
          </select></Field>
        </>}

        {step === 2 && <>
          <div className="field"><span className="lbl">Working days</span>
            <div className="days">{DAYS.map((d, i) => <label key={d} className="chk"><input type="checkbox" checked={form.workingDays.includes(i)} onChange={() => up({ workingDays: toggle(form.workingDays, i) })} />{d}</label>)}</div></div>
          {form.workingDays.includes(5) && <div className="field"><span className="lbl">Saturdays that are OFF <span className="hint">(leave empty if every Saturday is a working day)</span></span>
            <div className="days">{[1, 2, 3, 4, 5].map((n) => <label key={n} className="chk"><input type="checkbox" checked={form.saturdaysOff.includes(n)} onChange={() => up({ saturdaysOff: toggle(form.saturdaysOff, n) })} />{n}{["st", "nd", "rd", "th", "th"][n - 1]}</label>)}</div></div>}
          <div className="grid2">
            <Field id="start" label="School starts"><input id="start" type="time" value={form.start} onChange={(e) => up({ start: e.target.value })} /></Field>
            <Field id="end" label="School ends"><input id="end" type="time" value={form.end} onChange={(e) => up({ end: e.target.value })} /></Field>
            <Field id="mode" label="Attendance sessions"><select id="mode" value={form.sessionMode} onChange={(e) => up({ sessionMode: e.target.value as "single" | "two" })}><option value="single">One session a day</option><option value="two">Two sessions (morning and afternoon)</option></select></Field>
            {form.sessionMode === "two" && <Field id="split" label="Afternoon session begins at"><input id="split" type="time" value={form.split} onChange={(e) => up({ split: e.target.value })} /></Field>}
            <Field id="grace" label="Late grace (minutes)" hint="Arrival within this many minutes of the start is not late."><input id="grace" type="number" min={0} max={120} value={form.grace} onChange={(e) => up({ grace: e.target.value })} /></Field>
            <Field id="half" label="Half day if arriving after (optional)"><input id="half" type="time" value={form.halfDay} onChange={(e) => up({ halfDay: e.target.value })} /></Field>
          </div>
          <IntervalEditor label="Break" items={form.breaks} onChange={(breaks) => up({ breaks })} />
          <IntervalEditor label="Period" items={form.periods} onChange={(periods) => up({ periods })} />
          <p className="muted">Class-group timings and seasonal timings can be added later in school settings.</p>
        </>}

        {step === 3 && <div className="kv">
          <div>School</div><div>{form.name} ({form.code.toLowerCase()})</div>
          <div>Location and board</div><div>{form.state} · {form.board} · {form.timezone}</div>
          <div>Administrator</div><div>{form.adminName} · {form.adminEmail}</div>
          <div>Plan</div><div>{meta?.plans.find((p) => p.code === form.plan)?.name}</div>
          <div>Working days</div><div>{form.workingDays.map((d) => DAYS[d]).join(", ")}{form.saturdaysOff.length ? ` (Saturdays off: ${form.saturdaysOff.join(", ")})` : ""}</div>
          <div>Hours</div><div>{form.start} – {form.end} · {form.sessionMode === "two" ? `two sessions, afternoon from ${form.split}` : "one session"}</div>
          <div>Late / half day</div><div>{form.grace} min grace{form.halfDay ? ` · half day after ${form.halfDay}` : ""}</div>
          <div>Breaks / periods</div><div>{form.breaks.length} / {form.periods.length}</div>
        </div>}
      </div>

      <div className="row">
        {step > 0 && <button className="btn" onClick={() => { setProblems([]); setStep(step - 1); }}>Back</button>}
        {step < 3 && <button className="btn primary" onClick={next} disabled={!meta}>Continue</button>}
        {step === 3 && <button className="btn primary" onClick={submit} disabled={busy}>{busy ? "Creating your school…" : "Create school"}</button>}
        <a href="#/signin" className="muted">Already registered? Sign in</a>
      </div>
    </div>
  );
}
