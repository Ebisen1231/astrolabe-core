"""日次報告のターミナル整形。LLMは使わない(描画は決定的なコードが行う)。"""

from __future__ import annotations

import textwrap

_WIDTH = 72


def _wrap(text: str, indent: str = "  ") -> str:
    return textwrap.fill(
        text, width=_WIDTH, initial_indent=indent, subsequent_indent=indent
    )


def _usage_line(usage: dict) -> str:
    parts = []
    for key in sorted(usage):
        used, cap = usage[key]["used"], usage[key]["cap"]
        pct = 100.0 * used / cap if cap else 0.0
        parts.append(f"{key} {used:,} / {cap:,} ({pct:.1f}%)")
    return " | ".join(parts)


def render_report(
    date: str,
    topics: list[dict],
    map_delta_text: str,
    meta: dict,
    *,
    reviews: list[dict] | None = None,
) -> str:
    lines: list[str] = []
    tag = "  [dry-run]" if meta.get("dry_run") else ""
    lines.append("=" * _WIDTH)
    lines.append(f" ASTROLABE 朝の観測報告  {date}{tag}")
    lines.append("=" * _WIDTH)
    flow = (
        f"収集 {meta.get('collected', 0)}件"
        f" → 重複除去後 {meta.get('after_dedupe', 0)}件"
        f" → 新規 {meta.get('fresh', 0)}件"
    )
    if "top_k" in meta:
        flow += f" → 統合対象 {meta['top_k']}件"
    lines.append(flow)
    lines.append("")

    if not topics:
        lines.append("本日の新規トピックはない。")
        lines.append("")

    for n, t in enumerate(topics, 1):
        lines.append(f"【{n}】{t.get('name', '')}")
        lines.append(f"  種別: {t.get('kind', 'concept')} / 目安 {t.get('est_minutes', '?')}分")
        if t.get("why_now"):
            lines.append(_wrap("なぜ今: " + t["why_now"]))
        if t.get("summary"):
            lines.append(_wrap(t["summary"]))
        if t.get("learn_content"):
            lines.append("  -- 学習コンテンツ --")
            for raw in t["learn_content"].splitlines():
                lines.append(_wrap(raw.strip()) if raw.strip() else "")
        practice_task = t.get("practice_task") or {}
        if practice_task.get("title"):
            lines.append("  -- 実践課題 --")
            lines.append(
                _wrap(
                    f"{practice_task['title']} "
                    f"({practice_task.get('kind', 'read')} / "
                    f"{practice_task.get('est_minutes', '?')}分)"
                )
            )
        related = ", ".join(
            f"{r.get('name')}({r.get('type')})" for r in t.get("related", [])
        )
        if related:
            lines.append(_wrap("関連: " + related))
        for url in t.get("source_urls", []):
            lines.append(f"  出典: {url}")
        lines.append("")

    if reviews:
        lines.append("【今日の復習】")
        for row in reviews:
            lines.append(
                _wrap(
                    f"- {row.get('concept_name', row.get('concept_id', ''))} "
                    f"(期日 {row.get('due_date', '?')} / 間隔 {row.get('interval_days', '?')}日)",
                    indent="  ",
                )
            )
        lines.append("")

    lines.append("-" * _WIDTH)
    if map_delta_text:
        lines.append(_wrap("マップ差分: " + map_delta_text, indent=""))
    usage = meta.get("usage") or {}
    if usage:
        lines.append(_wrap("トークン使用: " + _usage_line(usage), indent=""))
    return "\n".join(lines)
