"""M2 map layoutの決定性と既存座標維持。"""

from astrolabe.layout import build_layout

EDGES = [
    {"src": "a", "dst": "b", "type": "prerequisite"},
    {"src": "b", "dst": "c", "type": "related"},
]


def test_same_graph_without_previous_layout_has_same_positions():
    first = build_layout(["c", "a", "b"], EDGES)
    second = build_layout(["b", "c", "a"], list(reversed(EDGES)))

    assert first == second


def test_adding_node_never_moves_existing_positions():
    first = build_layout(["a", "b"], EDGES[:1])
    second = build_layout(["a", "b", "c"], EDGES, first)

    assert second["positions"]["a"] == first["positions"]["a"]
    assert second["positions"]["b"] == first["positions"]["b"]
    assert "c" in second["positions"]
