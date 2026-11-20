import { describe, it } from "node:test";
import { expect } from "./expect";
import { micErrorText, pickMime } from "../lib/voice";

describe("voice recording (2.2)", () => {
  it("picks a recording format the browser supports, webm first, mp4 for WebKit", () => {
    expect(pickMime((m) => m.startsWith("audio/webm"))).toBe("audio/webm;codecs=opus");
    expect(pickMime((m) => m === "audio/mp4")).toBe("audio/mp4");
    expect(pickMime(() => false)).toBe("");
  });
  it("explains microphone errors in plain words", () => {
    expect(micErrorText({ name: "NotAllowedError" })).toContain("blocked");
    expect(micErrorText({ name: "NotFoundError" })).toContain("No microphone");
    expect(micErrorText({ name: "NotReadableError" })).toContain("busy");
    expect(micErrorText(new Error("odd"))).toBe("odd");
  });
});
