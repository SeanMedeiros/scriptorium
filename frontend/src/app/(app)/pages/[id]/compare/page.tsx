"use client";

import { GitCompareArrows } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Alert, Button, Card, CardHeader, EmptyState, Field, PageHeader, Select, Table, Td, Th } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api, ApiError, type ModelVersionInfo, type PageInfo, type SegmentationInfo } from "@/lib/api";
import { useResource } from "@/lib/hooks";

export default function ComparePage() {
  const { id } = useParams<{ id: string }>();
  const { t, formatDate } = useI18n();
  const router = useRouter();
  const page = useResource<PageInfo>(`/pages/${id}`);
  const segs = useResource<SegmentationInfo[]>(`/pages/${id}/segmentations`);
  const models = useResource<ModelVersionInfo[]>("/models?task=recognition&usable=true");
  const past = useResource<{ id: string; created_at: string; models: string[] }[]>(`/pages/${id}/comparisons`);
  const [segId, setSegId] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!segId && segs.data?.length) setSegId(segs.data[0].id);
  }, [segs.data, segId]);

  async function start() {
    setErr(null);
    setBusy(true);
    try {
      const c = await api<{ id: string }>(`/pages/${id}/comparisons`, { method: "POST", json: { segmentation_id: segId, model_version_ids: chosen } });
      router.push(`/comparisons/${c.id}`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.body.message : String(e));
      setBusy(false);
    }
  }

  const p = page.data;
  if (!p) return <p className="text-sm text-muted">{t("common.loading")}</p>;
  return (
    <>
      <PageHeader
        eyebrow={
          <Link href={`/pages/${id}`} className="hover:text-ink">
            {t("cmp.backToPage", { n: String(p.sequence) })}
          </Link>
        }
        title={t("cmp.title")}
        subtitle={t("cmp.subtitle")}
      />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader title={t("cmp.new")} description={t("cmp.new.help")} />
          <div className="space-y-4 p-5">
            {err && <Alert>{err}</Alert>}
            {segs.data && segs.data.length === 0 ? (
              <EmptyState title={t("cmp.noSeg")} body={t("cmp.noSeg.body")} />
            ) : (
              <Field htmlFor="cmp-f1" label={t("cmp.segmentation")} hint={t("cmp.segmentation.hint")}>
                <Select id="cmp-f1" value={segId} onChange={(e) => setSegId(e.target.value)} aria-label={t("cmp.segmentation")}>
                  {segs.data?.map((s) => (
                    <option key={s.id} value={s.id}>
                      {t(`cmp.segSource.${s.source}` as never) || s.source} · {s.lines.length} {t("ev.lines").toLowerCase()} · {formatDate(s.created_at)}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            <fieldset className="space-y-1 text-sm">
              <legend className="mb-1 text-xs font-medium text-muted">{t("cmp.models")}</legend>
              {models.data?.map((m) => (
                <label key={m.id} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={chosen.includes(m.id)}
                    disabled={!chosen.includes(m.id) && chosen.length >= 6}
                    onChange={(e) => setChosen((c) => (e.target.checked ? [...c, m.id] : c.filter((x) => x !== m.id)))}
                  />
                  {m.model_name} · {m.version}
                </label>
              ))}
            </fieldset>
            <Button onClick={start} disabled={busy || !segId || chosen.length < 2}>
              <GitCompareArrows /> {t("cmp.start")}
            </Button>
          </div>
        </Card>
        <Card>
          <CardHeader title={t("cmp.past")} />
          {past.data && past.data.length ? (
            <Table>
              <thead>
                <tr>
                  <Th>{t("common.date")}</Th>
                  <Th>{t("cmp.models")}</Th>
                </tr>
              </thead>
              <tbody>
                {past.data.map((c) => (
                  <tr key={c.id}>
                    <Td>
                      <Link className="text-accent hover:underline" href={`/comparisons/${c.id}`}>
                        {formatDate(c.created_at)}
                      </Link>
                    </Td>
                    <Td className="text-xs">{c.models.join(" · ")}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : (
            <p className="p-5 text-sm text-muted">{t("common.none")}</p>
          )}
        </Card>
      </div>
    </>
  );
}
