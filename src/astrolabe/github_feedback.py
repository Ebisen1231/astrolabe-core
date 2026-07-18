"""private ledgerのGitHub Issueを学習イベントへ同期する(M1)。"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from astrolabe.ledger import derive, events, store

GITHUB_API = "https://api.github.com"
TITLE_PATTERN = re.compile(
    r"^\[fb\] (?P<action>selected|selected-later|marked_known|dismissed) "
    r"(?P<concept_id>\S{1,200})$"
)


@dataclass(frozen=True)
class FeedbackIssue:
    number: int
    action: str
    concept_id: str


@dataclass
class FeedbackSyncResult:
    imported: int = 0
    already_recorded: int = 0
    closed: int = 0
    close_failed: int = 0
    invalid: int = 0
    issues_to_close: list[int] = field(default_factory=list)


class GitHubFeedbackClient:
    """GitHub Issues REST APIの最小クライアント。全体でリトライ1回。"""

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
        last_error: Exception | None = None
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
                last_error = exc
                retryable = isinstance(exc, httpx.TransportError)
                if isinstance(exc, httpx.HTTPStatusError):
                    retryable = exc.response.status_code == 429 or exc.response.status_code >= 500
                if attempt == 0 and retryable:
                    self._sleeper(2.0)
                    continue
                raise
        raise RuntimeError(f"GitHub API呼び出し失敗: {last_error}")

    def list_open_feedback_issues(self) -> list[FeedbackIssue]:
        response = self._request(
            "GET",
            f"/repos/{self.repository}/issues",
            params={"state": "open", "per_page": 100, "sort": "created", "direction": "asc"},
        )
        parsed: list[FeedbackIssue] = []
        for issue in response.json():
            if "pull_request" in issue:
                continue
            match = TITLE_PATTERN.fullmatch(str(issue.get("title", "")))
            if match:
                parsed.append(
                    FeedbackIssue(
                        number=int(issue["number"]),
                        action=match.group("action"),
                        concept_id=match.group("concept_id"),
                    )
                )
        return parsed

    def close_issue(self, number: int) -> None:
        self._request(
            "PATCH",
            f"/repos/{self.repository}/issues/{number}",
            json={"state": "closed", "state_reason": "completed"},
        )


def _recorded_issue_numbers(conn, repository: str) -> set[int]:
    recorded: set[int] = set()
    for row in events.load_events(conn):
        payload = row.get("payload") or {}
        feedback = payload.get("feedback") or {}
        if feedback.get("repository") == repository and isinstance(
            feedback.get("issue_number"), int
        ):
            recorded.add(feedback["issue_number"])
    return recorded


def import_feedback_issues(
    conn,
    client: GitHubFeedbackClient,
    *,
    logger: logging.Logger | None = None,
) -> FeedbackSyncResult:
    """open Issueをeventsへ冪等に反映する。Issueはまだ閉じない。"""
    logger = logger or logging.getLogger("astrolabe.feedback")
    result = FeedbackSyncResult()
    recorded = _recorded_issue_numbers(conn, client.repository)
    try:
        issues = client.list_open_feedback_issues()
    except httpx.HTTPError as exc:
        logger.warning("GitHubフィードバック取得をスキップ: %s", exc)
        return result

    concept_names = {row["id"]: row["name"] for row in store.list_concepts(conn)}
    pending_events: list[dict] = []
    for issue in issues:
        if issue.number in recorded:
            result.already_recorded += 1
            result.issues_to_close.append(issue.number)
            continue
        concept_name = concept_names.get(issue.concept_id)
        if concept_name is None:
            result.invalid += 1
            logger.warning(
                "未知concept_idのフィードバックIssue #%dを保留: %s",
                issue.number,
                issue.concept_id,
            )
            continue

        event_type = "selected" if issue.action == "selected-later" else issue.action
        payload = {
            "name": concept_name,
            "feedback": {
                "repository": client.repository,
                "issue_number": issue.number,
                "action": issue.action,
            },
        }
        if issue.action == "selected-later":
            payload["later"] = True
        pending_events.append(
            {
                "ts": events.utcnow_iso(),
                "type": event_type,
                "concept_id": issue.concept_id,
                "payload": payload,
            }
        )
        recorded.add(issue.number)
        result.imported += 1
        result.issues_to_close.append(issue.number)

    if pending_events:
        events.append_events(conn, pending_events)
        derive.rebuild(conn)
    return result


def close_feedback_issues(
    client: GitHubFeedbackClient,
    issue_numbers: list[int],
    result: FeedbackSyncResult | None = None,
    *,
    logger: logging.Logger | None = None,
) -> FeedbackSyncResult:
    """反映済みIssueを閉じる。失敗は警告に留め、次runで再試行可能にする。"""
    logger = logger or logging.getLogger("astrolabe.feedback")
    result = result or FeedbackSyncResult(issues_to_close=list(issue_numbers))
    for number in sorted(set(issue_numbers)):
        try:
            client.close_issue(number)
            result.closed += 1
        except httpx.HTTPError as exc:
            result.close_failed += 1
            logger.warning("フィードバックIssue #%dのclose失敗。次runで再試行: %s", number, exc)
    return result


def sync_feedback(
    conn,
    client: GitHubFeedbackClient,
    *,
    logger: logging.Logger | None = None,
) -> FeedbackSyncResult:
    """ローカルmorning向け: import後にcloseまで行う。"""
    result = import_feedback_issues(conn, client, logger=logger)
    return close_feedback_issues(
        client,
        result.issues_to_close,
        result,
        logger=logger,
    )
