"use client";

import { ExportDialog, ProvenanceDialog } from "@/components/research";
import { History, LayoutTemplate, MessageSquare, Redo2, Trash2, Undo2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ImageCanvas, type CanvasLine, type Pt } from "@/components/workspace/ImageCanvas";
import { Alert, Badge, Button, Input, Select, Textarea } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/messages";
import { api, ApiError, type DocumentInfo, type PageInfo } from "@/lib/api";
import { diffChars } from "@/lib/diff";
import { safeRender, validate, wrap } from "@/lib/markup";
import { cn } from "@/lib/utils";

// ------------------------------------------------------------------ types
type LineState = {
  version_id: string;
  text: string;
  normalized_text: string | null;
  note: string | null;
  status: "unreviewed" | "corrected" | "reviewed";
  has_uncertain: boolean;
  has_illegible: boolean;
  source: string;
  author: string | null;
  created_at: string;
};
type TxLine = {
  line_id: string;
  order_index: number;
  bbox: [number, number, number, number];
  boundary: Pt[] | null;
  baseline: Pt[] | null;
  original: { text: string; model_confidence: number | null; model?: string | null } | null;
  current: LineState | null;
};
type Tx = {
  id: string;
  page_id: string;
  segmentation_id: string;
  model: { name: string; version: string; unicode_normalization: string | null; recognition_id: string; model_version_id: string } | null;
  origin?: "recognition" | "combined";
  comparison_id?: string | null;
  policy: Record<string, string>;
  head_revision: number;
  viewing_revision: number;
  input_asset: { url: string; width: number; height: number };
  permissions: string[];
  counts: Record<string, number>;
  lines: TxLine[];
};
type Edit = { text: string; normalized: string | null; note: string | null; status: LineState["status"] };
type Edits = Record<string, Edit>;
type View = "edit" | "compare" | "diplomatic" | "expanded" | "reading";
type Conflict = { line_id: string; mine: Edit; theirs: string };

const toEdit = (s: LineState | null): Edit => ({ text: s?.text ?? "", normalized: s?.normalized_text ?? null, note: s?.note ?? null, status: s?.status ?? "unreviewed" });
const editsEqual = (a: Edits, b: Edits) => {
  const ka = Object.keys(a);
  return ka.length === Object.keys(b).length && ka.every((k) => b[k] && same(a[k], b[k]));
};
const same = (a: Edit, b: Edit) => a.text === b.text && (a.normalized ?? null) === (b.normalized ?? null) && (a.note ?? null) === (b.note ?? null) && a.status === b.status;

