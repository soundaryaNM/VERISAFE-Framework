#!/usr/bin/env python3
"""Create the mandatory manual review approval flag(s).

This writes the exact, required contents to per-test approval files under:
    <repo>/tests/review/APPROVED.<test_filename>.flag

The runner enforces an exact match against these files.
"""

from __future__ import annotations

import argparse
from pathlib import Path


REQUIRED_CONTENT = "approved = true\nreviewed_by = <human_name>\ndate = <ISO date>\n"


def _parse_generated_test_files(repo_root: Path) -> list[Path]:
    review_required = repo_root / "tests" / "review" / "review_required.md"
    try:
        text = review_required.read_text(encoding="utf-8").replace("\r\n", "\n")
    except Exception:
        return []

    lines = text.split("\n")
    in_section = False
    generated: list[Path] = []

    for line in lines:
        if line.strip() == "## Generated test files":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue

        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        item = stripped.lstrip("-").strip()
        if not item or item == "(none)":
            continue

        item = item.replace("\\", "/")
        generated.append(repo_root / Path(item))

    return generated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create tests/review/APPROVED.flag with the exact required contents"
    )
    parser.add_argument(
        "--repo-path",
        default="RailwaySignalSystem",
        help="Path to the target repo (default: RailwaySignalSystem)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite APPROVED.flag if it already exists",
    )
    parser.add_argument(
        "--test-file",
        default=None,
        help="Optional: approve only a specific generated test file (e.g., tests/test_Interlocking.cpp)",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_path).resolve()
    review_dir = repo_root / "tests" / "review"
    review_dir.mkdir(parents=True, exist_ok=True)

    if args.test_file:
        generated_tests = [repo_root / Path(str(args.test_file))]
    else:
        generated_tests = _parse_generated_test_files(repo_root)

    if not generated_tests:
        print(f"No generated tests found (expected {review_dir / 'review_required.md'}).")
        return 2

    wrote_any = False
    for test_path in generated_tests:
        approval_name = f"APPROVED.{test_path.name}.flag"
        approved_path = review_dir / approval_name

        if approved_path.exists() and not args.overwrite:
            print(f"Approval already exists: {approved_path}")
            continue

        approved_path.write_text(REQUIRED_CONTENT, encoding="utf-8", newline="\n")
        print(f"Wrote: {approved_path}")
        wrote_any = True

    if not wrote_any:
        print("No approval files written (use --overwrite to replace existing approvals).")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
