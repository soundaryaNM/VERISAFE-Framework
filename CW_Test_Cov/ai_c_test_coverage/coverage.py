from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from html import escape as _html_escape
from pathlib import Path


@dataclass(frozen=True)
class CoverageOutputs:
    output_dir: Path
    text_report: Path
    xml_report: Path
    html_report: Path | None


def _find_gcda_files(build_dir: Path) -> list[Path]:
    if not build_dir.exists():
        return []
    # GCC/MinGW gcov data files
    return list(build_dir.rglob("*.gcda"))


def _python_executable() -> str:
    # Prefer current interpreter so `-m gcovr` uses the same environment.
    return sys.executable or os.environ.get("PYTHON", "python")


def _post_process_html_for_file_links(*, html_path: Path, repo_root: Path, output_dir: Path) -> None:
    """Modify gcovr HTML to make source-file links point to file:// URLs.

    gcovr summary pages often link file entries to hashed detail pages like:
      <a href="<base>_coverage.<File>.<hash>.html">src/foo/File.cpp</a>

    In some report layouts (and when html-details pages are missing), these links break.
    This post-processing rewrites only *source file* links to open the real file on disk.
    """
    if not html_path.exists():
        return

    content = html_path.read_text(encoding="utf-8")

    def _infer_source_rel_from_location(link_text: str) -> str:
        # If the link already contains a path, trust it.
        if "/" in link_text or "\\" in link_text:
            return link_text.replace("\\", "/").lstrip("./")

        # Otherwise, infer the directory from where this HTML lives under output_dir.
        try:
            rel_parent = html_path.parent.resolve().relative_to(output_dir.resolve())
        except Exception:
            return link_text

        # Typical layout: <output_dir>/src/drivers/TrackCircuitInput.cpp/TrackCircuitInput.cpp_coverage.html
        # The last path component is the source filename.
        source_dir = rel_parent
        last = rel_parent.name
        if re.search(r"\.(?:c|cc|cpp|cxx|h|hpp)$", last, flags=re.IGNORECASE):
            source_dir = rel_parent.parent

        return (source_dir / link_text).as_posix().lstrip("./")

    def replace_link(match: re.Match[str]) -> str:
        href = match.group(1)
        link_text = match.group(2)

        # Compute repo-relative source path.
        source_rel = _infer_source_rel_from_location(link_text)
        source_abs = (repo_root / source_rel).resolve()
        file_url = f"file:///{source_abs.as_posix()}"

        # Replace only the href value; keep any other attributes unchanged.
        return match.group(0).replace(f'href="{href}"', f'href="{file_url}"', 1)

    # Match anchors whose *text* looks like a source file and whose href points to a gcovr detail-ish page.
    # Important: allow arbitrary attributes/order, because gcovr themes differ (e.g., GitHub theme).
    pattern = (
        r'<a[^>]*\bhref="([^"]*_coverage\.[^"]+\.html?)"[^>]*>'
        r'([^<]*\.(?:c|cc|cpp|cxx|h|hpp))'
        r"</a>"
    )
    new_content = re.sub(pattern, replace_link, content, flags=re.IGNORECASE)

    # Some gcovr layouts include a "List of functions" link that may not be generated
    # (e.g., per-file folder summaries in newer themes). If the target doesn't exist,
    # remove the hyperlink to avoid ERR_FILE_NOT_FOUND.
    def replace_functions_link(match: re.Match[str]) -> str:
        href = match.group(1)

        # Keep external or already-absolute links unchanged.
        if href.startswith(("http://", "https://", "file:///")):
            return match.group(0)

        target = (html_path.parent / href).resolve()
        if target.exists():
            return match.group(0)

        # Replace the whole anchor with plain text.
        return "List of functions"

    functions_pattern = r'<a[^>]*\bhref="([^"]*?_coverage\.functions\.html)"[^>]*>\s*List of functions\s*</a>'
    new_content = re.sub(functions_pattern, replace_functions_link, new_content, flags=re.IGNORECASE)

    if new_content != content:
        html_path.write_text(new_content, encoding="utf-8")


