/**
 * Proxy to the backend admin API. Forwards the method, body and Authorization header only;
 * the backend checks the token on every call.
 */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const API = (process.env.ADMIN_API_URL ?? "http://127.0.0.1:8200").replace(/\/$/, "");

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const url = `${API}/api/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers: Record<string, string> = {};
  const auth = req.headers.get("authorization");
  if (auth) headers.authorization = auth;
  const type = req.headers.get("content-type");
  if (type) headers["content-type"] = type;
  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  try {
    const res = await fetch(url, {
      method: req.method,
      headers,
      body: hasBody ? await req.text() : undefined,
      cache: "no-store",
    });
    return new Response(await res.text(), {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return Response.json({ detail: "admin_api_unreachable" }, { status: 502 });
  }
}

export { forward as GET, forward as POST, forward as PUT, forward as DELETE };
