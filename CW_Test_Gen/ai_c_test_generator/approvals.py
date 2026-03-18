"""Section-based approvals registry for AI-generated tests.

V2 model:
- Test files may contain multiple generated sections.
- Each section is tracked in <repo>/tests/.approvals.json.
- Build/run is blocked unless all ACTIVE sections are approved.

This module is intentionally dependency-free (std-lib only).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SECTION_HEADER_SENTINEL = "AI-TEST-SECTION"
_LEGACY_SECTION_HEADER_SENTINEL = "AI-TESTGEN-SECTION"


_GTEST_PREFIX_RE = re.compile(
    r"(?m)^(\s*)(TEST|TEST_F|TEST_P)\s*\(\s*(?P<suite>[A-Za-z_][A-Za-z0-9_:]*)\s*,\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\)"
)


def _uniquify_gtest_fixtures_and_names(
    code: str,
    suite_prefix: str,
    *,
    preferred_fixture_base: str | None = None,
) -> str:
    """Ensure section-local GTest identifiers don't collide across sections.

    - Always prefix the TEST/TEST_F/TEST_P *test name*.
    - For TEST_F/TEST_P, also rename the fixture/suite identifier to a unique name
      and rewrite matching class/struct declarations.

    This prevents GoogleTest runtime errors where multiple translation-unit sections
    define the same test suite name with different fixture classes.
    """

    normalized = _normalize_newlines(code)
    if not suite_prefix:
        return normalized

    fixtures: set[str] = set()
    fixture_new_base: dict[str, str] = {}

    # Only enforce a single standardized fixture base name when the section uses exactly
    # one fixture type. Otherwise, keep per-fixture naming to avoid collisions.
    enforce_preferred_fixture = bool(preferred_fixture_base)

    def _macro_repl(m: re.Match[str]) -> str:
        indent = m.group(1)
        macro = m.group(2)
        suite = m.group("suite")
        name = m.group("name")

        # Prefix the test name (2nd arg) for uniqueness.
        new_name = name if name.startswith(suite_prefix) else f"{suite_prefix}{name}"

        # For fixture-based macros, also uniquify the fixture identifier.
        new_suite = suite
        if macro in ("TEST_F", "TEST_P"):
            # Avoid double-prefixing.
            if not suite.startswith(suite_prefix):
                fixtures.add(suite)
                # Tentatively map fixture -> preferred base if requested.
                if preferred_fixture_base:
                    fixture_new_base[suite] = preferred_fixture_base
                new_suite_base = fixture_new_base.get(suite, suite)
                new_suite = f"{suite_prefix}{new_suite_base}"

        return f"{indent}{macro}({new_suite}, {new_name})"

    rewritten = _GTEST_PREFIX_RE.sub(_macro_repl, normalized)

    # If multiple fixtures exist, do not enforce a single preferred base name.
    if enforce_preferred_fixture and len(fixtures) != 1:
        fixture_new_base.clear()

    # Rename fixture class/struct declarations to match rewritten TEST_F/TEST_P.
    for fx in sorted(fixtures, key=len, reverse=True):
        base = fixture_new_base.get(fx, fx)
        new_fx = f"{suite_prefix}{base}"
        rewritten = re.sub(
            rf"\b(class|struct)\s+{re.escape(fx)}\b",
            rf"\1 {new_fx}",
            rewritten,
        )

    return rewritten


def _prefix_gtest_suite_names(code: str, suite_prefix: str) -> str:
    """Make section-generated GoogleTest names unique without breaking compilation.

    IMPORTANT:
    - We do NOT prefix the suite/fixture argument.
      For TEST_F, the first argument is a C++ *type*; modifying it breaks compilation.
    - Instead, we prefix the *test name* argument to keep names unique across appended sections.
    """

    def _repl(m: re.Match[str]) -> str:
        indent = m.group(1)
        macro = m.group(2)
        suite = m.group("suite")
        name = m.group("name")
        if not suite_prefix:
            return m.group(0)
        # Avoid double-prefixing if regeneration appends a new section using the same prefix.
        if name.startswith(suite_prefix):
            return m.group(0)
        return f"{indent}{macro}({suite}, {suite_prefix}{name})"

    return _GTEST_PREFIX_RE.sub(_repl, _normalize_newlines(code))


def _split_preprocessor_lines(text: str) -> tuple[list[str], str]:
    """Extract preprocessor-ish lines and return (directives, remainder).

    We treat any line starting with '#' (after whitespace) as a directive.
    """
    directives: list[str] = []
    remainder: list[str] = []
    for ln in _normalize_newlines(text).split("\n"):
        if ln.lstrip().startswith("#"):
            directives.append(ln)
        else:
            remainder.append(ln)
    return directives, "\n".join(remainder).strip() + "\n"


def build_section_block(
    *,
    raw_test_code: str,
    section_name: str,
    source_rel: str,
    section_namespace: str,
    suite_prefix: str,
    preferred_fixture_base: str | None = None,
    approved: bool = False,
    reviewed_by: str | None = None,
    reviewed_at_iso: str | None = None,
    meta_name: str | None = None,
) -> tuple[str, str]:
    """Create a section block + return (block_text, section_sha256).

    The section hash is computed over the section BODY (everything after the header comment).
    """

    directives, body = _split_preprocessor_lines(raw_test_code)

    # Best-effort: ensure standard library includes required by common generated patterns.
    if ("std::unique_ptr" in body or "std::make_unique" in body) and not any(
        re.search(r"(?m)^\s*#\s*include\s*<memory>\s*$", ln or "") for ln in directives
    ):
        directives.append("#include <memory>")

    body = _uniquify_gtest_fixtures_and_names(body, suite_prefix, preferred_fixture_base=preferred_fixture_base)

    section_body_parts: list[str] = []
    if directives:
        section_body_parts.append("\n".join(directives).rstrip() + "\n")

    section_body_parts.append(f"namespace {section_namespace} {{\n")
    section_body_parts.append(body.rstrip() + "\n")
    section_body_parts.append("}  // namespace\n")

    section_body = "\n".join(section_body_parts).lstrip("\n")
    section_sha256 = sha256_text(section_body)

    status = "Approved" if approved else "Pending Review"

    rb = (reviewed_by or "").strip()
    ra = (reviewed_at_iso or "").strip()
    reviewed_by_line = f"Reviewed-By: {rb}\n" if rb else "Reviewed-By:\n"
    reviewed_at_line = f"Reviewed-At: {ra}\n" if ra else "Reviewed-At:\n"
    # Include an optional Name field when the caller passes an explicit
    # identifier. Some callers previously set section_name to function-like
    # values (e.g. FUNC_evaluate) which confused the approvals CLI. We now
    # intentionally write the user-facing Section label (e.g. BASE_TESTS) and
    # include a `Name:` meta field when an internal name differs.
    section_label = section_name or ""
    name_line = f"Name: {meta_name}\n" if meta_name else ""
    parts: list[str] = []
    parts.append(f"/* {SECTION_HEADER_SENTINEL}\n")
    parts.append(f"Section: {section_label}\n")
    if name_line:
        parts.append(name_line)
    parts.extend([
        f"Source: {source_rel}\n",
        f"Status: {status}\n",
        f"Approved: {'true' if approved else 'false'}\n",
        reviewed_by_line,
        reviewed_at_line,
        "*/\n",
    ])
    header = "".join(parts)

    return header + section_body, section_sha256


def append_section_to_file(test_file_path: Path, section_block: str) -> None:
    test_file_path.parent.mkdir(parents=True, exist_ok=True)
    if test_file_path.exists():
        existing = _normalize_newlines(test_file_path.read_text(encoding="utf-8"))
        if existing and not existing.endswith("\n"):
            existing += "\n"
        combined = existing + "\n" + section_block
        test_file_path.write_text(combined, encoding="utf-8", newline="\n")
        return
    test_file_path.write_text(section_block, encoding="utf-8", newline="\n")


def _normalize_newlines(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _canonical_section_body(text: str) -> str:
    """Canonicalize section body text for stable hashing.

    Important: when additional sections are appended to a test file, there may be
    extra blank lines between the end of one section and the next section header.
    Those separator newlines must NOT change the previous section's hash.

    We therefore:
    - normalize newlines
    - strip trailing whitespace/newlines
    - enforce a single trailing newline (if non-empty)
    """

    normalized = _normalize_newlines(text)
    stripped = normalized.rstrip()
    if not stripped:
        return ""
    return stripped + "\n"


def sha256_text(text: str) -> str:
    data = _normalize_newlines(text).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    # Newline normalization for stable hashing across platforms.
    try:
        text = data.decode("utf-8")
        return sha256_text(text)
    except Exception:
        return hashlib.sha256(data).hexdigest()


def _utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def _to_posix_rel(path: Path) -> str:
    return path.as_posix().lstrip("/")


def repo_relpath(path: Path, repo_root: Path) -> str:
    try:
        rel = path.resolve().relative_to(repo_root.resolve())
        return _to_posix_rel(rel)
    except Exception:
        return _to_posix_rel(Path(path.name))


@dataclass(frozen=True)
class ParsedSection:
    section_sha256: str
    header_start: int
    header_end: int
    body_start: int
    body_end: int
    meta: dict[str, str]


_SECTION_HEADER_RE = re.compile(
    r"(?ms)^/\*\s*(?:"
    + re.escape(SECTION_HEADER_SENTINEL)
    + r"|"
    + re.escape(_LEGACY_SECTION_HEADER_SENTINEL)
    + r")\s*\n(?P<body>.*?)\*/\s*\n"
)


def parse_sections(file_text: str) -> list[ParsedSection]:
    """Parse sections from a sectioned test file.

    Sections look like:

        /* AI-TESTGEN-SECTION
        Key: Value
        ...
        */
        <section body...>

    The section body ends right before the next section header, or EOF.
    """

    text = _normalize_newlines(file_text)
    matches = list(_SECTION_HEADER_RE.finditer(text))
    sections: list[ParsedSection] = []

    for idx, m in enumerate(matches):
        header_start = m.start()
        header_end = m.end()

        # Body starts after header block.
        body_start = header_end
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)

        meta_block = m.group("body")
        meta: dict[str, str] = {}
        for line in _normalize_newlines(meta_block).split("\n"):
            line = line.strip()
            if not line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()

        body_text = _canonical_section_body(text[body_start:body_end])
        section_sha256 = sha256_text(body_text)

        sections.append(
            ParsedSection(
                section_sha256=section_sha256,
                header_start=header_start,
                header_end=header_end,
                body_start=body_start,
                body_end=body_end,
                meta=meta,
            )
        )

    return sections


def update_section_header(
    file_text: str,
    *,
    section_sha256: str,
    approved: bool,
    reviewed_by: str | None = None,
    reviewed_at_iso: str | None = None,
) -> str:
    """Update Approved/Reviewed-* fields for a single section by hash."""

    text = _normalize_newlines(file_text)
    sections = parse_sections(text)

    target: ParsedSection | None = None
    for s in sections:
        if s.section_sha256 == section_sha256:
            target = s
            break

    if not target:
        raise ValueError("Section not found")

    header_text = text[target.header_start:target.header_end]

    # Replace or insert fields inside the header comment body.
    def _set_field(block: str, key: str, value: str) -> str:
        # Operate line-by-line to preserve layout.
        lines = _normalize_newlines(block).split("\n")
        out: list[str] = []
        replaced = False
        for ln in lines:
            if ln.strip().startswith(f"{key}:"):
                out.append(f"{key}: {value}")
                replaced = True
            else:
                out.append(ln)
        if not replaced:
            # Insert before closing */ line if possible.
            try:
                end_idx = next(i for i, ln in enumerate(out) if ln.strip().startswith("*/"))
                out.insert(end_idx, f"{key}: {value}")
            except StopIteration:
                out.append(f"{key}: {value}")
        return "\n".join(out)

    updated = header_text
    updated = _set_field(updated, "Approved", "true" if approved else "false")
    updated = _set_field(updated, "Status", "Approved" if approved else "Pending Review")

    if reviewed_by is not None:
        updated = _set_field(updated, "Reviewed-By", reviewed_by)
    if reviewed_at_iso is not None:
        updated = _set_field(updated, "Reviewed-At", reviewed_at_iso)

    return text[: target.header_start] + updated + text[target.header_end :]


class ApprovalsRegistry:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self.path = self.repo_root / "tests" / ".approvals.json"
        self.data: dict[str, Any] = {"version": 1, "sections": {}}

    def load(self) -> None:
        if not self.path.exists():
            self.data = {"version": 1, "sections": {}}
            return
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(self.data, dict):
            self.data = {"version": 1, "sections": {}}
        self.data.setdefault("version", 1)
        self.data.setdefault("sections", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def iter_sections(self) -> Iterable[dict[str, Any]]:
        sections = self.data.get("sections", {})
        if isinstance(sections, dict):
            for _, val in sections.items():
                if isinstance(val, dict):
                    yield val

    def get_active_pending(self) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for s in self.iter_sections():
            if s.get("active") is True and s.get("approved") is not True:
                pending.append(s)
        return pending

    def deactivate_active_for_source(self, *, source_rel: str, reason: str = "") -> None:
        for s in self.iter_sections():
            if s.get("source_rel") == source_rel and s.get("active") is True:
                s["active"] = False
                s.setdefault("deactivated_at", _utc_now_iso())
                if reason:
                    s.setdefault("deactivated_reason", reason)

    def deactivate_if_source_changed(self, *, source_rel: str, new_source_sha256: str) -> bool:
        """Deactivate all active sections for this source if source hash changed."""
        any_active = False
        for s in self.iter_sections():
            if s.get("source_rel") == source_rel and s.get("active") is True:
                any_active = True
                if s.get("source_sha256") != new_source_sha256:
                    self.deactivate_active_for_source(source_rel=source_rel, reason="source_changed")
                    return True
        return any_active is False

    def upsert_section(
        self,
        *,
        section_sha256: str,
        name: str,
        test_file_rel: str,
        source_rel: str,
        source_sha256: str,
        approved: bool,
        active: bool,
        kind: str | None = None,
        decision: dict[str, Any] | None = None,
    ) -> None:
        sections = self.data.setdefault("sections", {})
        if not isinstance(sections, dict):
            sections = {}
            self.data["sections"] = sections

        existing = sections.get(section_sha256)
        if isinstance(existing, dict):
            existing.update(
                {
                    "name": name,
                    "test_file_rel": test_file_rel,
                    "source_rel": source_rel,
                    "source_sha256": source_sha256,
                    "approved": bool(approved),
                    "active": bool(active),
                    "kind": kind or existing.get("kind") or "base",
                    "decision": decision or existing.get("decision"),
                }
            )
            existing.setdefault("updated_at", _utc_now_iso())
            return

        sections[section_sha256] = {
            "section_sha256": section_sha256,
            "name": name,
            "test_file_rel": test_file_rel,
            "source_rel": source_rel,
            "source_sha256": source_sha256,
            "approved": bool(approved),
            "active": bool(active),
            "kind": kind or "base",
            "decision": decision,
            "created_at": _utc_now_iso(),
        }

    def approve(
        self,
        *,
        section_sha256: str,
        reviewed_by: str,
        reviewed_at_iso: str | None = None,
    ) -> None:
        reviewed_at_iso = reviewed_at_iso or _utc_now_iso()
        sections = self.data.get("sections", {})
        if not isinstance(sections, dict) or section_sha256 not in sections:
            raise ValueError("Unknown section")
        s = sections[section_sha256]
        if not isinstance(s, dict):
            raise ValueError("Invalid registry entry for section")
        s["approved"] = True
        s["reviewed_by"] = reviewed_by
        s["reviewed_at"] = reviewed_at_iso
