"""収集(arXiv / RSS)と重複排除。

収集アイテムは全ソース共通の dict:
{id, title, summary, url, published, source, categories}
"""

USER_AGENT = "astrolabe/0.1 (personal learning agent)"


class CollectError(Exception):
    """収集の失敗。呼び出し側は原則スキップして続行する(1ソース障害で朝ジョブを落とさない)。"""
