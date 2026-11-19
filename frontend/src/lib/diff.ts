// Character-level diff (LCS) between the original HTR line and the current reading.
export type DiffPart = { op: "eq" | "del" | "ins"; text: string };

export function diffChars(a: string, b: string): DiffPart[] {
  const A = Array.from(a);
  const B = Array.from(b);
  const n = A.length;
  const m = B.length;
  if (n * m > 400_000) return [{ op: "del", text: a }, { op: "ins", text: b }];
  const dp: Uint16Array[] = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: DiffPart[] = [];
  const push = (op: DiffPart["op"], ch: string) => {
    const last = out[out.length - 1];
    if (last && last.op === op) last.text += ch;
    else out.push({ op, text: ch });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) {
      push("eq", A[i]);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) push("del", A[i++]);
    else push("ins", B[j++]);
  }
  while (i < n) push("del", A[i++]);
  while (j < m) push("ins", B[j++]);
  return out;
}