export default function Workspace() {
  const { id } = useParams<{ id: string }>();
  const { t, formatDate } = useI18n();
  const [tx, setTx] = useState<Tx | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [doc, setDoc] = useState<DocumentInfo | null>(null);
  const [page, setPage] = useState<PageInfo | null>(null);
  // server: last saved state + version per line; local: what the researcher sees
  const [server, setServer] = useState<Record<string, { version: string; edit: Edit }>>({});
  const [local, setLocal] = useState<Edits>({});
  const [active, setActive] = useState<string | null>(null);
  const [view, setView] = useState<View>("edit");
  const [save, setSave] = useState<{ state: "saved" | "saving" | "dirty" | "error"; msg?: string }>({ state: "saved" });
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [panel, setPanel] = useState<"none" | "history" | "comments">("none");
  const [viewing, setViewing] = useState<number | null>(null); // past revision (read-only)
  const inputs = useRef<Record<string, HTMLInputElement | null>>({});
  // undo / redo: snapshots of `local`
  const hist = useRef<{ stack: Edits[]; i: number }>({ stack: [], i: -1 });
  const checkpointTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saving = useRef(false);
  const localRef = useRef(local);
  // Keep a synchronous mirror of `local` so that rapid edits never read a stale render.
  const setL = useCallback((next: Edits) => {
    localRef.current = next;
    setLocal(next);
  }, []);
  const serverRef = useRef(server);
  serverRef.current = server;

  const canEdit = !!tx?.permissions.includes("transcribe") && viewing === null;
  const canReview = !!tx?.permissions.includes("review") && viewing === null;

  const absorb = useCallback((data: Tx, keepLocal: boolean) => {
    setTx(data);
    const srv: Record<string, { version: string; edit: Edit }> = {};
    for (const ln of data.lines) if (ln.current) srv[ln.line_id] = { version: ln.current.version_id, edit: toEdit(ln.current) };
    setServer(srv);
    if (!keepLocal) {
      const loc: Edits = Object.fromEntries(Object.entries(srv).map(([k, v]) => [k, v.edit]));
      setL(loc);
      hist.current = { stack: [loc], i: 0 };
    }
  }, [setL]);

  const load = useCallback(async () => {
    try {
      const data = await api<Tx>(`/transcriptions/${id}`);
      absorb(data, false);
      const pg = await api<PageInfo>(`/pages/${data.page_id}`);
      setPage(pg);
      setDoc(await api<DocumentInfo>(`/documents/${pg.document_id}`));
    } catch (e) {
      setLoadErr(e instanceof ApiError ? e.body.message : String(e));
    }
  }, [id, absorb]);
  useEffect(() => {
    load();
  }, [load]);

  const dirtyIds = useMemo(() => Object.keys(local).filter((k) => server[k] && !same(local[k], server[k].edit)), [local, server]);
  const invalid = useMemo(() => {
    const out: Record<string, string> = {};
    for (const k of dirtyIds) {
      const e = validate(local[k].text);
      if (e) out[k] = `${e.message} (${e.position + 1})`;
    }
    return out;
  }, [dirtyIds, local]);

  // ------------------------------------------------------------ autosave
  const doSave = useCallback(async () => {
    if (saving.current || viewing !== null) return;
    const loc = localRef.current;
    const srv = serverRef.current;
    const ids = Object.keys(loc).filter((k) => srv[k] && !same(loc[k], srv[k].edit) && !validate(loc[k].text));
    if (!ids.length) return;
    saving.current = true;
    setSave({ state: "saving" });
    const sent: Edits = Object.fromEntries(ids.map((k) => [k, loc[k]]));
    try {
      const res = await api<{ transcription: Tx }>(`/transcriptions/${id}/revisions`, {
        method: "POST",
        json: {
          changes: ids.map((k) => {
            const e = sent[k], s = srv[k].edit;
            return {
              line_id: k,
              expected_version_id: srv[k].version,
              ...(e.text !== s.text ? { text: e.text } : {}),
              ...(e.normalized !== s.normalized ? (e.normalized ? { normalized_text: e.normalized } : { clear_normalized: true }) : {}),
              ...(e.note !== s.note ? (e.note ? { note: e.note } : { clear_note: true }) : {}),
              ...(e.status !== s.status ? { status: e.status } : {}),
            };
          }),
        },
      });
      absorb(res.transcription, true);
      // Server may normalise Unicode (explicit policy): adopt the server form for lines not edited
      // meanwhile, and rewrite the current undo step so that undo never "reverts" a normalisation.
      const before = localRef.current;
      const next = { ...before };
      for (const ln of res.transcription.lines) if (ln.current && sent[ln.line_id] && same(before[ln.line_id], sent[ln.line_id])) next[ln.line_id] = toEdit(ln.current);
      const h = hist.current;
      if (h.i >= 0 && editsEqual(h.stack[h.i], before)) h.stack[h.i] = next;
      setL(next);
      setSave({ state: "saved" });
    } catch (e) {
      if (e instanceof ApiError && e.code === "edit_conflict") {
        const cs = (e.body.details as { conflicts: { line_id: string; current_text: string }[] }).conflicts;
        const fresh = await api<Tx>(`/transcriptions/${id}`);
        absorb(fresh, true);
        setConflicts(cs.map((c) => ({ line_id: c.line_id, mine: sent[c.line_id], theirs: c.current_text })));
        setSave({ state: "error", msg: t("ws.conflict", { n: cs.length }) });
      } else {
        setSave({ state: "error", msg: e instanceof ApiError ? JSON.stringify(e.body.details ?? e.body.message) : String(e) });
      }
    } finally {
      saving.current = false;
    }
  }, [id, viewing, absorb, t]);

  useEffect(() => {
    if (conflicts.length) return;
    if (!dirtyIds.length) {
      // Edits that return the text to its saved form leave nothing to save.
      setSave((s) => (s.state === "dirty" ? { state: "saved" } : s));
      return;
    }
    setSave((s) => (s.state === "saving" ? s : { state: "dirty" }));
    const h = setTimeout(doSave, 1500);
    return () => clearTimeout(h);
  }, [local, dirtyIds.length, conflicts.length, doSave]);

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (Object.keys(localRef.current).some((k) => serverRef.current[k] && !same(localRef.current[k], serverRef.current[k].edit))) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  // ------------------------------------------------------------ undo / redo
  // Snapshots of the whole local state. Typing is grouped into one step after 700 ms of
  // inactivity; discrete actions (toolbar, status, restore…) close the pending step first.
  const pending = useRef<Edits | null>(null);
  const pushSnap = useCallback((snap: Edits) => {
    const h = hist.current;
    if (h.i >= 0 && editsEqual(h.stack[h.i], snap)) return;
    h.stack = h.stack.slice(0, h.i + 1).concat([snap]).slice(-300);
    h.i = h.stack.length - 1;
  }, []);
  const flush = useCallback(() => {
    if (checkpointTimer.current) {
      clearTimeout(checkpointTimer.current);
      checkpointTimer.current = null;
    }
    if (pending.current) {
      pushSnap(pending.current);
      pending.current = null;
    }
  }, [pushSnap]);
  const change = useCallback(
    (lineId: string, patch: Partial<Edit>, discrete = false) => {
      if (discrete) flush();
      const next = { ...localRef.current, [lineId]: { ...localRef.current[lineId], ...patch } };
      setL(next);
      if (discrete) pushSnap(next);
      else {
        pending.current = next;
        if (checkpointTimer.current) clearTimeout(checkpointTimer.current);
        checkpointTimer.current = setTimeout(flush, 700);
      }
    },
    [flush, pushSnap, setL],
  );
  const undo = useCallback(() => {
    flush();
    const h = hist.current;
    if (h.i > 0) {
      h.i -= 1;
      setL(h.stack[h.i]);
    }
  }, [flush, setL]);
  const redo = useCallback(() => {
    flush();
    const h = hist.current;
    if (h.i < h.stack.length - 1) {
      h.i += 1;
      setL(h.stack[h.i]);
    }
  }, [flush, setL]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      } else if (e.key.toLowerCase() === "y") {
        e.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [undo, redo]);

  // ------------------------------------------------------------ helpers
  const order = tx?.lines ?? [];
  const focusLine = (lineId: string) => {
    setActive(lineId);
    requestAnimationFrame(() => {
      const el = inputs.current[lineId];
      if (el) {
        el.focus({ preventScroll: true });
        el.scrollIntoView({ block: "nearest" });
      } else document.querySelector(`[data-row="${lineId}"]`)?.scrollIntoView({ block: "nearest" });
    });
  };
  const move = (from: string, delta: number) => {
    const i = order.findIndex((l) => l.line_id === from);
    const n = order[i + delta];
    if (n) focusLine(n.line_id);
  };
  const applyMarkup = (kind: Parameters<typeof wrap>[3]) => {
    if (!active || !canEdit) return;
    const el = inputs.current[active];
    const s = local[active]?.text ?? "";
    const a = el?.selectionStart ?? s.length, b = el?.selectionEnd ?? s.length;
    const r = wrap(s, a, b, kind);
    change(active, { text: r.text }, true);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(r.caret, r.caret);
    });
  };

  // ------------------------------------------------------------ past revisions
  const openRevision = async (n: number | null) => {
    if (n === null || n === tx?.head_revision) {
      setViewing(null);
      await load();
      return;
    }
    const data = await api<Tx>(`/transcriptions/${id}?at_revision=${n}`);
    setViewing(n);
    absorb(data, false);
  };

  // ------------------------------------------------------------ layout editing
  const [layout, setLayout] = useState<CanvasLine[] | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [layoutMsg, setLayoutMsg] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const startLayout = () => {
    setLayout(order.map((l) => ({ id: l.line_id, boundary: l.boundary, baseline: l.baseline, bbox: l.bbox })));
    setLayoutMsg(null);
  };
  const deleteLine = useCallback(() => {
    if (!layout || !active) return;
    setLayout(layout.filter((l) => l.id !== active));
    setActive(null);
  }, [layout, active]);
  useEffect(() => {
    if (!layout) return;
    const k = (e: KeyboardEvent) => {
      if ((e.key === "Delete" || e.key === "Backspace") && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) {
        e.preventDefault();
        deleteLine();
      }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [layout, deleteLine]);
  const saveLayout = async () => {
    if (!layout || !tx?.model) return;
    try {
      const seg = await api<{ id: string }>(`/pages/${tx.page_id}/segmentations`, {
        method: "POST",
        json: {
          parent_segmentation_id: tx.segmentation_id,
          lines: layout.map((l) => ({ baseline: l.baseline, boundary: l.boundary, source_line_id: l.id.startsWith("new-") ? null : l.id })),
        },
      });
      const projectId = doc?.project_id;
      await api(`/projects/${projectId}/recognition-jobs`, {
        method: "POST",
        json: { job_type: "recognize", page_ids: [tx.page_id], input_segmentation_id: seg.id, recognition_model_version_id: tx.model.model_version_id },
      });
      setLayout(null);
      setDrawing(false);
      setLayoutMsg({ tone: "success", text: t("ws.layout.saved", { model: `${tx.model.name} ${tx.model.version}` }) });
    } catch (e) {
      setLayoutMsg({ tone: "danger", text: e instanceof ApiError ? e.body.message : String(e) });
    }
  };

  if (loadErr) return <div className="p-8"><Alert>{loadErr}</Alert></div>;
  if (!tx) return <div className="p-8 text-sm text-muted">{t("common.loading")}</div>;

  const canvasLines: CanvasLine[] =
    layout ??
    order.map((l) => {
      const e = local[l.line_id];
      const flag = e && /\{illeg/.test(e.text) ? "illegible" : e && /\{\?/.test(e.text) ? "uncertain" : e?.status === "reviewed" ? "reviewed" : null;
      return { id: l.line_id, boundary: l.boundary, baseline: l.baseline, bbox: l.bbox, flag };
    });
  const pages = doc?.pages ?? [];
  const pidx = pages.findIndex((p) => p.id === tx.page_id);
  const statusTone = { unreviewed: "neutral", corrected: "accent", reviewed: "success" } as const;

  return (
    <div className="flex h-screen flex-col">
      {/* ---------------------------------------------------- header */}
      <header className="flex flex-wrap items-center gap-3 border-b border-rule bg-surface px-5 py-2.5">
        <div className="min-w-0">
          <div className="truncate text-xs text-muted">
            {doc && (
              <Link href={`/documents/${doc.id}`} className="hover:text-ink">
                {doc.project_title} / {doc.collection_title} / {doc.title}
              </Link>
            )}
          </div>
          <h1 className="font-serif text-lg font-semibold text-ink">
            {t("ws.title")} · {t("page.title", { n: page?.sequence ?? "?" })}
            {pages.length > 0 && <span className="ml-2 text-sm font-normal text-muted">({pidx + 1}/{pages.length})</span>}
          </h1>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span
            data-testid="save-state"
            data-state={save.state}
            className={cn("rounded px-2 py-1 text-xs", save.state === "error" ? "bg-danger-soft text-danger" : save.state === "saved" ? "text-success" : "text-muted")}
          >
            {viewing !== null ? t("ws.readOnly") : save.state === "saved" ? t("ws.saved", { n: tx.head_revision }) : save.state === "saving" ? t("ws.saving") : save.state === "dirty" ? t("ws.unsaved") : t("ws.error")}
          </span>
          <Button variant="ghost" size="icon" aria-label={t("ws.undo")} title={`${t("ws.undo")} (Ctrl+Z)`} onClick={undo} disabled={!canEdit}>
            <Undo2 />
          </Button>
          <Button variant="ghost" size="icon" aria-label={t("ws.redo")} title={`${t("ws.redo")} (Ctrl+Shift+Z)`} onClick={redo} disabled={!canEdit}>
            <Redo2 />
          </Button>
          <Select aria-label="View" value={view} onChange={(e) => setView(e.target.value as View)} className="h-8 w-48 text-xs">
            {(["edit", "compare", "diplomatic", "expanded", "reading"] as View[]).map((v) => (
              <option key={v} value={v}>
                {t(`ws.view.${v}` as MessageKey)}
              </option>
            ))}
          </Select>
          <Button variant={panel === "history" ? "secondary" : "ghost"} size="sm" onClick={() => setPanel(panel === "history" ? "none" : "history")}>
            <History /> {t("ws.history")}
          </Button>
          <Button variant={panel === "comments" ? "secondary" : "ghost"} size="sm" onClick={() => setPanel(panel === "comments" ? "none" : "comments")}>
            <MessageSquare /> {t("ws.annotations")}
          </Button>
          <ProvenanceDialog transcriptionId={tx.id} />
          {tx.permissions.includes("export") && <ExportDialog transcriptionId={tx.id} atRevision={viewing} />}
        </div>
      </header>
      {viewing !== null && (
        <div className="flex items-center gap-3 border-b border-warn/30 bg-warn-soft px-5 py-1.5 text-sm text-warn" data-testid="viewing-banner">
          {t("ws.viewingRevision", { n: viewing, head: tx.head_revision })}
          <Button size="sm" variant="secondary" onClick={() => openRevision(null)}>
            {t("ws.backToCurrent")}
          </Button>
        </div>
      )}
      {save.state === "error" && save.msg && !conflicts.length && <Alert>{save.msg}</Alert>}
      {conflicts.length > 0 && (
        <div className="border-b border-danger/30 bg-danger-soft px-5 py-2 text-sm text-danger" data-testid="conflict-banner">
          <p>{t("ws.conflict", { n: conflicts.length })}</p>
          <ul className="mt-1 space-y-1">
            {conflicts.map((c) => (
              <li key={c.line_id} className="flex flex-wrap items-center gap-2 text-ink-2">
                <span className="font-mono text-xs">#{(order.find((l) => l.line_id === c.line_id)?.order_index ?? 0) + 1}</span>
                <span>“{c.theirs}”</span>→<span className="italic">“{c.mine.text}”</span>
                <Button size="sm" variant="secondary" onClick={() => setConflicts((cs) => cs.filter((x) => x !== c))}>
                  {t("ws.useMine")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setConflicts((cs) => cs.filter((x) => x !== c));
                    change(c.line_id, serverRef.current[c.line_id].edit, true);
                  }}
                >
                  {t("ws.useTheirs")}
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ---------------------------------------------------- body */}
      <div className="grid min-h-0 flex-1" style={{ gridTemplateColumns: panel === "none" ? "minmax(0,1.1fr) minmax(0,1fr)" : "minmax(0,1fr) minmax(0,1fr) 320px" }}>
        <section className="flex min-h-0 flex-col border-r border-rule" aria-label="Page image">
          <div className="flex items-center gap-2 border-b border-rule px-3 py-1.5 text-xs">
            {canEdit && !layout && tx.model && (
              <Button variant="ghost" size="sm" onClick={startLayout}>
                <LayoutTemplate /> {t("ws.layout")}
              </Button>
            )}
            {layout && (
              <>
                <Button variant={drawing ? "primary" : "secondary"} size="sm" onClick={() => setDrawing(!drawing)}>
                  {t("ws.layout.draw")}
                </Button>
                <Button variant="secondary" size="sm" onClick={deleteLine} disabled={!active}>
                  <Trash2 /> {t("ws.layout.delete")}
                </Button>
                <Button size="sm" onClick={saveLayout}>
                  {t("ws.layout.save")}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => (setLayout(null), setDrawing(false))}>
                  {t("ws.layout.cancel")}
                </Button>
                <span className="tabular text-muted">{layout.length} lines</span>
              </>
            )}
          </div>
          {layout && <p className="border-b border-rule px-3 py-1.5 text-[11px] text-muted">{t("ws.layout.help")}</p>}
          {layoutMsg && <Alert tone={layoutMsg.tone}>{layoutMsg.text}</Alert>}
          <div className="min-h-0 flex-1">
            <ImageCanvas
              src={tx.input_asset.url}
              width={tx.input_asset.width}
              height={tx.input_asset.height}
              lines={canvasLines}
              activeId={active}
              onSelect={(lid) => (layout ? setActive(lid) : focusLine(lid))}
              layoutMode={!!layout}
              drawing={drawing}
              onBaselineChange={(lid, bl) =>
                setLayout((ls) => ls!.map((l) => (l.id === lid ? { ...l, baseline: bl, boundary: null } : l)))
              }
              onDrawn={(bl) => {
                const nid = `new-${Date.now()}`;
                const xs = bl.map((p) => p[0]), ys = bl.map((p) => p[1]);
                setLayout((ls) => {
                  const nl: CanvasLine = { id: nid, baseline: bl, boundary: null, bbox: [Math.min(...xs), Math.min(...ys) - 40, Math.max(...xs), Math.max(...ys)] };
                  const y = Math.min(...ys);
                  const idx = ls!.findIndex((l) => (l.baseline?.[0]?.[1] ?? l.bbox[3]) > y);
                  return idx < 0 ? [...ls!, nl] : [...ls!.slice(0, idx), nl, ...ls!.slice(idx)];
                });
                setDrawing(false);
                setActive(nid);
              }}
              fitLabel={t("ws.fit")}
            />
          </div>
        </section>

        <section className="flex min-h-0 flex-col" aria-label="Transcription">
          {view === "edit" && canEdit && (
            <div className="flex flex-wrap items-center gap-1 border-b border-rule px-3 py-1.5" role="toolbar" aria-label={t("ws.toolbar")}>
              {(
                [
                  ["uncertain", "ws.mk.uncertain", "{?…?}"],
                  ["illeg", "ws.mk.illegible", "{illeg:N}"],
                  ["addition", "ws.mk.addition", "{+…+}"],
                  ["deletion", "ws.mk.deletion", "{-…-}"],
                  ["abbr", "ws.mk.abbr", "{a|b}"],
                  ["superscript", "ws.mk.sup", "{^…^}"],
                ] as const
              ).map(([k, label, sym]) => (
                <Button key={k} variant="secondary" size="sm" title={t(label)} onMouseDown={(e) => e.preventDefault()} onClick={() => applyMarkup(k)} disabled={!active}>
                  <span className="font-mono text-[11px]">{sym}</span> <span className="hidden xl:inline">{t(label)}</span>
                </Button>
              ))}
            </div>
          )}
          <div className="border-b border-rule px-4 py-1.5 text-[11px] text-muted" data-testid="ws-counts">
            {t("ws.counts", {
              reviewed: Object.values(local).filter((e) => e.status === "reviewed").length,
              corrected: Object.values(local).filter((e) => e.status === "corrected").length,
              unreviewed: Object.values(local).filter((e) => e.status === "unreviewed").length,
              uncertain: Object.values(local).filter((e) => /\{\?/.test(e.text)).length,
              illegible: Object.values(local).filter((e) => /\{illeg/.test(e.text)).length,
            })}
            <span className="ml-2">· {t("ws.policy")}: {tx.policy.type}, Unicode {tx.policy.unicode_input}, abbr. {tx.policy.abbreviations}</span>
          </div>
          <ol className="min-h-0 flex-1 overflow-y-auto" data-testid="ws-lines">
            {order.map((ln) => {
              const e = local[ln.line_id] ?? toEdit(ln.current);
              const isActive = active === ln.line_id;
              const dirty = server[ln.line_id] && !same(e, server[ln.line_id].edit);
              const err = invalid[ln.line_id];
              return (
                <li
                  key={ln.line_id}
                  data-row={ln.line_id}
                  className={cn("border-b border-rule px-4 py-1.5", isActive ? "bg-accent-soft/60" : "hover:bg-paper")}
                  onClick={() => setActive(ln.line_id)}
                >
                  <div className="flex items-center gap-2">
                    <span className="tabular w-7 shrink-0 text-right text-[11px] text-muted">{ln.order_index + 1}</span>
                    {view === "edit" ? (
                      <Input
                        ref={(el) => {
                          inputs.current[ln.line_id] = el;
                        }}
                        data-line-input={ln.line_id}
                        aria-label={`Line ${ln.order_index + 1}`}
                        aria-invalid={!!err}
                        value={e.text}
                        readOnly={!canEdit}
                        spellCheck={false}
                        className={cn("h-8 border-transparent bg-transparent font-serif text-[15px] focus:bg-surface", dirty && "border-l-2 border-l-accent")}
                        onFocus={() => setActive(ln.line_id)}
                        onChange={(ev) => change(ln.line_id, { text: ev.target.value })}
                        onKeyDown={(ev) => {
                          if (ev.key === "Enter" || ev.key === "ArrowDown" || ev.key === "ArrowUp") {
                            ev.preventDefault();
                            move(ln.line_id, ev.key === "ArrowUp" || (ev.key === "Enter" && ev.shiftKey) ? -1 : 1);
                          }
                        }}
                      />
                    ) : view === "compare" ? (
                      <span className="flex-1 font-serif text-[15px] leading-snug" data-testid="diff-line">
                        {diffChars(ln.original?.text ?? "", safeRender(e.text) ?? e.text).map((p, i) => (
                          <span key={i} className={p.op === "del" ? "bg-danger-soft text-danger line-through" : p.op === "ins" ? "bg-success-soft text-success" : ""}>
                            {p.text}
                          </span>
                        ))}
                      </span>
                    ) : (
                      <span className="flex-1 font-serif text-[15px] leading-snug">{safeRender(e.text, view as "diplomatic" | "expanded" | "reading") ?? e.text}</span>
                    )}
                    <Badge tone={statusTone[e.status]} data-status={e.status} className="shrink-0">
                      {t(`ws.status.${e.status}` as MessageKey)}
                    </Badge>
                  </div>
                  {err && <p className="ml-9 text-xs text-danger" role="alert">{err}</p>}
                  {isActive && view === "edit" && (
                    <LineDetails
                      tx={tx}
                      line={ln}
                      edit={e}
                      canEdit={canEdit}
                      canReview={canReview}
                      onChange={(patch) => change(ln.line_id, patch, true)}
                      onRestored={(data) => absorb(data, false)}
                    />
                  )}
                </li>
              );
            })}
          </ol>
          <p className="border-t border-rule px-4 py-1 text-[10px] text-muted">{t("ws.keyboard")} · {t("ws.markupHelp")}</p>
        </section>

        {panel === "history" && <RevisionsPanel txId={tx.id} head={tx.head_revision} viewing={viewing} onOpen={openRevision} formatDate={formatDate} />}
        {panel === "comments" && <CommentsPanel txId={tx.id} active={active} order={order} canWrite={canEdit || canReview} />}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ line details
function LineDetails({ tx, line, edit, canEdit, canReview, onChange, onRestored }: {
  tx: Tx; line: TxLine; edit: Edit; canEdit: boolean; canReview: boolean;
  onChange: (p: Partial<Edit>) => void; onRestored: (t: Tx) => void;
}) {
  const { t, formatDate } = useI18n();
  const [history, setHistory] = useState<(LineState & { revision: number })[] | null>(null);
  return (
    <div className="ml-9 mt-2 space-y-2 pb-2 text-[13px]" onClick={(e) => e.stopPropagation()} data-testid="line-details">
      <div>
        <span className="text-xs text-muted">
          {t("ws.original")}
          {line.original?.model ? ` (${line.original.model})` : ""}:{" "}
        </span>
        <span className="font-serif text-ink-2" data-testid="original-text">{line.original?.text ?? "—"}</span>
        {line.original?.model_confidence != null && <span className="ml-2 text-[11px] text-muted" title={t("page.confidenceNote")}>({line.original.model_confidence.toFixed(2)})</span>}
      </div>
      <div className="grid gap-2 md:grid-cols-2">
        <label className="block">
          <span className="text-xs text-muted">{t("ws.normalized")}</span>
          <Input value={edit.normalized ?? ""} readOnly={!canEdit} onChange={(e) => onChange({ normalized: e.target.value || null })} className="h-8 font-serif" data-testid="normalized-input" />
        </label>
        <label className="block">
          <span className="text-xs text-muted">{t("ws.note")}</span>
          <Textarea value={edit.note ?? ""} readOnly={!canEdit} onChange={(e) => onChange({ note: e.target.value || null })} className="min-h-[32px] py-1 text-xs" data-testid="note-input" />
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {canEdit && edit.status === "unreviewed" && (
          <Button size="sm" variant="secondary" onClick={() => onChange({ status: "corrected" })}>
            {t("ws.markCorrected")}
          </Button>
        )}
        {canReview && edit.status !== "reviewed" && (
          <Button size="sm" variant="secondary" onClick={() => onChange({ status: "reviewed" })}>
            {t("ws.markReviewed")}
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={async () => setHistory(await api(`/transcriptions/${tx.id}/lines/${line.line_id}/history`))}>
          <History /> {t("ws.lineHistory")}
        </Button>
      </div>
      {history && (
        <ol className="space-y-1 rounded border border-rule bg-surface p-2" data-testid="line-history">
          {history.map((h, i) => (
            <li key={h.version_id} className="flex items-center gap-2 text-xs">
              <span className="tabular w-8 text-muted">r{h.revision}</span>
              <span className="flex-1 font-serif text-[13px] text-ink">{h.text}</span>
              <span className="text-muted">{h.author ?? h.source} · {formatDate(h.created_at)}</span>
              {canEdit && i > 0 && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={async () => {
                    const r = await api<{ transcription: Tx }>(`/transcriptions/${tx.id}/lines/${line.line_id}/restore`, { method: "POST", json: { version_id: h.version_id } });
                    onRestored(r.transcription);
                    setHistory(null);
                  }}
                >
                  {t("ws.restore")}
                </Button>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ side panels
function RevisionsPanel({ txId, head, viewing, onOpen, formatDate }: { txId: string; head: number; viewing: number | null; onOpen: (n: number | null) => void; formatDate: (s: string) => string }) {
  const { t } = useI18n();
  const [revs, setRevs] = useState<{ number: number; kind: string; message: string | null; change_count: number; author: string | null; created_at: string }[]>([]);
  useEffect(() => {
    api<typeof revs>(`/transcriptions/${txId}/revisions`).then(setRevs);
  }, [txId, head]);
  return (
    <aside className="min-h-0 overflow-y-auto border-l border-rule bg-surface" data-testid="revisions-panel">
      <h2 className="border-b border-rule px-4 py-2 font-serif text-sm font-semibold">{t("ws.revisions")}</h2>
      <ol className="divide-y divide-rule">
        {revs.map((r) => (
          <li key={r.number} className={cn("px-4 py-2 text-xs", viewing === r.number && "bg-warn-soft")}>
            <div className="flex items-center justify-between">
              <span className="font-medium text-ink">
                r{r.number} · {r.kind}
              </span>
              <Button size="sm" variant="ghost" onClick={() => onOpen(r.number)}>
                {t("ws.viewRevision")}
              </Button>
            </div>
            <div className="text-muted">
              {r.author ?? "—"} · {formatDate(r.created_at)} · {r.change_count} lines
            </div>
            {r.message && <div className="italic text-ink-2">{r.message}</div>}
          </li>
        ))}
      </ol>
    </aside>
  );
}

function CommentsPanel({ txId, active, order, canWrite }: { txId: string; active: string | null; order: TxLine[]; canWrite: boolean }) {
  const { t, formatDate } = useI18n();
  const [items, setItems] = useState<{ id: string; line_id: string | null; kind: string; body: string; author: string | null; created_at: string; resolved_at: string | null }[]>([]);
  const [body, setBody] = useState("");
  const [kind, setKind] = useState("comment");
  const reload = useCallback(() => api<typeof items>(`/transcriptions/${txId}/annotations`).then(setItems), [txId]);
  useEffect(() => {
    reload();
  }, [reload]);
  const num = (lid: string | null) => (lid ? `#${(order.find((l) => l.line_id === lid)?.order_index ?? 0) + 1}` : "page");
  return (
    <aside className="flex min-h-0 flex-col border-l border-rule bg-surface" data-testid="comments-panel">
      <h2 className="border-b border-rule px-4 py-2 font-serif text-sm font-semibold">{t("ws.annotations")}</h2>
      <ol className="min-h-0 flex-1 divide-y divide-rule overflow-y-auto">
        {items.map((a) => (
          <li key={a.id} className={cn("px-4 py-2 text-xs", a.resolved_at && "opacity-50")}>
            <div className="flex items-center gap-2">
              <Badge tone={a.kind === "query" ? "warn" : "neutral"}>{t(`an.kind.${a.kind}` as MessageKey)}</Badge>
              <span className="font-mono text-muted">{num(a.line_id)}</span>
              {!a.resolved_at && canWrite && (
                <Button size="sm" variant="link" className="ml-auto" onClick={() => api(`/annotations/${a.id}/resolve`, { method: "POST" }).then(reload)}>
                  {t("ws.resolve")}
                </Button>
              )}
            </div>
            <p className="mt-1 whitespace-pre-line text-ink-2">{a.body}</p>
            <div className="text-muted">
              {a.author} · {formatDate(a.created_at)}
            </div>
          </li>
        ))}
      </ol>
      {canWrite && (
        <form
          className="space-y-2 border-t border-rule p-3"
          onSubmit={async (e) => {
            e.preventDefault();
            await api(`/transcriptions/${txId}/annotations`, { method: "POST", json: { line_id: active, kind, body } });
            setBody("");
            reload();
          }}
        >
          <div className="flex items-center gap-2 text-xs text-muted">
            {num(active)}
            <Select value={kind} onChange={(e) => setKind(e.target.value)} className="h-7 w-32 text-xs" aria-label="Kind">
              <option value="comment">{t("ws.comment")}</option>
              <option value="query">{t("ws.query")}</option>
              <option value="palaeography">{t("an.kind.palaeography")}</option>
            </Select>
          </div>
          <Textarea value={body} onChange={(e) => setBody(e.target.value)} className="min-h-[60px] text-xs" aria-label={t("ws.addComment")} required />
          <Button size="sm" type="submit" disabled={!body.trim()}>
            {t("ws.addComment")}
          </Button>
        </form>
      )}
    </aside>
  );
}