def _post_process_html_tree_for_file_links(*, output_dir: Path, repo_root: Path) -> None:
    """Apply file-link post-processing to every HTML file under output_dir."""
    if not output_dir.exists():
        return

    for html in output_dir.rglob("*.html"):
        _post_process_html_for_file_links(html_path=html, repo_root=repo_root, output_dir=output_dir)


def _normalize_repo_rel(path_like: str) -> str:
    p = (path_like or "").replace("\\", "/")
    p = p.lstrip("./")
    return p


def _load_mcdc_gaps(*, mcdc_gaps_path: Path, only_files: set[str] | None = None) -> list[dict]:
    if not mcdc_gaps_path.exists():
        return []

    try:
        payload = json.loads(mcdc_gaps_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    files_obj = payload.get("files")
    if not isinstance(files_obj, dict):
        return []

    decisions: list[dict] = []
    for file_key, items in files_obj.items():
        file_rel = _normalize_repo_rel(str(file_key))
        if only_files is not None and file_rel not in only_files:
            continue
        if not isinstance(items, list):
            continue
        for entry in items:
            if not isinstance(entry, dict):
                continue
            line = entry.get("line")
            conds = entry.get("conditions")
            if not isinstance(line, int) or not isinstance(conds, list):
                continue
            decisions.append(
                {
                    "file": file_rel,
                    "line": int(line),
                    "kind": str(entry.get("kind") or ""),
                    "expression": str(entry.get("expression") or ""),
                    "conditions": [str(c) for c in conds],
                }
            )
    return decisions


def _load_gcovr_json_index(*, gcovr_json_path: Path) -> dict[str, dict[int, dict]]:
    """Map: file -> line_number -> line_entry."""
    try:
        payload = json.loads(gcovr_json_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    files = payload.get("files")
    if not isinstance(files, list):
        return {}

    index: dict[str, dict[int, dict]] = {}
    for f in files:
        if not isinstance(f, dict):
            continue
        file_rel = _normalize_repo_rel(str(f.get("file") or ""))
        if not file_rel:
            continue
        lines = f.get("lines")
        if not isinstance(lines, list):
            continue
        by_line: dict[int, dict] = {}
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            n = ln.get("line_number")
            if isinstance(n, int):
                by_line[int(n)] = ln
        index[file_rel] = by_line
    return index


def _compute_condition_execution(*, mcdc_conditions: int, line_entry: dict) -> tuple[int, list[str]]:
    """Return (executed_conditions, uncovered_descriptions).

    We approximate "executed" as: for each condition term, both true and false outcomes were seen.
    This uses gcovr JSON "conditions" if present. If missing, we cannot compute condition-level execution.
    """
    if mcdc_conditions <= 0:
        return 0, []

    conditions = line_entry.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        return 0, ["Condition-level coverage not available in gcovr JSON for this line"]

    not_true: set[int] = set()
    not_false: set[int] = set()
    for c in conditions:
        if not isinstance(c, dict):
            continue
        nt = c.get("not_covered_true")
        nf = c.get("not_covered_false")
        if isinstance(nt, list):
            not_true.update(int(x) for x in nt if isinstance(x, int))
        if isinstance(nf, list):
            not_false.update(int(x) for x in nf if isinstance(x, int))

    executed = 0
    uncovered: list[str] = []
    for idx in range(mcdc_conditions):
        missing_true = idx in not_true
        missing_false = idx in not_false
        if not missing_true and not missing_false:
            executed += 1
            continue

        label = chr(ord("A") + idx) if idx < 26 else f"C{idx}"
        if missing_true and missing_false:
            uncovered.append(f"Condition {label}: true+false not observed")
        elif missing_true:
            uncovered.append(f"Condition {label}: true not observed")
        else:
            uncovered.append(f"Condition {label}: false not observed")

    return executed, uncovered


def _inject_mcdc_summary_into_html(
    *,
    html_path: Path,
    repo_root: Path,
    output_dir: Path,
    report_base: str,
    include_only_files: set[str] | None,
) -> None:
    """Inject an MC/DC summary into the main gcovr HTML report.

    Data sources:
      - tests/analysis/mcdc_gaps.json (MC/DC obligations)
      - <report_base>_coverage.json (gcovr execution evidence)
    """
    if not html_path.exists():
        return

    mcdc_gaps_path = repo_root / "tests" / "analysis" / "mcdc_gaps.json"
    gcovr_json_path = output_dir / f"{(report_base.strip() or 'interlocking_test_report')}_coverage.json"
    if not mcdc_gaps_path.exists() or not gcovr_json_path.exists():
        return

    decisions = _load_mcdc_gaps(mcdc_gaps_path=mcdc_gaps_path, only_files=include_only_files)
    if not decisions:
        return

    cov_index = _load_gcovr_json_index(gcovr_json_path=gcovr_json_path)

    total_decisions = len(decisions)
    total_conditions = 0
    executed_conditions = 0

    per_decision_rows: list[dict] = []
    for d in decisions:
        file_rel = d["file"]
        line = int(d["line"])
        cond_count = len(d.get("conditions") or [])
        total_conditions += cond_count

        line_entry = cov_index.get(file_rel, {}).get(line, {})
        executed, uncovered = _compute_condition_execution(mcdc_conditions=cond_count, line_entry=line_entry)
        executed_conditions += executed

        pct = (executed / cond_count * 100.0) if cond_count else 0.0
        per_decision_rows.append(
            {
                "file": file_rel,
                "line": line,
                "executed": executed,
                "total": cond_count,
                "pct": pct,
                "uncovered": uncovered,
                "expression": str(d.get("expression") or ""),
            }
        )

    overall_pct = (executed_conditions / total_conditions * 100.0) if total_conditions else 0.0

    def _fmt_pct(x: float) -> str:
        return f"{x:.1f}%"

    # Build HTML block
    rows_html = "".join(
        "".join(
            [
                "<tr>",
                f"<td><code>{_html_escape(r['file'])}:{r['line']}</code></td>",
                f"<td>{r['executed']}/{r['total']} ({_fmt_pct(r['pct'])})</td>",
                f"<td>{_html_escape('; '.join(r['uncovered']) if r['uncovered'] else 'All condition outcomes observed')}</td>",
                "</tr>",
            ]
        )
        for r in sorted(per_decision_rows, key=lambda x: (x["file"], x["line"]))
    )

    block = (
        "<!-- AITEST_MCDC_SUMMARY_START -->\n"
        "<section id=\"aitest-mcdc-summary\" style=\"margin:16px 0; padding:12px 14px; border:1px solid #d0d7de; border-radius:8px; background:#f6f8fa;\">\n"
        "  <h2 style=\"margin:0 0 8px 0;\">MC/DC Coverage Summary</h2>\n"
        "  <ul style=\"margin:0 0 10px 18px;\">\n"
        f"    <li><b>Total Decisions:</b> {total_decisions}</li>\n"
        f"    <li><b>Total MC/DC Conditions:</b> {total_conditions}</li>\n"
        f"    <li><b>Executed MC/DC Conditions:</b> {executed_conditions}</li>\n"
        f"    <li><b>MC/DC Coverage:</b> {_fmt_pct(overall_pct)}</li>\n"
        "  </ul>\n"
        "  <details>\n"
        "    <summary style=\"cursor:pointer;\"><b>Per-Decision Details</b></summary>\n"
        "    <div style=\"overflow-x:auto; margin-top:8px;\">\n"
        "      <table style=\"border-collapse:collapse; width:100%;\">\n"
        "        <thead>\n"
        "          <tr>\n"
        "            <th style=\"text-align:left; border-bottom:1px solid #d0d7de; padding:6px;\">Decision (file:line)</th>\n"
        "            <th style=\"text-align:left; border-bottom:1px solid #d0d7de; padding:6px;\">Conditions executed</th>\n"
        "            <th style=\"text-align:left; border-bottom:1px solid #d0d7de; padding:6px;\">Uncovered</th>\n"
        "          </tr>\n"
        "        </thead>\n"
        "        <tbody>\n"
        f"{rows_html}\n"
        "        </tbody>\n"
        "      </table>\n"
        "      <p style=\"margin:8px 0 0 0; color:#57606a;\">\n"
        "        Note: This section approximates MC/DC progress by checking whether each condition term has been observed as both true and false in coverage data.\n"
        "      </p>\n"
        "    </div>\n"
        "  </details>\n"
        "</section>\n"
        "<!-- AITEST_MCDC_SUMMARY_END -->\n"
    )

    content = html_path.read_text(encoding="utf-8", errors="ignore")

    # Replace existing injected block if present.
    content2 = re.sub(
        r"<!-- AITEST_MCDC_SUMMARY_START -->.*?<!-- AITEST_MCDC_SUMMARY_END -->\n?",
        block,
        content,
        flags=re.DOTALL,
    )

    if content2 == content:
        # Insert after <body> if possible, else prepend.
        m = re.search(r"<body[^>]*>", content, flags=re.IGNORECASE)
        if m:
            insert_at = m.end()
            content2 = content[:insert_at] + "\n" + block + content[insert_at:]
        else:
            content2 = block + content

    if content2 != content:
        html_path.write_text(content2, encoding="utf-8")


def _promote_single_file_detail_html(output_dir: Path, report_base: str, source_basename: str) -> None:
    """If gcovr generated an html-details page for a single file, copy it onto the main summary HTML.

    This makes opening <report_base>_coverage.html jump straight into the line-by-line view
    instead of the directory summary table.
    """
    safe_base = report_base.strip() or "interlocking_test_report"
    summary = output_dir / f"{safe_base}_coverage.html"
    if not summary.exists():
        return

    # gcovr detail pages typically look like: <base>_coverage.<FileName>.<hash>.html
    # and may also emit: <base>_coverage.functions.html
    # Prefer the per-file detail page for the requested file.
    detail_candidates = sorted(output_dir.glob(f"{safe_base}_coverage.{source_basename}.*.html"))

    # Fallback for older/newer naming variants: pick any non-functions html that contains the file name.
    if not detail_candidates:
        candidates = sorted(output_dir.glob(f"{safe_base}_coverage.*.html"))
        candidates = [
            p
            for p in candidates
            if p.name != summary.name
            and source_basename in p.name
            and not p.name.endswith(".functions.html")
            and ".functions." not in p.name
        ]
        detail_candidates = candidates

    if not detail_candidates:
        return

    promoted = detail_candidates[0]
    summary.write_text(promoted.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")

    # Keep the functions coverage page (it can be useful to see call counts),
    # but rewrite its links to point to the promoted stable summary HTML so navigation works.
    functions_page = output_dir / f"{safe_base}_coverage.functions.html"
    try:
        if functions_page.exists():
            fp = functions_page.read_text(encoding="utf-8", errors="ignore")
            # Example href in functions page:
            #   <a href="<base>_coverage.<File>.<hash>.html#l5">...
            # After promotion, make it:
            #   <a href="<base>_coverage.html#l5">...
            fp = re.sub(
                rf'href="{re.escape(safe_base)}_coverage\.{re.escape(source_basename)}\.[^\"]+\.html#',
                f'href="{safe_base}_coverage.html#',
                fp,
            )
            functions_page.write_text(fp, encoding="utf-8")
    except Exception:
        pass

    # Keep hashed per-file detail pages so downstream tooling (e.g. run_demo)
    # can rehydrate stable src/<path>/index.html copies. Removing them here
    # breaks that workflow when single-file coverage is requested.


def run_gcovr(
    *,
    repo_root: Path,
    build_dir: Path,
    output_dir: Path,
    report_base: str = "interlocking_test_report",
    html: bool = False,
    html_details: bool = False,
    filters: list[str] | None = None,
    include_files: list[Path] | None = None,
    exclude_tests: bool = True,
    fail_if_no_data: bool = True,
) -> CoverageOutputs:
    """Run gcovr against an existing build directory.

    Notes:
    - Coverage data only exists if the project was built with coverage flags and tests were executed.
    - We intentionally do not reconfigure/rebuild here; CW_Test_Run is responsible for execution.
    """

    repo_root = repo_root.resolve()
    build_dir = build_dir.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_base = report_base.strip() or "interlocking_test_report"
    text_report = output_dir / f"{safe_base}_coverage.txt"
    xml_report = output_dir / f"{safe_base}_coverage.xml"
    html_report = output_dir / f"{safe_base}_coverage.html" if html else None
    json_report = output_dir / f"{safe_base}_coverage.json"

    gcda_files = _find_gcda_files(build_dir)
    if not gcda_files and fail_if_no_data:
        raise RuntimeError(
            "No coverage data (*.gcda) found. Rebuild with coverage flags and re-run tests before running CW_Test_Cov."
        )

    # Use 'python -m gcovr' so we don't rely on PATH.
    py = _python_executable()

    base_args = [
        py,
        "-m",
        "gcovr",
        "-r",
        str(repo_root),
        "--object-directory",
        str(build_dir),
        "--print-summary",
    ]

    # Optional include-only filters derived from specific file paths.
    # NOTE: gcovr requires forward slashes in filter regexes (even on Windows).
    if include_files:
        for p in include_files:
            try:
                rel = p.resolve().relative_to(repo_root)
            except Exception:
                rel = p
            rel_posix = str(rel).replace("\\", "/")
            escaped = re.escape(rel_posix)
            base_args += ["--filter", rf".*{escaped}$"]

    # Optional include filters.
    if filters:
        for f in filters:
            if f:
                base_args += ["--filter", str(f)]

    # Exclude third-party sources pulled into the build tree (e.g., FetchContent googletest)
    # so the report reflects the target repo's code and tests.
    default_excludes = [
        str((build_dir / "_deps").as_posix()),
        str((build_dir / "CMakeFiles").as_posix()),
        str((build_dir / "Testing").as_posix()),
    ]

    # Exclude repo test sources by default. These are scaffolding, not production coverage.
    if exclude_tests:
        default_excludes += [
            str((repo_root / "tests").as_posix()),
            r".*/test_.*\.(c|cc|cpp|cxx|h|hpp)$",
        ]

    for ex in default_excludes:
        base_args += ["--exclude", ex]

    # Text report
    with open(text_report, "w", encoding="utf-8") as f:
        subprocess.run(base_args, cwd=str(repo_root), stdout=f, stderr=subprocess.STDOUT, check=False, text=True)

    # XML report
    subprocess.run(
        base_args + ["--xml", "-o", str(xml_report)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )

    # JSON report (used for MC/DC-from-coverage correlation).
    # We generate this unconditionally so we can inject MC/DC summary into HTML by default.
    subprocess.run(
        base_args + ["--decisions", "--json-pretty", "--json", str(json_report)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )

    # Optional HTML
    if html_report is not None:
        html_args = base_args + ["--html"]
        if html_details:
            html_args.append("--html-details")
        subprocess.run(
            html_args + ["-o", str(html_report)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if html_details and include_files and len(include_files) == 1:
            _promote_single_file_detail_html(output_dir, safe_base, include_files[0].name)

        # Always post-process generated HTML to make file links robust.
        # This fixes broken links when detail pages are missing and also updates per-file subfolder reports.
        _post_process_html_tree_for_file_links(output_dir=output_dir, repo_root=repo_root)

        # Inject MC/DC summary derived from coverage + mcdc_gaps.json.
        only_files: set[str] | None = None
        if include_files:
            only_files = set()
            for p in include_files:
                try:
                    rel = p.resolve().relative_to(repo_root)
                except Exception:
                    rel = p
                only_files.add(_normalize_repo_rel(str(rel)))

        _inject_mcdc_summary_into_html(
            html_path=html_report,
            repo_root=repo_root,
            output_dir=output_dir,
            report_base=safe_base,
            include_only_files=only_files,
        )

    return CoverageOutputs(
        output_dir=output_dir,
        text_report=text_report,
        xml_report=xml_report,
        html_report=html_report,
    )
