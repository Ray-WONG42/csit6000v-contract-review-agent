import { getLanguage, t } from "./i18n";
let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}
export async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const multipart = body instanceof FormData;
  if (body && !multipart && typeof body === "object" && (path.includes("/jobs") || path === "/assistant/runs" || path.startsWith("/auth/"))) body = { ...body, language: getLanguage() };
  const response = await fetch("/api" + path, {
    method,
    credentials: "same-origin",
    headers: {
      ...(multipart ? {} : { "Content-Type": "application/json" }),
      "X-CSRF-Token": csrf,
      "Accept-Language": getLanguage(),
    },
    body:
      body === undefined ? undefined : multipart ? body as FormData : JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response
      .json()
      .catch(() => ({ detail: "服务连接失败" }));
    throw new Error(t(
      typeof data.detail === "string"
        ? data.detail
        : "输入格式有误，请检查必填项",
    ));
  }
  return response.json();
}
export const fileUrl = (id: string, download = false) =>
  "/api/files/" + encodeURIComponent(id) + (download ? "?download=true" : "");
