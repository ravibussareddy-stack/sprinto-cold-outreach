"""Review queue and measurement.

    python review.py queue                                   # companies that need a human
    python review.py add acme.io --email kept --poc-correct yes --minutes 1.5
    python review.py add acme.io --email rewritten --poc-correct no --minutes 4 --note "wrong CTO"
    python review.py metrics                                 # rewrite rate, PoC accuracy, time vs targets
"""
import argparse

import db
import measure


def _yn(v: str | None):
    if v is None:
        return None
    return {"yes": True, "no": False}[v.lower()]


def cmd_queue(_):
    rows = db.review_queue()
    if not rows:
        print("Review queue is empty.")
        return
    print(f"{'run':>5}  {'domain':<28} {'decision':<18} status")
    for run_id, domain, decision, status, created in rows:
        print(f"{run_id:>5}  {domain:<28} {str(decision):<18} {status}")


def cmd_add(a):
    run_id = db.add_review(a.domain, a.email, _yn(a.poc_correct), a.minutes, a.note or "")
    print(f"recorded review for {a.domain} (run {run_id})")


def cmd_metrics(_):
    m = measure.compute(db.review_rows())

    def pct(x):
        return "-" if x is None else f"{x:.0%}"

    def mins(x):
        return "-" if x is None else f"{x:.1f}"

    print(f"reviewed companies: {m['reviewed']}   drafts: {m['drafts']}")
    print(f"rewrite rate:       {pct(m['rewrite_rate'])}")
    print(f"PoC accuracy:       {pct(m['poc_accuracy'])}")
    print(f"avg minutes/co:     {mins(m['avg_minutes'])}  "
          f"(baseline {measure.BASELINE_MINUTES[0]}-{measure.BASELINE_MINUTES[1]} min)")
    for v in m["verdicts"]:
        print(" -", v)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(required=True)
    sub.add_parser("queue").set_defaults(func=cmd_queue)
    sub.add_parser("metrics").set_defaults(func=cmd_metrics)
    add = sub.add_parser("add")
    add.add_argument("domain")
    add.add_argument("--email", required=True, choices=["kept", "rewritten", "not_drafted"])
    add.add_argument("--poc-correct", choices=["yes", "no"])
    add.add_argument("--minutes", type=float)
    add.add_argument("--note")
    add.set_defaults(func=cmd_add)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
