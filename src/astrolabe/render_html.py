"""単一ファイルHTML報告の決定的生成(M1)。

LLM出力はテキスト/JSONとして受け取り、本モジュールがHTMLへ整形する。
Cytoscape.js本体だけをCDNから読み、報告データ・CSS・初期化コードはHTMLへ埋め込む。
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from string import Template
from urllib.parse import urlencode

from astrolabe.ledger.derive import concept_id_from_name

CYTOSCAPE_CDN = "https://cdn.jsdelivr.net/npm/cytoscape@3.34.0/dist/cytoscape.min.js"
DEFAULT_LEDGER_REPOSITORY = "Ebisen1231/astrolabe-ledger"

COLORS = {
    "background": "#0B1226",
    "divider": "#1D2A4A",
    "text_bright": "#E8E3D8",
    "text_muted": "#A8B4CE",
    "today_star": "#F2E8D5",
    "gold": "#D8A03D",
    "learned": "#E8B84B",
    "unknown": "#7C8FB8",
    "unknown_label": "#6B7EA3",
    "prerequisite": "#5C74AB",
    "related": "#3A4E7E",
}

FEEDBACK_ACTIONS = (
    ("selected", "学ぶ"),
    ("selected-later", "気になる"),
    ("marked_known", "もう知っている"),
    ("dismissed", "興味がない"),
)


def _embedded_json(value: object) -> str:
    """script要素を閉じられない安全なJSON文字列を返す。"""
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _safe_source_url(url: str) -> str | None:
    candidate = str(url or "").strip()
    if candidate.startswith(("https://", "http://")):
        return candidate
    return None


def feedback_issue_url(
    repository: str,
    action: str,
    concept_id: str,
    concept_name: str,
    report_date: str,
) -> str:
    """GitHubのprefilled Issue作成URLを返す。"""
    title = f"[fb] {action} {concept_id}"
    body = "\n".join(
        (
            "[astrolabe-feedback-v1]",
            f"concept: {concept_name}",
            f"report_date: {report_date}",
            "このIssueは次回の朝ジョブで学習台帳へ取り込まれます。",
        )
    )
    return f"https://github.com/{repository}/issues/new?{urlencode({'title': title, 'body': body})}"


def _topic_html(topic: dict, report_date: str, repository: str) -> str:
    name = str(topic.get("name", ""))
    concept_id = concept_id_from_name(name)
    sources = []
    for raw_url in topic.get("source_urls", []):
        url = _safe_source_url(str(raw_url))
        if url:
            escaped = html.escape(url, quote=True)
            sources.append(f'<li><a href="{escaped}" rel="noreferrer">{escaped}</a></li>')
    source_block = ""
    if sources:
        source_block = '<div class="sources"><h3>出典</h3><ul>' + "".join(sources) + "</ul></div>"

    feedback = []
    for action, label in FEEDBACK_ACTIONS:
        url = feedback_issue_url(repository, action, concept_id, name, report_date)
        feedback.append(
            f'<a class="feedback feedback-{html.escape(action)}" '
            f'href="{html.escape(url, quote=True)}" rel="noreferrer">{html.escape(label)}</a>'
        )

    return "".join(
        (
            '<article class="topic-card">',
            f'<div class="topic-kind">{html.escape(str(topic.get("kind", "concept")))}</div>',
            f"<h2>{html.escape(name)}</h2>",
            f'<p class="summary">{html.escape(str(topic.get("summary", "")))}</p>',
            '<div class="why"><h3>なぜ今か</h3>',
            f'<p>{html.escape(str(topic.get("why_now", "")))}</p></div>',
            '<div class="learn"><h3>学習コンテンツ</h3>',
            f'<div class="learn-content">{html.escape(str(topic.get("learn_content", "")))}</div>',
            f'<p class="minutes">目安 {html.escape(str(topic.get("est_minutes", 10)))}分</p></div>',
            (
                '<div class="practice-task"><h3>実践課題</h3>'
                f'<p>{html.escape(str((topic.get("practice_task") or {}).get("title", "")))}</p>'
                "</div>"
                if (topic.get("practice_task") or {}).get("title")
                else ""
            ),
            source_block,
            '<nav class="feedbacks" aria-label="このトピックへのフィードバック">',
            "".join(feedback),
            "</nav></article>",
        )
    )


def _cytoscape_elements(
    concepts: list[dict], edges: list[dict], topics: list[dict]
) -> list[dict]:
    today_ids = {concept_id_from_name(str(topic.get("name", ""))) for topic in topics}
    concept_by_id = {str(c["id"]): dict(c) for c in concepts}
    for topic in topics:
        cid = concept_id_from_name(str(topic.get("name", "")))
        concept_by_id.setdefault(
            cid,
            {
                "id": cid,
                "name": str(topic.get("name", cid)),
                "status": "unknown",
                "kind": str(topic.get("kind", "concept")),
            },
        )

    elements: list[dict] = []
    for cid in sorted(concept_by_id):
        concept = concept_by_id[cid]
        status = str(concept.get("status", "unknown"))
        classes = ["today"] if cid in today_ids else []
        classes.append("learned" if status == "learned" else "unknown")
        elements.append(
            {
                "data": {
                    "id": cid,
                    "label": str(concept.get("name", cid)),
                    "status": status,
                    "kind": str(concept.get("kind", "concept")),
                    "today": cid in today_ids,
                },
                "classes": " ".join(classes),
            }
        )

    known_ids = set(concept_by_id)
    for index, edge in enumerate(
        sorted(edges, key=lambda e: (str(e.get("src")), str(e.get("dst")), str(e.get("type"))))
    ):
        src, dst = str(edge.get("src", "")), str(edge.get("dst", ""))
        if not src or not dst or src not in known_ids or dst not in known_ids:
            continue
        edge_type = str(edge.get("type", "related"))
        elements.append(
            {
                "data": {
                    "id": f"edge-{index}-{src}-{dst}-{edge_type}",
                    "source": src,
                    "target": dst,
                    "type": edge_type,
                    "weight": float(edge.get("weight", 1.0)),
                }
            }
        )
    return elements


HTML_TEMPLATE = Template(
    """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Astrolabe 朝の観測報告 — $report_date</title>
  <script src="$cytoscape_cdn"></script>
  <style>
    :root {
      --background: #0B1226; --divider: #1D2A4A; --text-bright: #E8E3D8;
      --text-muted: #A8B4CE; --gold: #D8A03D; --learned: #E8B84B;
      --unknown: #7C8FB8; --unknown-label: #6B7EA3;
      color-scheme: dark;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--background); color: var(--text-bright);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
      line-height: 1.7; }
    main { width: min(1120px, calc(100% - 32px)); margin: 0 auto; padding: 48px 0 80px; }
    header { border-bottom: 1px solid var(--divider); padding-bottom: 28px; margin-bottom: 28px; }
    .eyebrow, .change-label, .topic-kind { color: var(--gold); font-size: 12px;
      font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }
    h1 { margin: 4px 0 0; font-size: clamp(28px, 5vw, 48px); line-height: 1.15; }
    .date { color: var(--text-muted); margin-top: 8px; }
    .today-change { border: 1px solid var(--divider); border-left: 3px solid var(--gold);
      border-radius: 8px; padding: 18px 20px; margin: 28px 0; }
    .today-change p { margin: 3px 0 0; font-size: 18px; }
    .map-shell { border: 1px solid var(--divider); border-radius: 12px; overflow: hidden;
      background: #0B1226; margin-bottom: 40px; }
    .map-heading { padding: 18px 20px 0; margin: 0; font-size: 18px; }
    #learning-map { width: 100%; height: min(62vh, 640px); min-height: 420px; }
    .legend { display: flex; flex-wrap: wrap; gap: 10px 20px; padding: 14px 20px 18px;
      border-top: 1px solid var(--divider); color: var(--text-muted); font-size: 12px; }
    .legend span { display: inline-flex; align-items: center; gap: 7px; }
    .dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
    .dot-today { background: #F2E8D5; box-shadow: 0 0 0 4px #0B1226, 0 0 0 5px #D8A03D; }
    .dot-related { width: 5px; height: 5px; background: #7C8FB8; }
    .dot-learned { background: #E8B84B; box-shadow: 0 0 7px #E8B84B; }
    .line { width: 24px; border-top: 1.2px solid #5C74AB; display: inline-block; }
    .line-related { border-color: #3A4E7E; border-top-style: dashed; }
    .topics-heading { font-size: 22px; margin: 0 0 16px; }
    .topic-grid { display: grid; gap: 18px; }
    .topic-card { border: 1px solid var(--divider); border-radius: 12px; padding: 24px;
      background: rgba(29, 42, 74, .16); }
    .topic-card h2 { margin: 2px 0 12px; font-size: 23px; }
    .topic-card h3 { margin: 18px 0 4px; font-size: 14px; color: var(--text-bright); }
    .summary, .topic-card p { color: var(--text-muted); }
    .learn-content { white-space: pre-wrap; color: var(--text-bright); }
    .minutes { font-size: 12px; }
    .sources ul { margin: 4px 0; padding-left: 20px; }
    a { color: #AFC6FF; overflow-wrap: anywhere; }
    .feedbacks { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 22px;
      padding-top: 18px; border-top: 1px solid var(--divider); }
    .feedback { border: 1px solid var(--divider); border-radius: 999px; padding: 7px 12px;
      color: var(--text-bright); text-decoration: none; font-size: 13px; }
    .feedback:hover, .feedback:focus-visible { border-color: var(--gold); color: var(--gold); }
    .empty { color: var(--text-muted); border: 1px dashed var(--divider); padding: 24px;
      border-radius: 12px; }
    .reviews { border: 1px solid var(--divider); border-left: 3px solid var(--gold);
      border-radius: 12px; padding: 20px 24px; margin-top: 28px; }
    .reviews h2 { margin: 0 0 8px; font-size: 20px; }
    .reviews ul { margin: 0; padding-left: 20px; color: var(--text-muted); }
    footer { color: var(--text-muted); font-size: 12px; margin-top: 40px;
      border-top: 1px solid var(--divider); padding-top: 16px; }
    @media (max-width: 640px) { main { width: min(100% - 20px, 1120px); padding-top: 28px; }
      .topic-card { padding: 18px; } #learning-map { min-height: 360px; } }
  </style>
</head>
<body>
  <main>
    <header><div class="eyebrow">Astrolabe</div><h1>朝の観測報告</h1>
      <div class="date">$report_date</div></header>
    <section class="today-change" aria-labelledby="change-heading">
      <div class="change-label" id="change-heading">今日の変化</div><p>$map_delta_text</p>
    </section>
    <section class="map-shell" aria-labelledby="map-heading">
      <h2 class="map-heading" id="map-heading">学習マップ</h2><div id="learning-map"></div>
      <div class="legend" aria-label="学習マップの凡例">
        <span><i class="dot dot-today"></i>今日の提案(金の環)</span>
        <span><i class="dot dot-related"></i>関連概念(未学習)</span>
        <span><i class="dot dot-learned"></i>習得済み</span>
        <span><i class="line"></i>前提(実線)</span>
        <span><i class="line line-related"></i>関連(破線)</span>
      </div>
    </section>
    <section aria-labelledby="topics-heading">
      <h2 class="topics-heading" id="topics-heading">今日の提案</h2>
      <div class="topic-grid">$topics_html</div>
    </section>
    $reviews_section
    <footer>生成は決定的コードで行い、LLMにはHTMLを作らせていません。</footer>
  </main>
  <script type="application/json" id="astrolabe-elements">$elements_json</script>
  <script>
    (() => {
      const elements = JSON.parse(document.getElementById('astrolabe-elements').textContent);
      const todayStar = 'data:image/svg+xml;utf8,' + encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20">' +
        '<circle cx="10" cy="10" r="9" fill="none" stroke="#D8A03D" stroke-width="1"/>' +
        '<circle cx="10" cy="10" r="4.5" fill="#F2E8D5"/></svg>'
      );
      cytoscape({ container: document.getElementById('learning-map'), elements,
        layout: { name: 'concentric', animate: false, minNodeSpacing: 34,
          concentric: node => node.data('today') ? 3 : (node.data('status') === 'learned' ? 2 : 1),
          levelWidth: () => 1 },
        style: [
          { selector: 'node', style: { 'label': 'data(label)', 'font-size': 11,
            'text-wrap': 'wrap', 'text-max-width': 120, 'text-valign': 'bottom',
            'text-margin-y': 7, 'background-color': '#7C8FB8', 'width': 5, 'height': 5,
            'color': '#6B7EA3', 'text-outline-color': '#0B1226', 'text-outline-width': 2 } },
          { selector: 'node.learned', style: { 'background-color': '#E8B84B', 'width': 8,
            'height': 8, 'color': '#E8E3D8', 'shadow-blur': 8, 'shadow-color': '#E8B84B',
            'shadow-opacity': .75 } },
          { selector: 'node.today', style: { 'width': 20, 'height': 20,
            'background-opacity': 0, 'background-image': todayStar, 'background-fit': 'contain',
            'background-clip': 'none', 'color': '#E8E3D8', 'font-weight': 700 } },
          { selector: 'edge', style: { 'curve-style': 'bezier', 'width': 1.2,
            'line-color': '#3A4E7E', 'opacity': .9 } },
          { selector: 'edge[type = "prerequisite"]', style: { 'line-style': 'solid',
            'line-color': '#5C74AB', 'width': 1.2 } },
          { selector: 'edge[type = "related"]', style: { 'line-style': 'dashed',
            'line-dash-pattern': [2, 4], 'line-color': '#3A4E7E' } }
        ], wheelSensitivity: .18
      });
    })();
  </script>
</body>
</html>
"""
)


def render_html_report(
    report_date: str,
    topics: list[dict],
    map_delta_text: str,
    concepts: list[dict],
    edges: list[dict],
    *,
    reviews: list[dict] | None = None,
    repository: str = DEFAULT_LEDGER_REPOSITORY,
) -> str:
    """報告データから単一HTML文字列を返す。"""
    topics_html = "".join(_topic_html(t, report_date, repository) for t in topics)
    if not topics_html:
        topics_html = '<p class="empty">本日の新規トピックはありません。</p>'
    reviews_section = ""
    if reviews:
        rows = "".join(
            "<li>"
            + html.escape(str(row.get("concept_name", row.get("concept_id", ""))))
            + " — 期日 "
            + html.escape(str(row.get("due_date", "")))
            + " / 間隔 "
            + html.escape(str(row.get("interval_days", "?")))
            + "日</li>"
            for row in reviews
        )
        reviews_section = (
            '<section class="reviews" aria-labelledby="reviews-heading">'
            '<h2 id="reviews-heading">今日の復習</h2><ul>' + rows + "</ul></section>"
        )
    return HTML_TEMPLATE.substitute(
        report_date=html.escape(report_date),
        cytoscape_cdn=html.escape(CYTOSCAPE_CDN, quote=True),
        map_delta_text=html.escape(map_delta_text or "変化はまだありません。"),
        topics_html=topics_html,
        reviews_section=reviews_section,
        elements_json=_embedded_json(_cytoscape_elements(concepts, edges, topics)),
    )


def write_html_report(
    output_dir: Path,
    report_date: str,
    topics: list[dict],
    map_delta_text: str,
    concepts: list[dict],
    edges: list[dict],
    *,
    reviews: list[dict] | None = None,
    repository: str = DEFAULT_LEDGER_REPOSITORY,
) -> Path:
    """`output_dir/YYYY-MM-DD.html`へ原子的に保存してパスを返す。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{report_date}.html"
    temporary = destination.with_suffix(".html.tmp")
    temporary.write_text(
        render_html_report(
            report_date,
            topics,
            map_delta_text,
            concepts,
            edges,
            reviews=reviews,
            repository=repository,
        ),
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination
