"""M2学習マップ用の決定的な座標配置。

既存layoutの座標は一切動かさず、新規ノードだけを安定した順序・角度で追加する。
"""

from __future__ import annotations

import hashlib
import math

SCHEMA_VERSION = 1
MIN_DISTANCE = 88.0
RING_STEP = 92.0
GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))


class LayoutError(ValueError):
    """既存layoutが安全に引き継げない。"""


def _stable_angle(node_id: str) -> float:
    digest = hashlib.sha256(node_id.encode("utf-8")).digest()
    fraction = int.from_bytes(digest[:8], "big") / float(2**64)
    return fraction * math.tau


def _read_existing(layout: dict | None, node_ids: set[str]) -> dict[str, dict[str, float]]:
    if layout is None:
        return {}
    if layout.get("schema_version") != SCHEMA_VERSION:
        raise LayoutError(
            f"layout.jsonのschema_versionが未対応: {layout.get('schema_version')!r}"
        )
    raw_positions = layout.get("positions")
    if not isinstance(raw_positions, dict):
        raise LayoutError("layout.jsonのpositionsがオブジェクトではない")

    positions: dict[str, dict[str, float]] = {}
    for node_id in sorted(node_ids):
        raw = raw_positions.get(node_id)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise LayoutError(f"layout.jsonの座標が不正: {node_id}")
        x, y = raw.get("x"), raw.get("y")
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, int | float)
            or not isinstance(y, int | float)
            or not math.isfinite(float(x))
            or not math.isfinite(float(y))
        ):
            raise LayoutError(f"layout.jsonの座標が不正: {node_id}")
        positions[node_id] = {"x": float(x), "y": float(y)}
    return positions


def _neighbor_map(node_ids: set[str], edges: list[dict]) -> dict[str, set[str]]:
    neighbors = {node_id: set() for node_id in node_ids}
    for edge in edges:
        src, dst = str(edge.get("src", "")), str(edge.get("dst", ""))
        if src in node_ids and dst in node_ids and src != dst:
            neighbors[src].add(dst)
            neighbors[dst].add(src)
    return neighbors


def _anchor(
    node_id: str,
    neighbors: dict[str, set[str]],
    positions: dict[str, dict[str, float]],
) -> tuple[float, float]:
    placed = [positions[n] for n in sorted(neighbors[node_id]) if n in positions]
    if not placed:
        return 0.0, 0.0
    return (
        sum(p["x"] for p in placed) / len(placed),
        sum(p["y"] for p in placed) / len(placed),
    )


def _is_free(x: float, y: float, positions: dict[str, dict[str, float]]) -> bool:
    return all(
        math.hypot(x - position["x"], y - position["y"]) >= MIN_DISTANCE
        for position in positions.values()
    )


def build_layout(
    node_ids: list[str], edges: list[dict], existing_layout: dict | None = None
) -> dict:
    """同一入力なら同一座標を返し、既存ノードの座標は追加時にも維持する。"""
    ordered_ids = sorted(set(node_ids))
    node_id_set = set(ordered_ids)
    positions = _read_existing(existing_layout, node_id_set)
    neighbors = _neighbor_map(node_id_set, edges)

    for node_id in ordered_ids:
        if node_id in positions:
            continue
        if not positions:
            positions[node_id] = {"x": 0.0, "y": 0.0}
            continue

        anchor_x, anchor_y = _anchor(node_id, neighbors, positions)
        base_angle = _stable_angle(node_id)
        for attempt in range(10_000):
            ring = 1 + attempt // 16
            angle = base_angle + attempt * GOLDEN_ANGLE
            x = round(anchor_x + ring * RING_STEP * math.cos(angle), 3)
            y = round(anchor_y + ring * RING_STEP * math.sin(angle), 3)
            if _is_free(x, y, positions):
                positions[node_id] = {"x": x, "y": y}
                break
        else:  # pragma: no cover - 現実的なノード数では到達しない安全弁
            raise LayoutError(f"新規ノードの空き座標を決定できない: {node_id}")

    return {
        "schema_version": SCHEMA_VERSION,
        "positions": {node_id: positions[node_id] for node_id in ordered_ids},
    }
