import rustworkx as rx
from revu.agents.tools.find_callers import find_callers


def test_unknown_symbol(sample_graph: rx.PyDiGraph) -> None:
    result = find_callers(sample_graph, "nope")
    assert result.found is False


def test_direct_caller_at_k1(sample_graph: rx.PyDiGraph) -> None:
    result = find_callers(sample_graph, "mod_b.bar", k=1)
    assert result.found is True
    names = [c.qualified_name for c in result.callers]
    assert names == ["mod_a.foo"]
    assert result.callers[0].distance == 1


def test_no_direct_callers_returns_empty_not_missing(sample_graph: rx.PyDiGraph) -> None:
    # mod_c.baz has no callers of its own callers (nothing calls foo).
    result = find_callers(sample_graph, "mod_a.foo", k=2)
    assert result.found is True
    assert result.callers == []


def test_transitive_caller_requires_larger_k(sample_graph: rx.PyDiGraph) -> None:
    # foo -> bar -> baz. Callers of baz at k=1 is just bar; at k=2 also foo.
    k1 = find_callers(sample_graph, "mod_c.baz", k=1)
    k2 = find_callers(sample_graph, "mod_c.baz", k=2)

    assert [c.qualified_name for c in k1.callers] == ["mod_b.bar"]
    names_k2 = {c.qualified_name for c in k2.callers}
    assert names_k2 == {"mod_b.bar", "mod_a.foo"}


def test_results_are_nearest_first(sample_graph: rx.PyDiGraph) -> None:
    result = find_callers(sample_graph, "mod_c.baz", k=2)
    distances = [c.distance for c in result.callers]
    assert distances == sorted(distances)


def test_seed_itself_is_never_included_as_its_own_caller(sample_graph: rx.PyDiGraph) -> None:
    result = find_callers(sample_graph, "mod_b.bar", k=3)
    assert all(c.qualified_name != "mod_b.bar" for c in result.callers)
