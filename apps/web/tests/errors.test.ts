import { describe, it } from "node:test";
import { expect } from "./expect";
import { errorText } from "../lib/errors";

describe("errorText", () => {
  it("reads Error, string and message-like values", () => {
    expect(errorText(new Error("boom"), "x")).toBe("boom");
    expect(errorText("plain", "x")).toBe("plain");
    expect(errorText({ message: "obj" }, "x")).toBe("obj");
  });
  it("falls back for empty or unknown values", () => {
    for (const v of [null, undefined, 42, {}, "", new Error("")]) expect(errorText(v, "fallback")).toBe("fallback");
  });
});
