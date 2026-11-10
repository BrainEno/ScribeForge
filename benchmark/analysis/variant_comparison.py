"""Reproducible synthetic A/B/C/D variant comparison (issue #81).

Prints the measured variant table for the deterministic synthetic corpus in
``scribeforge.benchmark.variants``. Run from the repository root:

    python benchmark/analysis/variant_comparison.py

The corpus, the scripted engines and the scripted VLM are fully deterministic,
so any third party gets the same numbers on any machine. These are pipeline
mechanism measurements (what the decision layer accepts or leaves for humans),
not OCR accuracy: the engines are scripts, not models.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from scribeforge.benchmark.variants import (
    CORRUPTED_LINE_INDEXES,
    PAGES,
    build_synthetic_corpus,
    run_variant_comparison,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-corpus",
        type=Path,
        default=None,
        help="Write the synthetic corpus here instead of a temporary directory.",
    )
    args = parser.parse_args()

    if args.keep_corpus is not None:
        manifest_path = build_synthetic_corpus(args.keep_corpus)
        root = args.keep_corpus
    else:
        temporary = tempfile.TemporaryDirectory(prefix="scribeforge-variants-")
        root = Path(temporary.name)
        manifest_path = build_synthetic_corpus(root)
    comparison = run_variant_comparison(root, manifest_path)

    print(
        "| Variant | Cases | Macro CER | Micro CER | Accepted pairs | Unresolved pairs | False-accepted pairs |"
    )
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for report in comparison.reports:
        aggregate = report.aggregate
        micro_cer = aggregate.micro_cer
        print(
            f"| {report.engine} | {aggregate.case_count} | {aggregate.mean_cer:.4f} | "
            f"{'-' if micro_cer is None else f'{micro_cer:.4f}'} | "
            f"{aggregate.accepted_pairs if aggregate.accepted_pairs is not None else '-'} | "
            f"{aggregate.unresolved_pairs if aggregate.unresolved_pairs is not None else '-'} | "
            f"{aggregate.false_accepted_pairs if aggregate.false_accepted_pairs is not None else '-'} |"
        )
    print()
    print(
        f"Corpus: {PAGES} synthetic pages, primary engine misreads line indexes "
        f"{list(CORRUPTED_LINE_INDEXES)} on every page; secondary engine and scripted VLM "
        "read the reference verbatim."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
