import { describe, expect, it } from "vitest";
import { displayDate, duration, groupIndian, money, monthLabel, relativeTime, shortMoney, stageOf } from "./format";
import { parseHash } from "./hooks";

describe("money", () => {
  it("groups rupees the Indian way", () => {
    expect(groupIndian(123456.5)).toBe("1,23,456.50");
    expect(groupIndian(1234567.891)).toBe("12,34,567.89");
    expect(groupIndian(999)).toBe("999.00");
    expect(money(607.42)).toBe("₹607.42");
    expect(money(null)).toBe("—");
    expect(money(1234.5, "USD")).toBe("$1,234.50");
  });

  it("abbreviates lakhs and crores", () => {
    expect(shortMoney(191200)).toBe("₹1.9L");
    expect(shortMoney(21533.82)).toBe("₹21.5k");
    expect(shortMoney(25000000)).toBe("₹2.5Cr");
    expect(shortMoney(210)).toBe("₹210");
  });
});

describe("dates and durations", () => {
  it("prints bill dates without shifting time zones", () => {
    expect(displayDate("2026-11-08")).toBe("8 Nov 2026");
    expect(displayDate(undefined)).toBe("—");
    expect(monthLabel("2026-07")).toBe("Jul 26");
  });

  it("formats durations and relative times", () => {
    expect(duration(526)).toBe("526 ms");
    expect(duration(2150)).toBe("2.1 s");
    expect(duration(null)).toBe("—");
    const now = Date.parse("2026-11-11T12:00:00Z");
    expect(relativeTime("2026-11-11T11:58:00Z", now)).toBe("2m ago");
    expect(relativeTime("2026-11-11T11:59:58Z", now)).toBe("just now");
  });
});

describe("stages and routes", () => {
  it("shows an uploaded document as queued, not waiting", () => {
    expect(stageOf("AWAITING_UPLOAD")).toBe("waiting");
    expect(stageOf("AWAITING_UPLOAD", true)).toBe("queued");
    expect(stageOf("PROCESSING")).toBe("reading");
    expect(stageOf("COMPLETED")).toBe("filed");
    expect(stageOf("FAILED")).toBe("failed");
  });

  it("parses hash routes", () => {
    expect(parseHash("#/doc/01M759W60M6PQY1BJD7KKV6G8Q")).toEqual({ name: "doc", id: "01M759W60M6PQY1BJD7KKV6G8Q" });
    expect(parseHash("#/how")).toEqual({ name: "how" });
    expect(parseHash("#/join/abc_DEF-123")).toEqual({ name: "join", key: "abc_DEF-123" });
    expect(parseHash("")).toEqual({ name: "home" });
    expect(parseHash("#/nope")).toEqual({ name: "home" });
  });
});
