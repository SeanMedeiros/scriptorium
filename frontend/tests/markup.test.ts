import { diffChars } from "@/lib/diff";
import { render, validate, wrap } from "@/lib/markup";

// Same cases as backend/tests/test_editorial_markup.py — the two implementations must agree.
const S = "{q̄|que} {?hũa?} pessoa {illeg:3} {-dixe-}{+disse+} o {^dito^} \\{x\\}";

describe("editorial markup (TS port)", () => {
  it("renders like the server", () => {
    expect(render(S, "diplomatic")).toBe("q̄ hũa pessoa […] dixe o dito {x}");
    expect(render(S, "expanded")).toBe("que hũa pessoa […] dixe o dito {x}");
    expect(render(S, "reading")).toBe("que hũa pessoa […] disse o dito {x}");
  });
  it.each([
    ["{?abc", "unclosed"],
    ["abc}", "closing brace"],
    ["{x}", "unknown element"],
    ["{|x}", "non-empty"],
    ["{??}", "empty"],
    ["{illeg:}", "number"],
  ])("rejects %s", (bad, frag) => {
    expect(validate(bad)?.message).toContain(frag);
  });
  it("wraps selections", () => {
    expect(wrap("a dita pessoa", 2, 6, "uncertain").text).toBe("a {?dita?} pessoa");
    const ab = wrap("q̄ a", 0, 2, "abbr");
    expect(ab.text).toBe("{q̄|} a");
    expect(ab.caret).toBe(4);
    expect(wrap("abc", 1, 1, "illeg").text).toBe("a{illeg:1}bc");
  });
});

describe("character diff", () => {
  it("finds insertions and deletions", () => {
    const d = diffChars("dixe nam", "disse não");
    expect(d.filter((p) => p.op === "eq").map((p) => p.text).join("")).toBe("die n");
    expect(d.map((p) => (p.op === "del" ? "" : p.text)).join("")).toBe("disse não");
    expect(d.map((p) => (p.op === "ins" ? "" : p.text)).join("")).toBe("dixe nam");
  });
});
