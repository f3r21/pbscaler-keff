"""Merge per-run summaries across MULTIPLE result roots into one aggregate.

Use this to build a UNIFIED aggregate.csv that spans una campaña previa (banked vanilla
+ khpa runs) AND la campaña actual (keff + naive + re-run vanilla), filtered down to
the workloads in scope for este trabajo (step, bursty).

It reuses the exact ingestion logic from `instrumentation/aggregate_results.py`
(find_summary_files, resolve_controller, normalize_benchmark, classify_runtime,
COLUMN_ORDER) so the merged table is schema-identical to a single-root
aggregate — only the workload filter and the multi-root walk are added here.

Per root:
    1. find_summary_files(root)        — rglob, excludes `_archive`/`_*` trees
    2. read each failure_modes_summary.csv
    3. df.controller = resolve_controller(df, path)   — CSV column, else path
    4. normalize_benchmark(df)         — strip "-khpa"/"-keff" suffix

All frames from all roots are concatenated, `runtime` is derived from
`service`, the table is filtered to `--workloads`, reordered with COLUMN_ORDER,
and written to `--out`.

Usage:
    python merge_aggregate.py \\
        --roots results/sprint-1 results/sprint-A \\
        --workloads step bursty \\
        --out results/aggregate_pfc2.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# aggregate_results.py lives in instrumentation/, a sibling of scripts/.
_INSTRUMENTATION_DIR = Path(__file__).resolve().parent.parent / "instrumentation"
if str(_INSTRUMENTATION_DIR) not in sys.path:
    sys.path.insert(0, str(_INSTRUMENTATION_DIR))

from aggregate_results import (  # noqa: E402  (sys.path tweak must precede import)
    COLUMN_ORDER,
    classify_runtime,
    find_summary_files,
    normalize_benchmark,
    resolve_controller,
)

DEFAULT_WORKLOADS: tuple[str, ...] = ("step", "bursty")


def _read_root(root: Path) -> tuple[list[pd.DataFrame], int]:
    """Ingest every summary under `root`, returning (frames, n_paths).

    Mirrors aggregate_results.main()'s per-file handling: trust the CSV's own
    controller column (normalized), fall back to path inference, normalize the
    benchmark name. Empty CSVs are skipped with a warning.
    """
    paths = find_summary_files(root)
    frames: list[pd.DataFrame] = []
    for p in paths:
        try:
            df = pd.read_csv(p)
        except pd.errors.EmptyDataError:
            print(f"  skipping empty file: {p}", file=sys.stderr)
            continue
        df["controller"] = resolve_controller(df, p)
        df = normalize_benchmark(df)
        frames.append(df)
    return frames, len(paths)


def merge(roots: list[Path], workloads: list[str]) -> pd.DataFrame:
    """Build the unified, workload-filtered aggregate frame across all roots."""
    all_frames: list[pd.DataFrame] = []
    total_paths = 0
    for root in roots:
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {root}")
        frames, n_paths = _read_root(root)
        total_paths += n_paths
        all_frames.extend(frames)
        print(f"  root {root}: {n_paths} summary file(s), "
              f"{len(frames)} non-empty", file=sys.stderr)

    if not all_frames:
        raise ValueError(
            f"no usable failure_modes_summary.csv found under any of: "
            f"{[str(r) for r in roots]}"
        )

    aggregate = pd.concat(all_frames, ignore_index=True)
    aggregate["runtime"] = aggregate["service"].apply(classify_runtime)

    # Filter to the in-scope workloads (este trabajo = step, bursty by default).
    if "workload" not in aggregate.columns:
        raise ValueError("merged frame has no 'workload' column to filter on")
    wanted = set(workloads)
    aggregate = aggregate[aggregate["workload"].isin(wanted)].reset_index(drop=True)

    existing = [c for c in COLUMN_ORDER if c in aggregate.columns]
    aggregate = aggregate[existing]
    return aggregate


def _counts(df: pd.DataFrame, column: str) -> dict[str, int]:
    """value_counts as a plain {label: int} dict (no numpy scalar noise)."""
    return {str(k): int(v) for k, v in df[column].value_counts().items()}


def _print_summary(df: pd.DataFrame, roots: list[Path], total_paths: int, out: Path) -> None:
    print(f"=== merged aggregate over {len(roots)} root(s) ===")
    print(f"  rows: {len(df)}")
    if "controller" in df.columns:
        print(f"  controllers: {_counts(df, 'controller')}")
    if "workload" in df.columns:
        print(f"  workloads: {_counts(df, 'workload')}")
    if "benchmark" in df.columns:
        print(f"  benchmarks: {_counts(df, 'benchmark')}")
    if {"benchmark", "controller", "workload", "rep"}.issubset(df.columns):
        ngroups = df.groupby(
            ["benchmark", "controller", "workload", "rep"], dropna=False
        ).ngroups
        print(f"  unique (benchmark, controller, workload, rep): {ngroups}")
    print(f"\nWritten: {out}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--roots", type=Path, nargs="+", required=True,
        help="one or more result roots, e.g. results/sprint-1 results/sprint-A",
    )
    ap.add_argument(
        "--workloads", nargs="+", default=list(DEFAULT_WORKLOADS),
        help=f"workloads to keep (default: {' '.join(DEFAULT_WORKLOADS)})",
    )
    ap.add_argument("--out", type=Path, required=True, help="output aggregate CSV")
    args = ap.parse_args()

    try:
        aggregate = merge(args.roots, args.workloads)
    except (NotADirectoryError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    aggregate.to_csv(args.out, index=False)

    total_paths = sum(len(find_summary_files(r)) for r in args.roots)
    _print_summary(aggregate, args.roots, total_paths, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
