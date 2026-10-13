let csrf = "";
const base = import.meta.env.VITE_API_BASE || "";

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) {
    super(message);
  }
}

export function setCsrf(value: string) { csrf = value; }

export async function api<T = any>(path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(base + "/api" + path, {
    method, credentials: "include",
    headers: { "Content-Type": "application/json", ...(method !== "GET" ? { "X-CSRF-Token": csrf } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await res.json();
  if (!res.ok) throw new ApiError(
    data.error?.code || "REQUEST_FAILED",
    data.error?.message || (typeof data.detail === "string" ? data.detail : "请求未通过验证"),
    res.status,
  );
  return data;
}
