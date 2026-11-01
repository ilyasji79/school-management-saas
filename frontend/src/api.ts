export class ApiError extends Error {
  constructor(message: string, public status: number, public code: string, public fields: { field: string; message: string }[] = []) {
    super(message);
  }
}

const store = {
  get: (k: string) => sessionStorage.getItem(k) ?? "",
  set: (k: string, v: string) => sessionStorage.setItem(k, v),
  clear: () => { ["schoolCode", "csrf", "platformCsrf"].forEach((k) => sessionStorage.removeItem(k)); },
};
export { store };

async function request<T>(method: string, url: string, body?: unknown, platform = false): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (!platform) {
    const code = store.get("schoolCode");
    if (code) headers["X-School-Code"] = code;
    const csrf = store.get("csrf");
    if (csrf && method !== "GET") headers["X-CSRF-Token"] = csrf;
  } else {
    const csrf = store.get("platformCsrf");
    if (csrf && method !== "GET") headers["X-CSRF-Token"] = csrf;
  }
  let res: Response;
  try {
    res = await fetch(url, { method, headers, credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError("Cannot reach the server. Check your connection and try again.", 0, "network");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const e = data?.error ?? {};
    throw new ApiError(e.message ?? `Request failed (${res.status})`, res.status, e.code ?? "error", e.fields ?? []);
  }
  return data as T;
}

export const api = {
  get: <T,>(url: string) => request<T>("GET", url),
  post: <T,>(url: string, body?: unknown) => request<T>("POST", url, body ?? {}),
  platformGet: <T,>(url: string) => request<T>("GET", url, undefined, true),
  platformPost: <T,>(url: string, body?: unknown) => request<T>("POST", url, body ?? {}, true),
  platformPut: <T,>(url: string, body?: unknown) => request<T>("PUT", url, body ?? {}, true),
};

export type Plan = { code: string; name: string; student_capacity: number | null; price_minor: number | null; currency: string };
export type Meta = { states: string[]; boards: string[]; plans: Plan[] };
export type Interval = { name: string; start: string; end: string };
export type Timing = {
  name: string; working_days: number[]; saturdays_off: number[]; start_time: string; end_time: string;
  session_mode: "single" | "two"; session_split_time: string | null; late_grace_minutes: number;
  half_day_after: string | null; breaks: Interval[]; periods: Interval[]; effective_from?: string;
};
export type Me = {
  user: { id: string; email: string; full_name: string }; roles: string[]; permissions: string[]; csrf: string;
  school: { code: string; name: string; timezone: string; state: string; board: string };
};
