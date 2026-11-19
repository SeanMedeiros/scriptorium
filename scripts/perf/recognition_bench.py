#!/usr/bin/env python3
"""Recognition throughput on CPU (performance evidence, Phase 7).

Runs Kraken's bundled baseline segmenter and a recognition model on real manuscript pages exactly as
the worker does (app.recognition.engine), timing segmentation and recognition separately. The first
pass includes model loading; the reported steady-state figures exclude it.

    recognition_bench.py <model.mlmodel|.safetensors> <page.jpg> [<page.jpg> …]
"""
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from PIL import Image  # noqa: E402

from app.recognition import engine  # noqa: E402


def main(model: str, pages: list[str]) -> None:
    blla = engine.bundled_segmentation_model_path()
    rows = []
    for rnd in range(2):  # round 0 warms the model cache
        for p in pages:
            im = Image.open(p)
            im.load()
            t0 = time.perf_counter()
            seg = engine.segment(im, model_path=blla)
            t1 = time.perf_counter()
            kseg = engine.build_kraken_segmentation([vars(l) for l in seg.lines], [vars(r) for r in seg.regions], seg.text_direction)
            out = list(engine.recognize(im, kseg, model_path=Path(model)))
            t2 = time.perf_counter()
            rows.append({"round": rnd, "page": Path(p).stem, "size": im.size, "lines": len(out),
                         "chars": sum(len(o.text) for o in out), "segment_s": round(t1 - t0, 2), "recognize_s": round(t2 - t1, 2)})
            print(json.dumps(rows[-1]), flush=True)
    steady = [r for r in rows if r["round"] == 1]
    per_page = [r["segment_s"] + r["recognize_s"] for r in steady]
    print(json.dumps({
        "cpu_threads": os.cpu_count(), "software": engine.software_versions(), "model": Path(model).name,
        "pages": len(steady), "mean_segment_s": round(statistics.mean(r["segment_s"] for r in steady), 2),
        "mean_recognize_s": round(statistics.mean(r["recognize_s"] for r in steady), 2),
        "mean_page_s": round(statistics.mean(per_page), 2), "pages_per_hour_one_worker": round(3600 / statistics.mean(per_page)),
        "lines_per_s_recognition": round(sum(r["lines"] for r in steady) / sum(r["recognize_s"] for r in steady), 2),
    }, indent=2))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
