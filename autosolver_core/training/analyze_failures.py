"""Summarize failure tags from the experience memory."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sqlite3

DEFAULT_MEMORY = str(Path(__file__).resolve().parents[2] / "memory" / "training" / "experiments.sqlite")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory", default=DEFAULT_MEMORY)
    args = parser.parse_args()
    conn = sqlite3.connect(args.memory)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT scenario_type, strategy_name, failure_tags FROM experiments").fetchall()
    counter = defaultdict(Counter)
    for row in rows:
        try:
            tags = json.loads(row["failure_tags"] or "[]")
        except Exception:
            tags = []
        for tag in tags:
            counter[(row["scenario_type"], row["strategy_name"])][tag] += 1
    for (scenario, strategy), counts in sorted(counter.items()):
        print(f"{scenario} / {strategy}: {dict(counts.most_common())}")
    conn.close()


if __name__ == "__main__":
    main()
