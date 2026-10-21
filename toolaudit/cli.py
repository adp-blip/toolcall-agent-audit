"""toolaudit: run agents on the PedalShare tasks k times each, grade, audit, write a report.

    python -m toolaudit.cli run --models groq:qwen/qwen3.8-27b --k 3
    python -m toolaudit.cli report
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from .episode import run_episode
from .grade import audit, grade

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
DEFAULT_MODELS = ["groq:openai/gpt-oss-20b", "groq:qwen/qwen3.8-27b"]


def _safe(model):
    return model.split(":", 1)[-1].replace("/", "_")


def run(args):
    tasks = json.loads((ROOT / "data" / "tasks.json").read_text(encoding="utf-8"))
    if args.only:
        tasks = [t for t in tasks if t["id"] in args.only]
    (RESULTS / "trajectories").mkdir(parents=True, exist_ok=True)

    for model in args.models:
        out = RESULTS / f"{_safe(model)}.jsonl"
        done = set()
        if out.exists() and args.resume:
            done = {(r["task_id"], r["trial"]) for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
        print(f"\n== {model}")
        with open(out, "a" if args.resume else "w", encoding="utf-8") as f:
            for task in tasks:
                for trial in range(args.k):
                    if (task["id"], trial) in done:
                        continue
                    try:
                        ep = run_episode(task, model, args.user_model)
                        ok, detail = grade(task, ep)
                        labels = audit(task, ep)
                    except Exception as e:
                        ep, ok, detail, labels = {"trace": [], "end_reason": "crash"}, False, {"error": str(e)[:300]}, ["crash"]
                    traj = RESULTS / "trajectories" / f"{_safe(model)}__{task['id']}__{trial}.json"
                    traj.write_text(json.dumps(ep, indent=2, ensure_ascii=False), encoding="utf-8")
                    row = {"task_id": task["id"], "category": task["category"], "trial": trial, "passed": ok,
                           "detail": detail, "labels": labels, "end_reason": ep["end_reason"],
                           "tool_calls": sum(e["type"] == "tool_call" for e in ep["trace"])}
                    f.write(json.dumps(row) + "\n")
                    f.flush()
                    print(f"  {'PASS' if ok else 'FAIL'} {task['id']} #{trial} {labels or ''}")
    report()


def report(*_):
    lines = ["# Results", "",
             "pass^k = share of tasks where **all** k trials passed (tau-bench's reliability metric).", "",
             "| model | trials | pass^1 | pass^k | avg tool calls |", "|---|---|---|---|---|"]
    label_rows, cat_rows = [], []
    for p in sorted(RESULTS.glob("*.jsonl")):
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not rows:
            continue
        by_task = defaultdict(list)
        for r in rows:
            by_task[r["task_id"]].append(r["passed"])
        k = min(len(v) for v in by_task.values())
        pass1 = sum(r["passed"] for r in rows) / len(rows)
        passk = sum(all(v[:k]) for v in by_task.values()) / len(by_task)
        avg_calls = sum(r["tool_calls"] for r in rows) / len(rows)
        lines.append(f"| `{p.stem}` | {len(rows)} | {pass1:.2f} | {passk:.2f} (k={k}) | {avg_calls:.1f} |")

        cats = defaultdict(list)
        for r in rows:
            cats[r["category"]].append(r["passed"])
        cat_rows.append((p.stem, {c: f"{sum(v)}/{len(v)}" for c, v in cats.items()}))
        counts = Counter(l for r in rows for l in r["labels"])
        label_rows.append((p.stem, counts))

    if cat_rows:
        cats = sorted({c for _, d in cat_rows for c in d})
        lines += ["", "## By category", "", "| model | " + " | ".join(cats) + " |", "|---" * (len(cats) + 1) + "|"]
        lines += [f"| `{m}` | " + " | ".join(d.get(c, "-") for c in cats) + " |" for m, d in cat_rows]

        labels = sorted({l for _, c in label_rows for l in c})
        lines += ["", "## Audit: failure labels (count of episodes)", "",
                  "| model | " + " | ".join(labels) + " |", "|---" * (len(labels) + 1) + "|"]
        lines += [f"| `{m}` | " + " | ".join(str(c.get(l, 0)) for l in labels) + " |" for m, c in label_rows]

    (RESULTS / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {RESULTS / 'summary.md'}")


def main():
    ap = argparse.ArgumentParser(prog="toolaudit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    r.add_argument("--user-model", default="groq:openai/gpt-oss-120b")
    r.add_argument("--k", type=int, default=2)
    r.add_argument("--only", nargs="*")
    r.add_argument("--resume", action="store_true", help="skip task/trial pairs already in results")
    sub.add_parser("report")
    args = ap.parse_args()
    run(args) if args.cmd == "run" else report()


if __name__ == "__main__":
    main()
