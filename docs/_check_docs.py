#!/usr/bin/env python3
"""VoiceGateway docs validator: the deterministic gate for the docs restructure loop.

Runs the seven rules from the restructure spec
(docs/superpowers/specs/2026-07-09-docs-restructure-design.md). Exits non-zero on any
failure and prints every violation. Stdlib only; locates the docs dir relative to this
file, so it works from any CWD.

    python3 docs/_check_docs.py                       # full: content rules on ALL pages
    python3 docs/_check_docs.py index guide/attach    # scoped: content rules on those pages

Structural rules (1, 2, 3) are ALWAYS global: the whole nav must be valid, every page
mapped, no orphans. The content/link rules (4, 5, 6, 7) apply to the SCOPE: the page
slugs passed as arguments, or every page when no arguments are given. This mirrors the
engine loop's "prove this phase's pages are clean, keep the global structure intact"
gate, so each phase gates on its own rewritten pages while P13 (no args) gates the whole
site.

Rules:
  1. docs.json parses and matches the tab shape (navigation.tabs -> groups -> pages).
  2. Every nav-referenced page has a matching .md/.mdx file.
  3. Every page file under docs/ (excluding superpowers/, snippets/, dot/underscore
     names) is referenced exactly once in the nav.
  4. Every root-absolute internal link (in scope pages) resolves to a nav page.
  5. Only guide/migration-attach-guard may contain the string "voicegateway.inference".
  6. No scope page carries VitePress-only frontmatter keys (layout / hero / features).
  7. Every scope page has non-empty title and description frontmatter.
  8. No scope page uses `{#custom-anchor}` heading ids (MDX/acorn cannot parse them;
     Mintlify auto-generates heading slugs). This is the concrete MDX-safety trap that
     breaks `mint broken-links`; broader "escape literal < and { in prose" guidance lives
     in the implementer briefs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent
MIGRATION_PAGE = "guide/migration-attach-guard"

# Link targets that are not nav pages (assets, images, mail, external).
_IMAGE_EXT = (".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico")
_FENCE_RE = re.compile(r"(^|\n)(```|~~~).*?(\n\2|\Z)", re.DOTALL)
_MD_LINK_RE = re.compile(r"\]\(([^)]+)\)")
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""")
# `## Heading {#anchor}` custom-id syntax: valid in some markdown flavors, but MDX parses
# the `{...}` as a JS expression and acorn fails ("Could not parse expression").
_HEADING_ANCHOR_RE = re.compile(r"^#{1,6}\s.*\{#[^}]+\}\s*$", re.MULTILINE)


def _iter_page_refs(nav_node) -> list[str]:
    """Flatten a navigation node into its page-string references.

    A `pages` entry is either a page string or a nested group dict (which itself has
    `pages`). Both shapes are walked so nested groups are supported.
    """
    refs: list[str] = []
    for tab in nav_node.get("tabs", []):
        refs.extend(_iter_group_pages(tab.get("groups", [])))
    # Also support a bare `groups` nav (pre-restructure shape) so the validator can
    # report shape errors rather than crashing.
    refs.extend(_iter_group_pages(nav_node.get("groups", [])))
    return refs


def _iter_group_pages(groups) -> list[str]:
    refs: list[str] = []
    for group in groups:
        for page in group.get("pages", []):
            if isinstance(page, str):
                refs.append(page)
            elif isinstance(page, dict):
                refs.extend(_iter_group_pages([page]))
    return refs


def _load_docs_json(errors: list[str]) -> tuple[dict, list[str]]:
    path = DOCS / "docs.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"[rule 1] docs.json does not parse: {exc}")
        return {}, []

    nav = data.get("navigation", {})
    tabs = nav.get("tabs")
    if not isinstance(tabs, list) or not tabs:
        errors.append("[rule 1] navigation.tabs must be a non-empty list")
        return data, _iter_page_refs(nav)

    for i, tab in enumerate(tabs):
        if not isinstance(tab.get("tab"), str) or not tab.get("tab"):
            errors.append(f"[rule 1] tab[{i}] missing string 'tab'")
        groups = tab.get("groups")
        if not isinstance(groups, list) or not groups:
            errors.append(f"[rule 1] tab '{tab.get('tab', i)}' has no groups list")
            continue
        for j, group in enumerate(groups):
            if not isinstance(group.get("group"), str) or not group.get("group"):
                errors.append(f"[rule 1] tab '{tab.get('tab')}' group[{j}] missing 'group'")
            pages = group.get("pages")
            if not isinstance(pages, list) or not pages:
                errors.append(
                    f"[rule 1] group '{group.get('group', j)}' has no pages list"
                )
    return data, _iter_page_refs(nav)


