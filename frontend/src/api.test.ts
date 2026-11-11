import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, contentTypeOf, isValidKey, newKey, workspaceKey } from "./api";

describe("workspace key", () => {
  it("is 256 random bits, base64url, and stable", () => {
    const key = newKey();
    expect(key).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(newKey()).not.toBe(key);
    expect(isValidKey(key)).toBe(true);
    expect(isValidKey("short")).toBe(false);
    expect(workspaceKey()).toBe(workspaceKey());
  });
});

describe("content types", () => {
  it("trusts the browser type, then the extension", () => {
    expect(contentTypeOf({ name: "a.png", type: "image/png" })).toBe("image/png");
    expect(contentTypeOf({ name: "a.JPG", type: "" })).toBe("image/jpeg");
    expect(contentTypeOf({ name: "a.jpg", type: "image/jpg" })).toBe("image/jpeg");
    expect(contentTypeOf({ name: "scan.pdf", type: "" })).toBe("application/pdf");
    expect(contentTypeOf({ name: "a.heic", type: "image/heic" })).toBeNull();
  });
});

describe("api client", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sends the workspace key and parses JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], nextCursor: null })));
    vi.stubGlobal("fetch", fetchMock);
    await expect(api.list()).resolves.toEqual({ items: [], nextCursor: null });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/documents?limit=100");
    expect(init.headers["x-parchi-key"]).toBe(workspaceKey());
  });

  it("turns API errors into ApiError with the server's message", async () => {
    const body = { error: { code: "daily_limit", message: "This workspace has used all 20 uploads for today." } };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 429 })));
    const error = await api.stats().catch((caught) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(429);
    expect(error.code).toBe("daily_limit");
    expect(error.message).toContain("20 uploads");
  });

  it("returns nothing for 204", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(api.remove("01M759W60M6PQY1BJD7KKV6G8Q")).resolves.toBeUndefined();
  });
});
