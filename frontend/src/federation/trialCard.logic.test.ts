import { describe, expect, it } from "vitest";
import { isValidElement } from "react";

import { asText, renderMd, scoreTier } from "./bits";

describe("scoreTier", () => {
  // Thresholds mirror CB `getScoreColor`: >=80 green, >=60 yellow, else red.
  it("returns green at and above 80", () => {
    expect(scoreTier(80)).toBe("green");
    expect(scoreTier(100)).toBe("green");
  });
  it("returns yellow in [60, 80)", () => {
    expect(scoreTier(79)).toBe("yellow");
    expect(scoreTier(60)).toBe("yellow");
  });
  it("returns red below 60", () => {
    expect(scoreTier(59)).toBe("red");
    expect(scoreTier(0)).toBe("red");
  });
});

describe("renderMd", () => {
  it("returns the raw string unchanged when there are no ** markers", () => {
    expect(renderMd("plain text")).toBe("plain text");
    expect(renderMd("")).toBe("");
  });

  it("returns an array containing a <strong> element for **bold** text", () => {
    const result = renderMd("**IMiDs**") as React.ReactNode[];
    // parts: ["", <strong>IMiDs</strong>, ""]
    expect(Array.isArray(result)).toBe(true);
    const strong = result.find(isValidElement);
    expect(strong).toBeTruthy();
    expect((strong as React.ReactElement<{ children: unknown }>).type).toBe("strong");
    expect((strong as React.ReactElement<{ children: unknown }>).props.children).toBe("IMiDs");
  });

  it("interleaves plain text and <strong> nodes", () => {
    const result = renderMd("Pre **bold** post") as React.ReactNode[];
    expect(result[0]).toBe("Pre ");
    expect(isValidElement(result[1])).toBe(true);
    expect(result[2]).toBe(" post");
  });

  it("handles multiple bold segments", () => {
    const result = renderMd("**A**, **B**") as React.ReactNode[];
    const strongs = result.filter(isValidElement);
    expect(strongs).toHaveLength(2);
    expect((strongs[0] as React.ReactElement<{ children: unknown }>).props.children).toBe("A");
    expect((strongs[1] as React.ReactElement<{ children: unknown }>).props.children).toBe("B");
  });
});

describe("asText", () => {
  it("joins arrays with ', ' by default and drops falsy entries", () => {
    expect(asText(["Boston", "", "New York"])).toBe("Boston, New York");
  });
  it("honours a custom separator", () => {
    expect(asText(["Phase 1", "Phase 2"], " / ")).toBe("Phase 1 / Phase 2");
  });
  it("passes strings through and coerces non-strings", () => {
    expect(asText("Interventional")).toBe("Interventional");
    expect(asText(3)).toBe("3");
  });
  it("returns an empty string for null/undefined", () => {
    expect(asText(null)).toBe("");
    expect(asText(undefined)).toBe("");
  });
});
