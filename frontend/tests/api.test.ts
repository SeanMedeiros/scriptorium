import { api, ApiError, getAccessToken, setAccessToken } from "@/lib/api";
import { formatPeriod, parseLanguages } from "@/lib/utils";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("api client", () => {
  beforeEach(() => setAccessToken(null));

  it("sends the bearer token and JSON body", async () => {
    setAccessToken("tok-1");
    const fetchImpl = vi.fn(async () => json(200, { ok: true }));
    await api("/projects", { method: "POST", json: { title: "X" }, fetchImpl });
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/projects");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok-1");
    expect(init.body).toBe('{"title":"X"}');
  });

  it("refreshes once on 401 and retries with the new token", async () => {
    setAccessToken("expired");
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(json(401, { error: { code: "unauthorized", message: "expired" } }))
      .mockResolvedValueOnce(json(200, { access_token: "fresh" }))
      .mockResolvedValueOnce(json(200, { id: "me" }));
    const out = await api<{ id: string }>("/me", { fetchImpl });
    expect(out.id).toBe("me");
    expect(getAccessToken()).toBe("fresh");
    expect(fetchImpl.mock.calls[1][0]).toBe("/api/v1/auth/refresh");
    expect(new Headers(fetchImpl.mock.calls[2][1].headers).get("Authorization")).toBe("Bearer fresh");
  });

  it("surfaces structured API errors and clears the token when refresh fails", async () => {
    setAccessToken("expired");
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(json(401, { error: { code: "unauthorized", message: "expired" } }))
      .mockResolvedValueOnce(json(401, { error: { code: "invalid_session", message: "revoked" } }));
    await expect(api("/me", { fetchImpl })).rejects.toMatchObject({ status: 401, body: { code: "unauthorized" } });
    expect(getAccessToken()).toBeNull();
  });

  it("does not try to refresh for auth endpoints", async () => {
    const fetchImpl = vi.fn(async () => json(401, { error: { code: "invalid_credentials", message: "bad" } }));
    const err = await api("/auth/login", { method: "POST", json: {}, fetchImpl }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("invalid_credentials");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});

describe("utils", () => {
  it("parses ISO 639-3 language lists", () => {
    expect(parseLanguages("por, LAT;fro  spa")).toEqual(["por", "lat", "fro", "spa"]);
  });
  it("formats historical periods", () => {
    expect(formatPeriod(1600, 1699)).toBe("1600–1699");
    expect(formatPeriod(1600, null)).toBe("1600–");
    expect(formatPeriod(null, null)).toBeNull();
  });
});
