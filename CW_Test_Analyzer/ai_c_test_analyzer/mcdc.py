"""Minimal MC/DC gap analysis (v1).

This does not compute true MC/DC achieved from execution traces (that requires
instrumentation/tooling). Instead, it identifies candidate decisions with
multiple atomic conditions and records what *would* need MC/DC pairs.

Output is intended to drive AI generation of MC/DC-focused test sections.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Decision:
    kind: str  # if/while/for/ternary
    file_rel: str
    line: int  # 1-based
    expression: str
    conditions: list[str]


def _normalize_newlines(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _strip_outer_parens(expr: str) -> str:
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")"):
        depth = 0
        ok = True
        for i, ch in enumerate(expr):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i != len(expr) - 1:
                    ok = False
                    break
        if not ok:
            break
        expr = expr[1:-1].strip()
    return expr


def _split_top_level(expr: str, op: str) -> list[str]:
    """Split expr on top-level operator occurrences ('&&' or '||')."""
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    expr = _normalize_newlines(expr)

    in_str: str | None = None
    escape = False

    while i < len(expr):
        ch = expr[i]

        if in_str:
            buf.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
            i += 1
            continue

        if ch in ("\"", "'"):
            in_str = ch
            buf.append(ch)
            i += 1
            continue

        if ch == "(":
            depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch == ")":
            depth = max(depth - 1, 0)
            buf.append(ch)
            i += 1
            continue

        if depth == 0 and expr.startswith(op, i):
            part = "".join(buf).strip()
            if part:
                out.append(part)
            buf = []
            i += len(op)
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        out.append(tail)

    return out


def extract_atomic_conditions(expr: str) -> list[str]:
    """Extract atomic conditions from a boolean expression.

    Heuristic: split by top-level '||' then '&&'.
    Returns flattened list of leaf expressions.
    """
    expr = _strip_outer_parens(expr)

    # First split OR groups.
    or_parts = _split_top_level(expr, "||")
    leaves: list[str] = []
    for part in or_parts:
        and_parts = _split_top_level(part, "&&")
        for leaf in and_parts:
            leaf = _strip_outer_parens(leaf.strip())
            if leaf:
                leaves.append(leaf)

    # De-dup while preserving order.
    seen: set[str] = set()
    uniq: list[str] = []
    for c in leaves:
        if c in seen:
            continue
        seen.add(c)
        uniq.append(c)
    return uniq


_IF_RE = re.compile(r"\bif\s*\(")
_WHILE_RE = re.compile(r"\bwhile\s*\(")
_FOR_RE = re.compile(r"\bfor\s*\(")
_SWITCH_RE = re.compile(r"\bswitch\s*\(")


def _find_unquoted_char(text: str, needle: str) -> int:
    in_str: str | None = None
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
            continue

        if ch in ("\"", "'"):
            in_str = ch
            continue
        if ch == needle:
            return i
    return -1


def _extract_ternary_condition_from_line(line: str) -> str | None:
    # Very small heuristic: handles common single-line patterns like:
    #   x = cond ? a : b;
    #   return cond ? a : b;
    # Not a full parser; intended only for demo-gap detection.

    # Strip single-line comments.
    if "//" in line:
        line = line.split("//", 1)[0]

    q = _find_unquoted_char(line, "?")
    if q == -1:
        return None
    # Require ':' after '?'
    tail = line[q + 1 :]
    if _find_unquoted_char(tail, ":") == -1:
        return None

    prefix = line[:q].strip()
    if not prefix:
        return None

    # Prefer the part after 'return'.
    if "return" in prefix:
        prefix = prefix.split("return", 1)[1].strip()

    # Prefer the RHS of a simple assignment (ignore ==, !=, <=, >=, etc.).
    assign_idx = -1
    for i in range(len(prefix) - 1, -1, -1):
        if prefix[i] != "=":
            continue
        left = prefix[i - 1] if i - 1 >= 0 else ""
        right = prefix[i + 1] if i + 1 < len(prefix) else ""
        if left == "=" or right == "=":
            continue
        assign_idx = i
        break
    if assign_idx != -1:
        prefix = prefix[assign_idx + 1 :].strip()

    # If ternary appears as a function argument, prefer the last argument fragment.
    # Example: foo(x, cond ? a : b) -> prefix is "foo(x, cond"; we want "cond".
    def _paren_depth(s: str) -> int:
        depth = 0
        in_str: str | None = None
        escape = False
        for ch in s:
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == in_str:
                    in_str = None
                continue
            if ch in ("\"", "'"):
                in_str = ch
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(depth - 1, 0)
        return depth

    end_depth = _paren_depth(prefix)
    if "," in prefix:
        depth = 0
        in_str: str | None = None
        escape = False
        for i in range(len(prefix) - 1, -1, -1):
            ch = prefix[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == in_str:
                    in_str = None
                continue
            if ch in ("\"", "'"):
                in_str = ch
                continue
            if ch == ")":
                depth += 1
                continue
            if ch == "(":
                depth = max(depth - 1, 0)
                continue
            if ch == "," and depth == 0 and end_depth >= 1:
                # We are inside a call; find comma separating args.
                prefix = prefix[i + 1 :].strip()
                break

    prefix = _strip_outer_parens(prefix)
    return prefix or None


def _extract_paren_expr(text: str, start_idx: int) -> tuple[str, int] | None:
    """Given index at '(' return (inside, end_idx_after_closing_paren)."""
    if start_idx >= len(text) or text[start_idx] != "(":
        return None
    depth = 0
    i = start_idx
    buf: list[str] = []

    in_str: str | None = None
    escape = False

    while i < len(text):
        ch = text[i]

        if in_str:
            buf.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
            i += 1
            continue

        if ch in ("\"", "'"):
            in_str = ch
            buf.append(ch)
            i += 1
            continue

        if ch == "(":
            depth += 1
            if depth > 1:
                buf.append(ch)
            i += 1
            continue
        if ch == ")":
            depth -= 1
            if depth == 0:
                return ("".join(buf), i + 1)
            buf.append(ch)
            i += 1
            continue

        buf.append(ch)
        i += 1

    return None


def analyze_repo_mcdc(repo_root: Path, *, source_root: Path | None = None) -> dict:
    repo_root = repo_root.resolve()
    src_root = (source_root or (repo_root / "src")).resolve()

    files: list[Path] = []
    if src_root.exists():
        for ext in ("*.c", "*.cc", "*.cpp", "*.cxx", "*.h", "*.hpp"):
            files.extend(src_root.rglob(ext))

    decisions_by_file: dict[str, list[dict]] = {}

    for path in sorted(set(files)):
        try:
            rel = path.relative_to(repo_root).as_posix()
            text = _normalize_newlines(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue

        file_decisions: list[Decision] = []

        # Scan with simple regex for control keywords, then extract parentheses expression.
        for kind, rx in (("if", _IF_RE), ("while", _WHILE_RE), ("for", _FOR_RE)):
            for m in rx.finditer(text):
                # m ends right after '(' due to the pattern.
                start = m.end() - 1
                extracted = _extract_paren_expr(text, start)
                if not extracted:
                    continue
                expr, _ = extracted

                # For 'for(...)', decision is the middle clause if possible.
                if kind == "for":
                    parts = _split_top_level(expr, ";")
                    if len(parts) >= 2:
                        expr = parts[1]

                expr = expr.strip()
                if not expr:
                    continue

                conditions = extract_atomic_conditions(expr)
                if len(conditions) < 2:
                    continue

                line = text.count("\n", 0, m.start()) + 1
                file_decisions.append(
                    Decision(kind=kind, file_rel=rel, line=line, expression=expr, conditions=conditions)
                )

        # Scan switch(expr) and treat expr as a decision input.
        for m in _SWITCH_RE.finditer(text):
            start = m.end() - 1
            extracted = _extract_paren_expr(text, start)
            if not extracted:
                continue
            expr, _ = extracted
            expr = expr.strip()
            if not expr:
                continue
            conditions = extract_atomic_conditions(expr)
            if not conditions:
                continue
            line = text.count("\n", 0, m.start()) + 1
            file_decisions.append(
                Decision(kind="switch", file_rel=rel, line=line, expression=expr, conditions=conditions)
            )

        # Heuristic scan for single-line ternary operator decisions.
        in_block_comment = False
        for line_no, raw_line in enumerate(text.split("\n"), start=1):
            line = raw_line
            if in_block_comment:
                end = line.find("*/")
                if end == -1:
                    continue
                line = line[end + 2 :]
                in_block_comment = False

            start = line.find("/*")
            if start != -1:
                end = line.find("*/", start + 2)
                if end == -1:
                    # Keep prefix before comment start and mark remainder in-comment.
                    line = line[:start]
                    in_block_comment = True
                else:
                    line = line[:start] + line[end + 2 :]

            cond_expr = _extract_ternary_condition_from_line(line)
            if not cond_expr:
                continue

            conditions = extract_atomic_conditions(cond_expr)
            if not conditions:
                continue

            file_decisions.append(
                Decision(
                    kind="ternary",
                    file_rel=rel,
                    line=line_no,
                    expression=cond_expr,
                    conditions=conditions,
                )
            )

        if file_decisions:
            decisions_by_file[rel] = [
                {
                    "kind": d.kind,
                    "line": d.line,
                    "expression": d.expression,
                    "conditions": d.conditions,
                    "required_pairs_estimate": (
                        max(1, len(d.conditions)) if d.kind in ("switch", "ternary") else max(0, len(d.conditions))
                    ),
                }
                for d in file_decisions
            ]

    return {
        "version": 1,
        "generated_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        "source_root": src_root.as_posix() if src_root.exists() else None,
        "files": decisions_by_file,
    }
