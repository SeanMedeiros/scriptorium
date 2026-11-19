// TypeScript port of app/editorial/markup.py (SCRIPTORIUM editorial markup v1).
// Used for instant validation and preview while typing; the server remains authoritative.

export type Node =
  | { t: "text"; v: string }
  | { t: "span"; kind: "uncertain" | "addition" | "deletion" | "superscript"; children: Node[] }
  | { t: "abbr"; abbr: string; expansion: string }
  | { t: "gap"; extent: number | null };

export class MarkupError extends Error {
  constructor(message: string, public position: number) {
    super(message);
  }
}

const PAIRED: Record<string, "uncertain" | "addition" | "deletion" | "superscript"> = {
  "?": "uncertain",
  "+": "addition",
  "-": "deletion",
  "^": "superscript",
};
const ESC = "{}|\\";

export function parse(s: string): Node[] {
  let i = 0;
  const plainUntil = (stops: string) => {
    let out = "";
    while (i < s.length) {
      const c = s[i];
      if (c === "\\" && i + 1 < s.length && ESC.includes(s[i + 1])) {
        out += s[i + 1];
        i += 2;
        continue;
      }
      if (stops.includes(c)) break;
      if (c === "{") throw new MarkupError("nested markup is not allowed here", i);
      out += c;
      i++;
    }
    return out;
  };
  const element = (): Node => {
    const start = i;
    i++;
    if (i >= s.length) throw new MarkupError("unclosed '{'", start);
    const c = s[i];
    if (c in PAIRED) {
      i++;
      const children = seq(c);
      if (!children.length) throw new MarkupError(`empty ${PAIRED[c]} element`, start);
      return { t: "span", kind: PAIRED[c], children };
    }
    if (s.startsWith("illeg", i)) {
      i += 5;
      let extent: number | null = null;
      if (s[i] === ":") {
        i++;
        const j = i;
        while (i < s.length && /\d/.test(s[i])) i++;
        if (j === i) throw new MarkupError("{illeg:N} needs a number", j);
        extent = Number(s.slice(j, i));
      }
      if (s[i] !== "}") throw new MarkupError("expected '}' after illeg", i);
      i++;
      return { t: "gap", extent };
    }
    const abbr = plainUntil("|}");
    if (s[i] !== "|") throw new MarkupError("unknown element: use {?…?} {+…+} {-…-} {^…^} {illeg} or {abbreviation|expansion}", start);
    i++;
    const expansion = plainUntil("}");
    if (i >= s.length) throw new MarkupError("unclosed abbreviation", start);
    i++;
    if (!abbr || !expansion) throw new MarkupError("abbreviation and expansion must both be non-empty", start);
    return { t: "abbr", abbr, expansion };
  };
  const seq = (closer: string | null): Node[] => {
    const nodes: Node[] = [];
    let buf = "";
    const flush = () => {
      if (buf) nodes.push({ t: "text", v: buf });
      buf = "";
    };
    while (i < s.length) {
      const c = s[i];
      if (closer && c === closer && s[i + 1] === "}") {
        flush();
        i += 2;
        return nodes;
      }
      if (c === "\\" && i + 1 < s.length && ESC.includes(s[i + 1])) {
        buf += s[i + 1];
        i += 2;
        continue;
      }
      if (c === "}") throw new MarkupError("closing brace without an opening one", i);
      if (c === "{") {
        flush();
        nodes.push(element());
        continue;
      }
      buf += c;
      i++;
    }
    if (closer) throw new MarkupError(`unclosed element: expected '${closer}}'`, i);
    flush();
    return nodes;
  };
  return seq(null);
}

export function validate(s: string): MarkupError | null {
  try {
    parse(s);
    return null;
  } catch (e) {
    if (e instanceof MarkupError) return e;
    throw e;
  }
}

export type Mode = "diplomatic" | "expanded" | "reading";

function r(n: Node, mode: Mode): string {
  switch (n.t) {
    case "text":
      return n.v;
    case "gap":
      return "[…]";
    case "abbr":
      return mode === "diplomatic" ? n.abbr : n.expansion;
    case "span": {
      const inner = n.children.map((c) => r(c, mode)).join("");
      if (n.kind === "addition") return mode === "reading" ? inner : "";
      if (n.kind === "deletion") return mode === "reading" ? "" : inner;
      return inner;
    }
  }
}

export function render(s: string, mode: Mode = "diplomatic"): string {
  return parse(s)
    .map((n) => r(n, mode))
    .join("");
}

export function safeRender(s: string, mode: Mode = "diplomatic"): string | null {
  try {
    return render(s, mode);
  } catch {
    return null;
  }
}

/** Wrap the selection [a, b) of `s` with a markup element; returns new text and caret. */
export function wrap(s: string, a: number, b: number, kind: "uncertain" | "addition" | "deletion" | "superscript" | "abbr" | "illeg"): { text: string; caret: number } {
  const sel = s.slice(a, b);
  const open = { uncertain: "{?", addition: "{+", deletion: "{-", superscript: "{^" } as const;
  const close = { uncertain: "?}", addition: "+}", deletion: "-}", superscript: "^}" } as const;
  if (kind === "illeg") {
    const ins = `{illeg:${Math.max(1, sel.length || 1)}}`;
    return { text: s.slice(0, a) + ins + s.slice(b), caret: a + ins.length };
  }
  if (kind === "abbr") {
    const ins = `{${sel || "…"}|}`;
    return { text: s.slice(0, a) + ins + s.slice(b), caret: a + ins.length - 1 };
  }
  const ins = `${open[kind]}${sel}${close[kind]}`;
  return { text: s.slice(0, a) + ins + s.slice(b), caret: sel ? a + ins.length : a + 2 };
}