def _page_files() -> list[str]:
    """All docs page slugs on disk (excluding superpowers/, snippets/, dot/underscore)."""
    out: list[str] = []
    for path in DOCS.rglob("*"):
        if path.suffix not in (".md", ".mdx"):
            continue
        rel = path.relative_to(DOCS)
        parts = rel.parts
        if any(p.startswith((".", "_")) for p in parts):
            continue
        if parts[0] in ("superpowers", "snippets"):
            continue
        out.append(str(rel.with_suffix("")))
    return out


def _resolve_file(slug: str) -> bool:
    return (DOCS / f"{slug}.md").exists() or (DOCS / f"{slug}.mdx").exists()


def _strip_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_block, body). Empty frontmatter if none present."""
    if text.startswith("---\n") or text.startswith("---\r\n"):
        end = text.find("\n---", 4)
        if end != -1:
            fm = text[4:end]
            body = text[end + 4 :]
            return fm, body
    return "", text


def _frontmatter_value(fm: str, key: str) -> str | None:
    for line in fm.splitlines():
        m = re.match(rf"^{re.escape(key)}\s*:\s*(.*)$", line)
        if m:
            return m.group(1).strip().strip("'\"")
    return None


def _has_top_level_key(fm: str, key: str) -> bool:
    return any(re.match(rf"^{re.escape(key)}\s*:", line) for line in fm.splitlines())


def _internal_targets(body: str) -> list[str]:
    stripped = _FENCE_RE.sub("\n", body)
    targets = _MD_LINK_RE.findall(stripped) + _HREF_RE.findall(stripped)
    out: list[str] = []
    for t in targets:
        t = t.strip()
        if not t.startswith("/"):
            continue  # external, relative, anchor, mailto
        if t.startswith("//"):
            continue
        if t.startswith("/assets/"):
            continue
        base = t.split("#", 1)[0].split("?", 1)[0]
        if base.lower().endswith(_IMAGE_EXT):
            continue
        out.append(base)
    return out


def main(argv: list[str]) -> int:
    errors: list[str] = []
    _data, refs = _load_docs_json(errors)

    ref_set = set(refs)
    # Rule 2 (global): every ref has a file.
    for slug in refs:
        if not _resolve_file(slug):
            errors.append(f"[rule 2] nav references '{slug}' but no {slug}.md/.mdx exists")
    # Rule 3 (global): no duplicate refs.
    seen: set[str] = set()
    for slug in refs:
        if slug in seen:
            errors.append(f"[rule 3] page '{slug}' referenced more than once in nav")
        seen.add(slug)

    files = _page_files()
    file_set = set(files)
    # Rule 3 (global): every file referenced.
    for slug in files:
        if slug not in ref_set:
            errors.append(f"[rule 3] orphan page file '{slug}' not referenced in nav")

    # Content/link rules (4, 5, 6, 7) apply to the scope: args, or all files.
    scope = argv or sorted(file_set)
    unknown = [s for s in scope if s not in file_set]
    for s in unknown:
        errors.append(f"[scope] '{s}' is not a docs page file")
    for slug in sorted(s for s in scope if s in file_set):
        path = DOCS / f"{slug}.md"
        if not path.exists():
            path = DOCS / f"{slug}.mdx"
        text = path.read_text()
        fm, body = _strip_frontmatter(text)

        # Rule 7: title + description non-empty.
        for key in ("title", "description"):
            val = _frontmatter_value(fm, key)
            if not val:
                errors.append(f"[rule 7] '{slug}' missing non-empty {key}: frontmatter")

        # Rule 6: no VitePress keys.
        for key in ("layout", "hero", "features"):
            if _has_top_level_key(fm, key):
                errors.append(f"[rule 6] '{slug}' has VitePress frontmatter key '{key}:'")

        # Rule 5: voicegateway.inference only in the migration page.
        if slug != MIGRATION_PAGE and "voicegateway.inference" in text:
            errors.append(
                f"[rule 5] '{slug}' contains 'voicegateway.inference' "
                f"(allowed only in {MIGRATION_PAGE})"
            )

        # Rule 4: root-absolute internal links resolve to a nav page.
        for target in _internal_targets(body):
            slug_target = "index" if target == "/" else target.lstrip("/").rstrip("/")
            slug_target = re.sub(r"\.(md|mdx)$", "", slug_target)
            if slug_target not in ref_set:
                errors.append(
                    f"[rule 4] '{slug}' links to '{target}' which is not a nav page"
                )

        # Rule 8: no `{#custom-anchor}` heading ids (breaks MDX/acorn).
        for m in _HEADING_ANCHOR_RE.finditer(body):
            errors.append(
                f"[rule 8] '{slug}' uses a {{#anchor}} heading id "
                f"(MDX cannot parse it): {m.group(0).strip()!r}"
            )

    if errors:
        print(f"docs validator: FAIL ({len(errors)} issue(s))")
        for e in sorted(set(errors)):
            print("  " + e)
        return 1
    scope_note = "all" if not argv else f"{len(scope)} scoped"
    print(
        f"docs validator: PASS ({len(files)} pages, {len(refs)} nav refs, "
        f"content rules on {scope_note})"
    )
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
