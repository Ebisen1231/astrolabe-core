"""朝の観測ジョブ: 収集 → 重複排除 → 一次選別(mini) → 統合報告(flagship) → 台帳更新。

台帳への書き込みは「proposed イベント追記 → concepts/edges 再導出 → daily_reports
アーカイブ」の順。concepts/edges はここでは直接書かない(導出のみ)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection

from astrolabe import render, render_html
from astrolabe.collect import arxiv, dedupe, rss
from astrolabe.config import Config
from astrolabe.ledger import derive, events, store
from astrolabe.llm import synthesize, triage
from astrolabe.llm.budget import TokenBudget


@dataclass
class MorningOutcome:
    report_text: str
    date: str
    meta: dict
    topics: list[dict]
    map_delta_text: str
    html_path: Path | None = None


def collect_items(
    config: Config,
    *,
    arxiv_max: int = 100,
    offline_dir: Path | None = None,
    logger: logging.Logger | None = None,
) -> list[dict]:
    """全ソースから収集する。offline_dir 指定時は fixtures のみ(ネットワーク不使用)。"""
    logger = logger or logging.getLogger("astrolabe.collect")
    if offline_dir is not None:
        items = arxiv.parse_arxiv_atom(
            (offline_dir / "arxiv_sample.xml").read_text(encoding="utf-8")
        )
        items += rss.parse_feed_text(
            (offline_dir / "rss_sample.xml").read_text(encoding="utf-8"), "fixtures-rss"
        )
        return items

    items = []
    client = arxiv.ArxivClient(config.cache_dir, config.arxiv_categories)
    try:
        try:
            items += client.fetch(max_results=arxiv_max)
        except Exception as e:  # 1ソースの障害で朝ジョブを落とさない
            logger.warning("arXiv収集をスキップ: %s", e)
    finally:
        client.close()
    items += rss.fetch_feeds(config.rss_feeds, logger=logger)
    return items


def run_morning(
    conn: Connection,
    llm,
    items: list[dict],
    *,
    today: str,
    budget: TokenBudget,
    top_k: int = 8,
    dry_run: bool = False,
    html_output_dir: Path | None = None,
    html_path_base: Path | None = None,
    feedback_repository: str = render_html.DEFAULT_LEDGER_REPOSITORY,
    logger: logging.Logger | None = None,
) -> MorningOutcome:
    logger = logger or logging.getLogger("astrolabe.morning")
    profile = store.get_profile(conn)
    learning_context = store.get_learning_context(conn)
    if not any(profile.get(k) for k in ("interests", "goals", "background")):
        logger.warning("プロファイル未登録。`astrolabe interview` で選別精度が上がる")

    batch = dedupe.dedupe_items(items)
    fresh = dedupe.filter_seen(batch, dedupe.seen_keys_from_ledger(conn))
    meta: dict = {
        "collected": len(items),
        "after_dedupe": len(batch),
        "fresh": len(fresh),
        "dry_run": dry_run,
        "personalization": {
            "learned": len(learning_context["learned_concepts"]),
            "recent_selected": len(learning_context["recent_selected"]),
            "recent_dismissed": len(learning_context["recent_dismissed"]),
        },
    }

    topics: list[dict] = []
    map_delta = ""
    if fresh:
        scores = triage.run_triage(
            llm,
            fresh,
            profile,
            learning_context=learning_context,
            logger=logger,
        )
        ranked = sorted(
            (i for i in fresh if i["id"] in scores),
            key=lambda i: scores[i["id"]]["score"],
            reverse=True,
        )
        top = ranked[:top_k]
        meta["triaged"] = len(scores)
        meta["top_k"] = len(top)
        if top:
            for item in top:
                item["score"] = scores[item["id"]]["score"]
                item["reason"] = scores[item["id"]]["reason"]
            result = synthesize.run_synthesis(llm, top, profile)
            topics = result["topics"]
            map_delta = result["map_delta_text"]
            _record_topics(conn, topics, top, today)
            n_concepts, n_edges = derive.rebuild(conn)
            meta["concepts"] = n_concepts
            meta["edges"] = n_edges

    meta["usage"] = budget.summary()
    html_path: Path | None = None
    stored_html_path: str | None = None
    if html_output_dir is not None:
        html_path = render_html.write_html_report(
            html_output_dir,
            today,
            topics,
            map_delta,
            store.list_concepts(conn),
            store.list_edges(conn),
            repository=feedback_repository,
        )
        stored_path = html_path
        if html_path_base is not None and html_path.is_relative_to(html_path_base):
            stored_path = html_path.relative_to(html_path_base)
        stored_html_path = str(stored_path)
        meta["html_path"] = stored_html_path
    store.save_daily_report(
        conn,
        today,
        {"topics": topics, "meta": meta},
        map_delta,
        stored_html_path,
    )
    return MorningOutcome(
        report_text=render.render_report(today, topics, map_delta, meta),
        date=today,
        meta=meta,
        topics=topics,
        map_delta_text=map_delta,
        html_path=html_path,
    )


def _record_topics(
    conn: Connection, topics: list[dict], top_items: list[dict], today: str
) -> None:
    """トピックを proposed イベントとして追記する(events が一次データ)。

    payload["dedupe_keys"] には、そのトピックが引用したアイテムのキーを入れ、
    翌日以降の既出判定に使う。引用されなかったアイテムは再浮上を許す。
    """
    url_to_keys = {
        dedupe.canonical_url(item.get("url", "")): sorted(dedupe.item_keys(item))
        for item in top_items
    }
    with conn:
        for t in topics:
            keys: list[str] = []
            for url in t.get("source_urls", []):
                keys += url_to_keys.get(dedupe.canonical_url(url), [])
            edges = [
                {
                    "dst": derive.concept_id_from_name(r["name"]),
                    "dst_name": r["name"],
                    "type": r["type"],
                    "weight": 1.0,
                }
                for r in t.get("related", [])
            ]
            events.append_event(
                conn,
                "proposed",
                concept_id=derive.concept_id_from_name(t["name"]),
                payload={
                    "name": t["name"],
                    "kind": t.get("kind", "concept"),
                    "summary": t.get("summary", ""),
                    "source_urls": t.get("source_urls", []),
                    "edges": edges,
                    "learn_content": t.get("learn_content", ""),
                    "why_now": t.get("why_now", ""),
                    "est_minutes": t.get("est_minutes"),
                    "report_date": today,
                    "dedupe_keys": sorted(set(keys)),
                },
            )
