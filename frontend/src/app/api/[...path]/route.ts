// Same-origin proxy to the FastAPI service. The target is read at REQUEST time
// (SCRIPTORIUM_API_ORIGIN), so one container image works in every deployment.
// In institutional deployments a reverse proxy (nginx/Caddy) may route /api
// directly to the API instead; both topologies are supported.

import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
]);

async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const origin = process.env.SCRIPTORIUM_API_ORIGIN ?? "http://127.0.0.1:8000";
  const { path } = await ctx.params;
  const target = new URL(`/api/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`, origin);

  const headers = new Headers();
  req.headers.forEach((v, k) => {
    if (!HOP_BY_HOP.has(k.toLowerCase())) headers.set(k, v);
  });
  headers.set("x-forwarded-host", req.headers.get("host") ?? "");
  headers.set("x-forwarded-proto", req.nextUrl.protocol.replace(":", ""));

  const hasBody = !["GET", "HEAD"].includes(req.method);
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? req.body : undefined,
      redirect: "manual",
      cache: "no-store",
      // @ts-expect-error — required by Node fetch for streamed request bodies
      duplex: hasBody ? "half" : undefined,
    });
  } catch (e) {
    return Response.json(
      { error: { code: "api_unreachable", message: `The SCRIPTORIUM API is unreachable (${(e as Error).message})`, details: null } },
      { status: 502 },
    );
  }

  const out = new Headers();
  upstream.headers.forEach((v, k) => {
    const key = k.toLowerCase();
    // Node fetch already decoded the body; drop encoding/length headers.
    if (!HOP_BY_HOP.has(key) && key !== "content-encoding" && key !== "set-cookie") out.set(k, v);
  });
  for (const c of upstream.headers.getSetCookie()) out.append("set-cookie", c);
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: out });
}

export { proxy as DELETE, proxy as GET, proxy as HEAD, proxy as OPTIONS, proxy as PATCH, proxy as POST, proxy as PUT };
