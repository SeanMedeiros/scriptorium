"""Training process, run as `python -m app.training.runner <work_dir>` by the training worker.

It reproduces `ketos train` (Kraken 7.1.1, kraken/ketos/recognition.py) with Kraken's public
training API — same configuration classes, data module, trainer and checkpointing — and adds a
Lightning callback that writes machine-readable events to `<work_dir>/events.jsonl`:

    {"type": "start", ...}  {"type": "progress", "epoch": e, "batch": b, "batches": n}
    {"type": "epoch", "epoch": e, "val_accuracy": …, "val_word_accuracy": …, "train_loss": …}
    {"type": "done", "best_checkpoint": …, "best_score": …, "weights": …, "checkpoints": [...]}
    {"type": "error", "message": …}

Cancellation: the worker creates `<work_dir>/CANCEL`; the callback stops the trainer at the next
batch. Running in a separate process isolates memory and lets the worker kill a stuck process.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

import lightning as L


class EventLog:
    def __init__(self, path: Path):
        self.f = open(path, "a", buffering=1, encoding="utf-8")

    def __call__(self, type_: str, **data) -> None:
        self.f.write(json.dumps({"type": type_, "ts": time.time(), **data}, default=str) + "\n")


def _metric(metrics, *names):
    for n in names:
        v = metrics.get(n)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


class ScriptoriumCallback(L.Callback):
    def __init__(self, emit: EventLog, work_dir: Path):
        self.emit, self.cancel_file, self.last = emit, work_dir / "CANCEL", 0.0

    def on_train_batch_end(self, trainer, pl_module, outputs, batch, batch_idx):
        if self.cancel_file.exists():
            trainer.should_stop = True
            self.emit("cancel_requested", epoch=trainer.current_epoch)
        now = time.time()
        if now - self.last > 2:
            self.last = now
            self.emit("progress", epoch=trainer.current_epoch, batch=batch_idx + 1, batches=trainer.num_training_batches,
                      max_epochs=trainer.max_epochs)

    def on_validation_end(self, trainer, pl_module):
        # Runs after the module's own validation hooks, so the epoch's metrics are logged.
        if trainer.sanity_checking:
            return
        m = trainer.callback_metrics
        self.emit("epoch", epoch=trainer.current_epoch,
                  val_accuracy=_metric(m, "val_accuracy", "val_metric"), val_metric=_metric(m, "val_metric"),
                  val_word_accuracy=_metric(m, "val_word_accuracy"),
                  val_cer=_metric(m, "val_cer"), val_wer=_metric(m, "val_wer"),
                  train_loss=_metric(m, "train_loss_epoch", "train_loss"))


def main(work_dir: Path) -> int:
    cfg = json.loads((work_dir / "config.json").read_text())
    emit = EventLog(work_dir / "events.jsonl")
    try:
        from lightning.pytorch.callbacks import ModelCheckpoint
        from threadpoolctl import threadpool_limits

        from kraken.models.convert import convert_models
        from kraken.train import KrakenTrainer
        from kraken.train.utils import KrakenOnExceptionCheckpoint
        from kraken.train.vgsl import VGSLRecognitionModel as module_cls

        if cfg.get("seed") is not None:
            L.seed_everything(cfg["seed"], workers=True)
        out = work_dir / "checkpoints"
        out.mkdir(exist_ok=True)
        lines = lambda name: [x for x in (work_dir / name).read_text().splitlines() if x.strip()]  # noqa: E731
        common = dict(training_data=lines("train.txt"), evaluation_data=lines("validation.txt"),
                      partition=1, format_type="page", normalization=cfg.get("normalization"),
                      normalize_whitespace=True, augment=cfg.get("augment", False), batch_size=cfg.get("batch_size", 1),
                      num_workers=0, epochs=cfg["epochs"], quit=cfg["quit"], min_epochs=cfg.get("min_epochs", 0),
                      lag=cfg.get("lag", 10), lrate=cfg["lrate"], optimizer=cfg.get("optimizer", "AdamW"),
                      schedule=cfg.get("schedule", "constant"), resize=cfg.get("resize", "union"),
                      checkpoint_path=str(out), weights_format="safetensors", freq=1.0)
        if cfg.get("spec"):
            common["spec"] = cfg["spec"]
        dm_config = module_cls._data_config_class(**{k: v for k, v in common.items()})
        m_config = module_cls._config_class(**{k: v for k, v in common.items()})
        # Weights-only checkpoints: no optimiser state (≈ 1/3 of the size and a much lower memory peak
        # while saving). Training is restarted from a registered version rather than resumed mid-run.
        ckpt = ModelCheckpoint(dirpath=out, save_top_k=cfg.get("keep_checkpoints", 3), monitor="val_metric", mode="max",
                               auto_insert_metric_name=False, filename="checkpoint_{epoch:02d}-{val_metric:.4f}",
                               save_weights_only=True)
        cbs = [KrakenOnExceptionCheckpoint(dirpath=out, filename="checkpoint_abort"), ckpt, ScriptoriumCallback(emit, work_dir)]
        trainer = KrakenTrainer(accelerator="cpu", devices=1, precision="32-true",
                                max_epochs=cfg["epochs"] if cfg["quit"] == "fixed" else -1,
                                min_epochs=cfg.get("min_epochs", 0), enable_progress_bar=False,
                                deterministic=cfg.get("deterministic", True), enable_model_summary=False,
                                accumulate_grad_batches=1, callbacks=cbs, gradient_clip_val=1.0,
                                num_sanity_val_steps=0, val_check_interval=1.0)
        data_module = module_cls._data_module_class(dm_config)
        with trainer.init_module(empty_init=not cfg.get("base_weights")):
            model = (module_cls.load_from_weights(cfg["base_weights"], config=m_config) if cfg.get("base_weights")
                     else module_cls(m_config))
        emit("start", base=cfg.get("base_weights"), epochs=cfg["epochs"], quit=cfg["quit"])
        with threadpool_limits(limits=cfg.get("threads", 1)):
            trainer.fit(model, data_module)
        if (work_dir / "CANCEL").exists():
            emit("cancelled", epoch=trainer.current_epoch)
            return 3
        if not ckpt.best_model_path:
            emit("error", message="Training finished without any validated checkpoint")
            return 2
        score = float(ckpt.best_model_score.item())
        weights = Path(ckpt.best_model_path).with_name(f"best_{score:.4f}.safetensors")
        convert_models([ckpt.best_model_path], weights, weights_format="safetensors")
        emit("done", best_checkpoint=Path(ckpt.best_model_path).name, best_score=score, weights=str(weights),
             checkpoints=[{"name": Path(p).name, "val_accuracy": float(s)} for p, s in ckpt.best_k_models.items()],
             epochs_completed=trainer.current_epoch)
        return 0
    except Exception as e:  # noqa: BLE001 — reported to the worker
        emit("error", message=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-4000:])
        return 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
