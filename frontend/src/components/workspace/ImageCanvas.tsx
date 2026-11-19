"use client";

import { Maximize2, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";

export type Pt = [number, number];
export type CanvasLine = { id: string; boundary: Pt[] | null; baseline: Pt[] | null; bbox: [number, number, number, number]; flag?: "uncertain" | "illegible" | "reviewed" | null };

type Props = {
  src: string;
  width: number;
  height: number;
  lines: CanvasLine[];
  activeId: string | null;
  onSelect: (id: string) => void;
  layoutMode?: boolean;
  drawing?: boolean;
  onBaselineChange?: (id: string, baseline: Pt[]) => void;
  onDrawn?: (baseline: Pt[]) => void;
  fitLabel: string;
};

export function ImageCanvas({ src, width: W, height: H, lines, activeId, onSelect, layoutMode, drawing, onBaselineChange, onDrawn, fitLabel }: Props) {
  const view = useRef<HTMLDivElement>(null);
  const [t, setT] = useState({ x: 0, y: 0, s: 0.2 });
  const drag = useRef<{ x: number; y: number; tx: number; ty: number; moved: boolean } | null>(null);
  const vtx = useRef<{ id: string; i: number } | null>(null);
  const [draft, setDraft] = useState<Pt[]>([]);
  const lineById = useRef(new Map<string, CanvasLine>());
  lineById.current = new Map(lines.map((l) => [l.id, l]));

  const fit = useCallback(() => {
    const el = view.current;
    if (!el) return;
    const s = el.clientWidth / W;
    setT({ x: 0, y: 0, s });
  }, [W]);
  useLayoutEffect(fit, [fit]);

  // Bring the active line into view (text → image synchronisation).
  useEffect(() => {
    const el = view.current;
    const ln = activeId ? lineById.current.get(activeId) : null;
    if (!el || !ln) return;
    const [x0, y0, x1, y1] = ln.bbox;
    setT((cur) => {
      const vx0 = -cur.x / cur.s, vy0 = -cur.y / cur.s;
      const vx1 = vx0 + el.clientWidth / cur.s, vy1 = vy0 + el.clientHeight / cur.s;
      if (x0 >= vx0 && x1 <= vx1 && y0 >= vy0 && y1 <= vy1) return cur;
      const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
      return { ...cur, x: el.clientWidth / 2 - cx * cur.s, y: el.clientHeight / 2 - cy * cur.s };
    });
  }, [activeId]);

  const toImage = (clientX: number, clientY: number): Pt => {
    const r = view.current!.getBoundingClientRect();
    return [Math.round((clientX - r.left - t.x) / t.s), Math.round((clientY - r.top - t.y) / t.s)];
  };

  const zoomAt = (factor: number, cx?: number, cy?: number) => {
    const el = view.current;
    if (!el) return;
    const px = cx ?? el.clientWidth / 2, py = cy ?? el.clientHeight / 2;
    setT((cur) => {
      const s = Math.min(8, Math.max(0.03, cur.s * factor));
      return { s, x: px - ((px - cur.x) * s) / cur.s, y: py - ((py - cur.y) * s) / cur.s };
    });
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-rule px-2 py-1.5">
        <Button variant="ghost" size="icon" aria-label="Zoom out" onClick={() => zoomAt(1 / 1.25)}>
          <Minus />
        </Button>
        <span className="tabular w-12 text-center text-xs text-muted" data-testid="zoom-level">
          {Math.round(t.s * 100)}%
        </span>
        <Button variant="ghost" size="icon" aria-label="Zoom in" onClick={() => zoomAt(1.25)}>
          <Plus />
        </Button>
        <Button variant="ghost" size="sm" onClick={fit}>
          <Maximize2 /> {fitLabel}
        </Button>
      </div>
      <div
        ref={view}
        className={cn("relative flex-1 touch-none overflow-hidden bg-sunken", drawing ? "cursor-crosshair" : "cursor-grab active:cursor-grabbing")}
        data-testid="image-canvas"
        onWheel={(e) => {
          e.preventDefault();
          const r = view.current!.getBoundingClientRect();
          zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - r.left, e.clientY - r.top);
        }}
        onPointerDown={(e) => {
          if (drawing) return;
          (e.target as Element).setPointerCapture?.(e.pointerId);
          drag.current = { x: e.clientX, y: e.clientY, tx: t.x, ty: t.y, moved: false };
        }}
        onPointerMove={(e) => {
          if (vtx.current && onBaselineChange) {
            const ln = lineById.current.get(vtx.current.id);
            if (ln?.baseline) {
              const bl = ln.baseline.map((p) => [...p] as Pt);
              const [x, y] = toImage(e.clientX, e.clientY);
              bl[vtx.current.i] = [Math.max(0, Math.min(W, x)), Math.max(0, Math.min(H, y))];
              onBaselineChange(vtx.current.id, bl);
            }
            return;
          }
          const d = drag.current;
          if (!d) return;
          if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 3) d.moved = true;
          setT((cur) => ({ ...cur, x: d.tx + e.clientX - d.x, y: d.ty + e.clientY - d.y }));
        }}
        onPointerUp={() => {
          drag.current = null;
          vtx.current = null;
        }}
        onClick={(e) => {
          if (!drawing) return;
          const [x, y] = toImage(e.clientX, e.clientY);
          const p: Pt = [Math.max(0, Math.min(W, x)), Math.max(0, Math.min(H, y))];
          // A double click also fires two clicks: never add the same point twice in a row.
          setDraft((d) => (d.length && d[d.length - 1][0] === p[0] && d[d.length - 1][1] === p[1] ? d : [...d, p]));
        }}
        onDoubleClick={() => {
          if (!drawing) return;
          const pts = draft.filter((p, i) => i === 0 || p[0] !== draft[i - 1][0] || p[1] !== draft[i - 1][1]);
          setDraft([]);
          if (pts.length >= 2) onDrawn?.(pts);
        }}
      >
        <div className="absolute left-0 top-0 origin-top-left" style={{ transform: `translate(${t.x}px, ${t.y}px) scale(${t.s})`, width: W, height: H }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={src} alt="" width={W} height={H} draggable={false} className="pointer-events-none block select-none" />
          <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="absolute inset-0" data-testid="line-overlay">
            {lines.map((ln) => {
              const active = ln.id === activeId;
              const poly = ln.boundary ?? (ln.baseline ? ln.baseline.concat([...ln.baseline].reverse().map(([x, y]) => [x, y - 40] as Pt)) : []);
              return (
                <g key={ln.id}>
                  <polygon
                    data-line-id={ln.id}
                    points={poly.map((p) => p.join(",")).join(" ")}
                    strokeWidth={Math.max(1.5, 1.5 / t.s)}
                    strokeDasharray={ln.boundary ? undefined : `${8 / t.s} ${6 / t.s}`}
                    className={cn(
                      "cursor-pointer",
                      active
                        ? "fill-[#23395b]/25 stroke-[#23395b]"
                        : ln.flag === "illegible" || ln.flag === "uncertain"
                          ? "fill-[#8a5a12]/10 stroke-[#8a5a12]/70"
                          : ln.flag === "reviewed"
                            ? "fill-[#2f5d3a]/8 stroke-[#2f5d3a]/60"
                            : "fill-[#23395b]/5 stroke-[#23395b]/45 hover:fill-[#23395b]/15",
                    )}
                    onClick={(e) => {
                      if (drawing) return;
                      if (drag.current?.moved) return;
                      e.stopPropagation();
                      onSelect(ln.id);
                    }}
                  />
                  {layoutMode && ln.baseline && (
                    <>
                      <polyline points={ln.baseline.map((p) => p.join(",")).join(" ")} fill="none" stroke="#9b2c2c" strokeWidth={Math.max(2, 2 / t.s)} pointerEvents="none" />
                      {active &&
                        ln.baseline.map((p, i) => (
                          <circle
                            key={i}
                            cx={p[0]}
                            cy={p[1]}
                            r={Math.max(6, 7 / t.s)}
                            className="cursor-move fill-white stroke-[#9b2c2c]"
                            strokeWidth={Math.max(2, 2 / t.s)}
                            data-vertex={i}
                            onPointerDown={(e) => {
                              e.stopPropagation();
                              (e.target as Element).setPointerCapture?.(e.pointerId);
                              vtx.current = { id: ln.id, i };
                            }}
                          />
                        ))}
                    </>
                  )}
                </g>
              );
            })}
            {draft.length > 0 && (
              <polyline points={draft.map((p) => p.join(",")).join(" ")} fill="none" stroke="#9b2c2c" strokeWidth={Math.max(2, 2 / t.s)} strokeDasharray={`${6 / t.s} ${4 / t.s}`} />
            )}
          </svg>
        </div>
      </div>
    </div>
  );
}
