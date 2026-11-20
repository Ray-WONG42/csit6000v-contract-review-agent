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
  const response = await fetch("/api" + path, {
    method,
    credentials: "same-origin",
    headers: {
      ...(multipart ? {} : { "Content-Type": "application/json" }),
      "X-CSRF-Token": csrf,
    },
    body:
      body === undefined ? undefined : multipart ? body : JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response
      .json()
      .catch(() => ({ detail: "服务连接失败" }));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "输入格式有误，请检查必填项",
    );
  }
  return response.json();
}
export const fileUrl = (id: string, download = false) =>
  "/api/files/" + encodeURIComponent(id) + (download ? "?download=true" : "");
