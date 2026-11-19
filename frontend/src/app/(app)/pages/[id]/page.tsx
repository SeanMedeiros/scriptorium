"use client";

import { ChevronLeft, ChevronRight, Minus, Plus } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Badge, Button, Card, CardHeader, EmptyState, PageHeader, Select } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api, ApiError, type DocumentInfo, type PageInfo, type RecognitionInfo, type SegmentationInfo } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import { cn } from "@/lib/utils";

export default function PageViewer() {
  const { id } = useParams<{ id: string }>();
  const { t, formatDate } = useI18n();
  const page = useResource<PageInfo>(`/pages/${id}`);
  const doc = useResource<DocumentInfo>(page.data ? `/documents/${page.data.document_id}` : null);
  const recs = useResource<RecognitionInfo[]>(`/pages/${id}/recognitions`);
  const segs = useResource<SegmentationInfo[]>(`/pages/${id}/segmentations`);
  const [recId, setRecId] = useState<string | null>(null);
  const [segId, setSegId] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [showLines, setShowLines] = useState(true);
  const textRefs = useRef<Record<string, HTMLLIElement | null>>({});
  const router = useRouter();
  const [openErr, setOpenErr] = useState<string | null>(null);
  async function openEditor() {
    try {
      const t = await api<{ id: string }>(`/pages/${id}/transcriptions`, { method: "POST", json: { recognition_id: recId } });
      router.push(`/transcriptions/${t.id}`);
    } catch (e) {
      setOpenErr(e instanceof ApiError ? e.body.message : String(e));
    }
  }

  useEffect(() => {
    if (recs.data && recs.data.length && !recId) setRecId(recs.data[0].id);
  }, [recs.data, recId]);
  const rec = useResource<RecognitionInfo>(recId ? `/recognitions/${recId}` : null);
  const effectiveSegId = rec.data?.segmentation_id ?? segId ?? segs.data?.[0]?.id ?? null;
  const seg = useResource<SegmentationInfo>(effectiveSegId ? `/segmentations/${effectiveSegId}` : null);

  const textByLine = useMemo(() => {
    const m: Record<string, { text: string; conf: number | null }> = {};
    rec.data?.lines.forEach((l) => (m[l.line_id] = { text: l.text, conf: l.mean_confidence }));
    return m;
  }, [rec.data]);

  if (page.error) return <Alert>{t("common.notFound")}</Alert>;
  const p = page.data;
  if (!p) return <p className="text-sm text-muted">{t("common.loading")}</p>;
  const pages = doc.data?.pages ?? [];
  const idx = pages.findIndex((x) => x.id === p.id);
  const prev = idx > 0 ? pages[idx - 1] : null;
  const next = idx >= 0 && idx < pages.length - 1 ? pages[idx + 1] : null;
  const img = seg.data?.input_asset ?? p.image;
  const W = img?.width ?? 1;
  const H = img?.height ?? 1;
  const ops = seg.data?.config.preprocessing?.operations ?? [];

  function selectLine(lineId: string, scroll: boolean) {
    setActive(lineId);
    if (scroll) textRefs.current[lineId]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  return (
    <>
      <PageHeader
        eyebrow={
          doc.data && (
            <Link href={`/documents/${doc.data.id}`} className="hover:text-ink">
              {doc.data.project_title} / {doc.data.collection_title} / {doc.data.title}
            </Link>
          )
        }
        title={t("page.title", { n: p.sequence })}
        action={
          <div className="flex items-center gap-2">
            {recId && (
              <Button size="sm" onClick={openEditor}>
                {t("ws.open")}
              </Button>
            )}
            {(segs.data?.length ?? 0) > 0 && (
              <Button size="sm" variant="secondary" asChild>
                <Link href={`/pages/${p.id}/compare`}>{t("cmp.open")}</Link>
              </Button>
            )}
            <Button variant="secondary" size="sm" asChild disabled={!prev}>
              {prev ? (
                <Link href={`/pages/${prev.id}`} aria-label={t("page.prev")}>
                  <ChevronLeft />
                </Link>
              ) : (
                <span aria-hidden>
                  <ChevronLeft />
                </span>
              )}
            </Button>
            <span className="tabular text-sm text-muted">
              {idx + 1} / {pages.length || "?"}
            </span>
            <Button variant="secondary" size="sm" asChild disabled={!next}>
              {next ? (
                <Link href={`/pages/${next.id}`} aria-label={t("page.next")}>
                  <ChevronRight />
                </Link>
              ) : (
                <span aria-hidden>
                  <ChevronRight />
                </span>
              )}
            </Button>
          </div>
        }
      />
      {openErr && <Alert>{openErr}</Alert>}
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <Card className="overflow-hidden">
          <div className="flex items-center gap-2 border-b border-rule px-3 py-2">
            <Button variant="ghost" size="icon" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.25, z / 1.25))}>
              <Minus />
            </Button>
            <span className="tabular w-12 text-center text-xs text-muted">{Math.round(zoom * 100)}%</span>
            <Button variant="ghost" size="icon" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(6, z * 1.25))}>
              <Plus />
            </Button>
            <label className="ml-3 flex items-center gap-1.5 text-xs text-ink-2">
              <input type="checkbox" checked={showLines} onChange={(e) => setShowLines(e.target.checked)} className="accent-[var(--color-accent)]" />
              {t("page.showLines")}
            </label>
            {img && (
              <span className="ml-auto font-mono text-[11px] text-muted">
                {img.kind} · {img.width}×{img.height}
              </span>
            )}
          </div>
          <div className="max-h-[78vh] overflow-auto bg-sunken" data-testid="page-image">
            {img?.url && (
              <div className="relative" style={{ width: `${zoom * 100}%` }}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={img.url} alt={t("page.title", { n: p.sequence })} className="block w-full select-none" draggable={false} />
                {seg.data && showLines && (
                  <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 h-full w-full" data-testid="line-overlay">
                    {seg.data.lines.map((ln) => (
                      <polygon
                        key={ln.id}
                        data-line-id={ln.id}
                        points={(ln.boundary ?? []).map((pt) => pt.join(",")).join(" ")}
                        className={cn(
                          "cursor-pointer transition-colors",
                          active === ln.id ? "fill-[#23395b]/25 stroke-[#23395b]" : "fill-[#23395b]/5 stroke-[#23395b]/50 hover:fill-[#23395b]/15",
                        )}
                        strokeWidth={Math.max(1.5, W / 900)}
                        onClick={() => selectLine(ln.id, true)}
                      >
                        <title>{textByLine[ln.id]?.text ?? `#${ln.order_index + 1}`}</title>
                      </polygon>
                    ))}
                  </svg>
                )}
              </div>
            )}
          </div>
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader
              title={t("page.lines")}
              description={rec.data ? t("page.originalOutput") : undefined}
              action={
                recs.data && recs.data.length > 1 ? (
                  <Select aria-label={t("page.runs")} value={recId ?? ""} onChange={(e) => setRecId(e.target.value)} className="w-56 text-xs">
                    {recs.data.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.model} · {formatDate(r.created_at)}
                      </option>
                    ))}
                  </Select>
                ) : undefined
              }
            />
            {!recs.data?.length && !segs.data?.length && <EmptyState title={t("page.noResult")} />}
            {!recs.data?.length && !!segs.data?.length && <p className="px-5 py-4 text-sm text-muted">{t("page.segmentationOnly")}</p>}
            {rec.data && (
              <ol className="max-h-[56vh] divide-y divide-rule overflow-y-auto" data-testid="recognised-lines">
                {seg.data?.lines.map((ln) => {
                  const tx = textByLine[ln.id];
                  return (
                    <li
                      key={ln.id}
                      ref={(el) => {
                        textRefs.current[ln.id] = el;
                      }}
                      onMouseEnter={() => setActive(ln.id)}
                      onClick={() => selectLine(ln.id, false)}
                      className={cn("flex cursor-pointer gap-3 px-5 py-1.5", active === ln.id ? "bg-accent-soft" : "hover:bg-paper")}
                      data-line-id={ln.id}
                    >
                      <span className="tabular w-6 shrink-0 pt-0.5 text-right text-[11px] text-muted">{ln.order_index + 1}</span>
                      <span className="flex-1 font-serif text-[15px] leading-snug text-ink">{tx?.text || <span className="text-muted">∅</span>}</span>
                      {tx?.conf != null && (
                        <span className="tabular shrink-0 pt-0.5 text-[11px] text-muted" title={t("page.confidenceNote")}>
                          {tx.conf.toFixed(2)}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ol>
            )}
          </Card>

          {(rec.data || seg.data) && (
            <Card>
              <CardHeader title={t("page.provenance")} />
              <dl className="grid grid-cols-[150px_minmax(0,1fr)] gap-x-4 gap-y-2 px-5 py-4 text-[13px]" data-testid="provenance">
                {rec.data && (
                  <>
                    <dt className="text-muted">{t("page.model")}</dt>
                    <dd className="text-ink-2">
                      {rec.data.model}
                      {rec.data.unicode_normalization && <Badge className="ml-2">{rec.data.unicode_normalization}</Badge>}
                    </dd>
                    <dt className="text-muted">{t("page.confidence")}</dt>
                    <dd className="text-xs text-muted">{t("page.confidenceNote")}</dd>
                    <dt className="text-muted">{t("jobs.duration")}</dt>
                    <dd className="tabular text-ink-2">
                      {rec.data.line_count} lines · {rec.data.char_count} chars · {((rec.data.duration_ms ?? 0) / 1000).toFixed(1)} s
                    </dd>
                  </>
                )}
                {seg.data && (
                  <>
                    <dt className="text-muted">{t("recognition.segModel")}</dt>
                    <dd className="text-ink-2">
                      {seg.data.source} · {seg.data.lines.length} lines · {seg.data.regions.length} regions ·{" "}
                      {((seg.data.duration_ms ?? 0) / 1000).toFixed(1)} s
                    </dd>
                    <dt className="text-muted">{t("page.preprocessing")}</dt>
                    <dd className="font-mono text-xs text-ink-2">{ops.length ? ops.map((o) => o.op).join(" → ") : t("page.none")}</dd>
                    <dt className="text-muted">{t("page.software")}</dt>
                    <dd className="font-mono text-[11px] text-ink-2">
                      {Object.entries(seg.data.software)
                        .filter(([k]) => ["kraken", "torch", "python", "device"].includes(k))
                        .map(([k, v]) => `${k} ${v}`)
                        .join(" · ")}
                    </dd>
                    <dt className="text-muted">Image</dt>
                    <dd className="break-all font-mono text-[11px] text-ink-2">
                      {seg.data.input_asset?.kind} sha256:{seg.data.input_asset?.sha256.slice(0, 16)}…
                    </dd>
                  </>
                )}
              </dl>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
