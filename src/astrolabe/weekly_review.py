"""週次自己改修: 集計からprivate Issue発注書と学習タスクを作る。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

from astrolabe.ledger import events, store
from astrolabe.ledger.backend import LedgerBackend
from astrolabe.llm.budget import BudgetExceededError
from astrolabe.tutor.tools import TutorTools

JST = ZoneInfo("Asia/Tokyo")
GITHUB_API = "https://api.github.com"
SELF_IMPROVEMENT_LABEL = "self-improvement"
SELF_IMPROVEMENT_CONCEPT_ID = "astrolabe-self-improvement"
SELF_IMPROVEMENT_CONCEPT_NAME = "Astrolabe自己改修"
CHAT_FRAGMENT_LENGTH = 12
URL_PATTERN = re.compile(r"https?://[^\s)\]}>'\"]+")

WEEKLY_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "proposals": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "problem": {"type": "string"},
                    "requirements": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 5,
                        "items": {"type": "string"},
                    },
                    "acceptance_criteria": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 5,
                        "items": {"type": "string"},
                    },
                    "task_title": {"type": "string"},
                    "est_minutes": {"type": "integer", "minimum": 10, "maximum": 480},
                },
                "required": [
                    "title",
                    "problem",
                    "requirements",
                    "acceptance_criteria",
                    "task_title",
                    "est_minutes",
                ],
            },
        },
    },
    "required": ["summary", "proposals"],
}

WEEKLY_REVIEW_SYSTEM_PROMPT = """あなたはAstrolabeの週次プロダクト改善編集者です。
入力は学習台帳から機能面の傾向だけを集計・伏字化したものです。
改善提案を1〜3件、日本語の構造化出力で返してください。
提案には既知概念名を列挙または引用してはいけません。
URL、chat_note原文またはその断片、面談内容を引用してはいけません。
個別の学習内容ではなく、機能面の不満・傾向の要約だけを背景にしてください。
実装コードやHTMLは書かず、Codexへ渡す要求と検証可能な受け入れ条件を作ってください。"""


class WeeklyReviewError(RuntimeError):
    """週次分析または副作用の安全な完了に失敗した。"""


class PrivacyViolation(WeeklyReviewError):
    """提案に非公開の生データが残った。副作用前に停止する。"""


@dataclass(frozen=True)
class WeeklySignals:
    start_date: str
    end_date: str
    prompt_data: dict[str, Any]
    concept_names: tuple[str, ...]
    source_urls: tuple[str, ...]
    chat_fragments: tuple[str, ...]


@dataclass(frozen=True)
class ImprovementIssue:
    number: int
    url: str
    created: bool


@dataclass(frozen=True)
class WeeklyReviewResult:
    summary: str
    issue_urls: tuple[str, ...]
    created_issues: int
    created_tasks: int
    proposal_keys: tuple[str, ...]


class WeeklyBudgetGuard:
    """台帳上の日次flagship総量と今回runの実測usageを同時に守る。"""

    def __init__(
        self,
        ledger: LedgerBackend,
        usage_date: str,
        run_id: str,
        *,
        total_cap: int,
    ) -> None:
        if not run_id.startswith("weekly-"):
            raise ValueError("weekly run_idは'weekly-'で始める")
        self.ledger = ledger
        self.usage_date = usage_date
        self.run_id = run_id
        self.total_cap = total_cap
        self._run_used = store.get_llm_usage_for_run(
            ledger, usage_date, run_id, "flagship"
        )

    def attempt_precheck(self, budget_key: str, estimated_tokens: int) -> None:
        if budget_key != "flagship":
            return
        total = store.get_llm_usage_total(
            self.ledger, self.usage_date, "flagship"
        )
        estimate = max(0, estimated_tokens)
        if total + estimate > self.total_cap:
            raise BudgetExceededError(
                "週次実行のflagship予算超過見込み: "
                f"{total:,} + {estimate:,} > {self.total_cap:,}"
            )

    def record_usage(self, budget_key: str, tokens: int) -> None:
        if budget_key != "flagship":
            return
        self._run_used += max(0, tokens)
        store.save_llm_usage(
            self.ledger,
            self.usage_date,
            self.run_id,
            {"flagship": {"used": self._run_used}},
        )


class WeeklyLLM(Protocol):
    def structured(
        self,
        budget_key: str,
        schema_name: str,
        schema: dict,
        system: str,
        user: str,
        max_output_tokens: int,
    ) -> dict: ...


class ImprovementIssueClient(Protocol):
    def create_or_get_issue(self, key: str, title: str, body: str) -> ImprovementIssue: ...


def _event_date(timestamp: str) -> date:
    value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(JST).date()


def _payload(event: dict) -> dict:
    value = event.get("payload") or {}
    if isinstance(value, str):
        value = json.loads(value or "{}")
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _chat_fragments(note: str) -> set[str]:
    normalized = _normalize_text(note).casefold()
    if len(normalized) < CHAT_FRAGMENT_LENGTH:
        return {normalized} if normalized else set()
    return {
        normalized[index : index + CHAT_FRAGMENT_LENGTH]
        for index in range(len(normalized) - CHAT_FRAGMENT_LENGTH + 1)
    }


def _sanitize_note(note: str, concept_names: list[str]) -> str:
    value = URL_PATTERN.sub("<url>", _normalize_text(note))
    for name in sorted(concept_names, key=len, reverse=True):
        if name:
            value = re.sub(re.escape(name), "<concept>", value, flags=re.IGNORECASE)
    return value[:500]


def collect_weekly_signals(conn: LedgerBackend, today: date | str) -> WeeklySignals:
    end = today if isinstance(today, date) else date.fromisoformat(today)
    start = end - timedelta(days=6)
    all_events = events.load_events(conn)
    window = [row for row in all_events if start <= _event_date(str(row["ts"])) <= end]
    concepts = store.list_concepts(conn)
    concept_names = sorted(
        {str(row.get("name") or "").strip() for row in concepts if row.get("name")}
    )
    source_urls = {
        match.group(0)
        for row in all_events
        for text in _strings(_payload(row))
        for match in URL_PATTERN.finditer(text)
    }
    source_urls.update(
        str(url)
        for concept in concepts
        for url in concept.get("source_urls", [])
        if str(url).startswith(("http://", "https://"))
    )
    dismissed_by_day = {day.isoformat(): 0 for day in _date_range(start, end)}
    chat_notes: list[str] = []
    original_notes: list[str] = []
    event_counts: dict[str, int] = {}
    for row in window:
        event_type = str(row["type"])
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        if event_type == "dismissed":
            dismissed_by_day[_event_date(str(row["ts"])).isoformat()] += 1
        if event_type == "chat_note":
            note = str(_payload(row).get("note") or "").strip()
            if note:
                original_notes.append(note)
                chat_notes.append(_sanitize_note(note, concept_names))

    tasks = store.list_tasks(conn)
    task_summary: dict[str, dict[str, int]] = {}
    for task in tasks:
        kind = str(task.get("kind") or "unknown")
        status = str(task.get("status") or "unknown")
        task_summary.setdefault(kind, {"open": 0, "done": 0})
        task_summary[kind][status] = task_summary[kind].get(status, 0) + 1

    usage = []
    for day in _date_range(start, end):
        iso = day.isoformat()
        usage.append(
            {
                "date": iso,
                "mini": store.get_llm_usage_total(conn, iso, "mini"),
                "flagship": store.get_llm_usage_total(conn, iso, "flagship"),
            }
        )
    reports = []
    for report in store.list_daily_reports(conn):
        report_date = date.fromisoformat(str(report["date"]))
        if not start <= report_date <= end:
            continue
        meta = report.get("items", {}).get("meta", {})
        reports.append(
            {
                "date": report_date.isoformat(),
                "collected": int(meta.get("collected", 0)),
                "fresh": int(meta.get("fresh", 0)),
                "top_k": int(meta.get("top_k", 0)),
                "reviews_due": int(meta.get("reviews_due", 0)),
            }
        )
    fragments = set()
    for note in original_notes:
        fragments.update(_chat_fragments(note))
    prompt_data = {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "event_counts": dict(sorted(event_counts.items())),
        "dismissed_by_day": dismissed_by_day,
        "sanitized_feature_notes": chat_notes,
        "tasks_by_kind_and_status": dict(sorted(task_summary.items())),
        "llm_usage": usage,
        "report_metrics": reports,
    }
    return WeeklySignals(
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        prompt_data=prompt_data,
        concept_names=tuple(concept_names),
        source_urls=tuple(sorted(source_urls)),
        chat_fragments=tuple(sorted(fragments)),
    )


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def validate_proposal_privacy(result: dict, signals: WeeklySignals) -> None:
    serialized = _normalize_text(json.dumps(result, ensure_ascii=False, sort_keys=True)).casefold()
    for name in signals.concept_names:
        candidate = name.casefold()
        if candidate.isascii() and candidate.replace("-", "").isalnum():
            found = re.search(rf"(?<!\w){re.escape(candidate)}(?!\w)", serialized)
        else:
            found = candidate in serialized
        if found:
            raise PrivacyViolation("週次提案に既知概念名が含まれる")
    known_url_found = any(
        url.casefold() in serialized for url in signals.source_urls
    )
    if URL_PATTERN.search(serialized) or known_url_found:
        raise PrivacyViolation("週次提案にURLが含まれる")
    if any(fragment and fragment in serialized for fragment in signals.chat_fragments):
        raise PrivacyViolation("週次提案にchat_note原文断片が含まれる")


def analyze_week(llm: WeeklyLLM, signals: WeeklySignals) -> dict:
    result = llm.structured(
        "flagship",
        "weekly_self_improvement",
        WEEKLY_REVIEW_SCHEMA,
        WEEKLY_REVIEW_SYSTEM_PROMPT,
        json.dumps(signals.prompt_data, ensure_ascii=False, sort_keys=True),
        3_000,
    )
    proposals = result.get("proposals")
    if not isinstance(proposals, list) or not 1 <= len(proposals) <= 3:
        raise WeeklyReviewError("週次提案は1..3件必要")
    validate_proposal_privacy(result, signals)
    return result


def proposal_key(start_date: str, title: str) -> str:
    source = f"{start_date}\n{_normalize_text(title).casefold()}".encode()
    return hashlib.sha256(source).hexdigest()[:16]


def render_issue_body(proposal: dict, *, key: str, start_date: str, end_date: str) -> str:
    requirements = "\n".join(f"- {item}" for item in proposal["requirements"])
    acceptance = "\n".join(f"- {item}" for item in proposal["acceptance_criteria"])
    return (
        f"<!-- astrolabe-weekly-review:v1 key={key} -->\n"
        "# Codexへの発注書ドラフト\n\n"
        f"対象期間: {start_date}〜{end_date}\n\n"
        f"## 背景\n\n{proposal['problem']}\n\n"
        f"## 要求\n\n{requirements}\n\n"
        f"## 受け入れ条件\n\n{acceptance}\n\n"
        "このIssue本文を施主が確認し、必要なら編集してからCodexへ手動で発注する。\n"
    )


def _recorded_proposal_keys(conn: LedgerBackend) -> set[str]:
    keys = set()
    for row in events.load_events(conn):
        if row["type"] != "task_created":
            continue
        metadata = _payload(row).get("metadata") or {}
        key = metadata.get("proposal_key")
        if isinstance(key, str):
            keys.add(key)
    return keys


def run_weekly_review(
    conn: LedgerBackend,
    llm: WeeklyLLM,
    issue_client: ImprovementIssueClient,
    notifier: Callable[[str, list[dict]], bool],
    *,
    today: date | str,
    now: Callable[[], datetime] | None = None,
) -> WeeklyReviewResult:
    """privacy検証完了後にだけIssue→task→Discordの順で副作用を実行する。"""
    signals = collect_weekly_signals(conn, today)
    analysis = analyze_week(llm, signals)
    recorded = _recorded_proposal_keys(conn)
    issue_rows: list[dict] = []
    keys: list[str] = []
    created_issues = 0
    created_tasks = 0
    tools = TutorTools(conn, now=now)
    for proposal in analysis["proposals"]:
        key = proposal_key(signals.start_date, str(proposal["title"]))
        keys.append(key)
        body = render_issue_body(
            proposal,
            key=key,
            start_date=signals.start_date,
            end_date=signals.end_date,
        )
        issue = issue_client.create_or_get_issue(
            key,
            f"[self-improvement] {proposal['title']}",
            body,
        )
        created_issues += int(issue.created)
        if key not in recorded:
            tools.create_task(
                SELF_IMPROVEMENT_CONCEPT_ID,
                SELF_IMPROVEMENT_CONCEPT_NAME,
                str(proposal["task_title"]),
                "build_app_feature",
                int(proposal["est_minutes"]),
                [],
                metadata={
                    "proposal_key": key,
                    "issue_number": issue.number,
                    "issue_url": issue.url,
                    "week_start": signals.start_date,
                },
            )
            recorded.add(key)
            created_tasks += 1
        issue_rows.append({"title": str(proposal["title"]), "url": issue.url})
    if not notifier(str(analysis["summary"]), issue_rows):
        raise WeeklyReviewError("週次Discord通知に失敗")
    return WeeklyReviewResult(
        summary=str(analysis["summary"]),
        issue_urls=tuple(row["url"] for row in issue_rows),
        created_issues=created_issues,
        created_tasks=created_tasks,
        proposal_keys=tuple(keys),
    )


class GitHubImprovementClient:
    """private ledger Issue専用の最小GitHub REST client。リトライは全体1回。"""

    def __init__(
        self,
        token: str,
        repository: str,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.repository = repository
        self._sleeper = sleeper
        self._client = httpx.Client(
            base_url=GITHUB_API,
            timeout=timeout,
            transport=transport,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "astrolabe-core/0.1",
            },
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        for attempt in range(2):
            try:
                response = self._client.request(method, path, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"一時的なGitHub APIエラー: {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                retryable = isinstance(exc, httpx.TransportError) or (
                    isinstance(exc, httpx.HTTPStatusError)
                    and (exc.response.status_code == 429 or exc.response.status_code >= 500)
                )
                if attempt == 0 and retryable:
                    self._sleeper(2.0)
                    continue
                raise
        raise AssertionError("unreachable")

    def _ensure_label(self) -> None:
        path = f"/repos/{self.repository}/labels/{SELF_IMPROVEMENT_LABEL}"
        try:
            self._request("GET", path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            self._request(
                "POST",
                f"/repos/{self.repository}/labels",
                json={
                    "name": SELF_IMPROVEMENT_LABEL,
                    "color": "B08A2E",
                    "description": "Astrolabe weekly improvement drafts",
                },
            )

    def create_or_get_issue(self, key: str, title: str, body: str) -> ImprovementIssue:
        marker = f"astrolabe-weekly-review:v1 key={key}"
        response = self._request(
            "GET",
            f"/repos/{self.repository}/issues",
            params={"state": "all", "labels": SELF_IMPROVEMENT_LABEL, "per_page": 100},
        )
        for issue in response.json():
            if "pull_request" not in issue and marker in str(issue.get("body") or ""):
                return ImprovementIssue(
                    number=int(issue["number"]), url=str(issue["html_url"]), created=False
                )
        self._ensure_label()
        created = self._request(
            "POST",
            f"/repos/{self.repository}/issues",
            json={"title": title, "body": body, "labels": [SELF_IMPROVEMENT_LABEL]},
        ).json()
        return ImprovementIssue(
            number=int(created["number"]), url=str(created["html_url"]), created=True
        )
