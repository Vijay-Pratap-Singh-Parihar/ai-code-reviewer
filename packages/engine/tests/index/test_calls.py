from revu.index.calls import resolve_calls
from revu.index.symbols import extract_symbols
from revu.index.types import Symbol, SymbolKind


def test_resolves_bare_call_to_top_level_function() -> None:
    source = b"""
def helper():
    pass

def caller():
    helper()
"""
    symbols = extract_symbols(source, "pkg.mod", "pkg/mod.py")
    all_symbols = {s.qualified_name: s for s in symbols}
    local_scope = {"helper": "pkg.mod.helper", "caller": "pkg.mod.caller"}

    edges, unresolved = resolve_calls(
        source,
        module_name="pkg.mod",
        file_path="pkg/mod.py",
        module_symbols=symbols,
        local_scope=local_scope,
        import_module_scope={},
        all_symbols=all_symbols,
        known_modules={"pkg.mod"},
    )

    assert unresolved == []
    assert len(edges) == 1
    assert edges[0].caller == "pkg.mod.caller"
    assert edges[0].callee == "pkg.mod.helper"


def test_resolves_self_method_call_within_same_class() -> None:
    source = b"""
class Foo:
    def a(self):
        self.b()

    def b(self):
        pass
"""
    symbols = extract_symbols(source, "pkg.mod", "pkg/mod.py")
    all_symbols = {s.qualified_name: s for s in symbols}

    edges, unresolved = resolve_calls(
        source,
        module_name="pkg.mod",
        file_path="pkg/mod.py",
        module_symbols=symbols,
        local_scope={},
        import_module_scope={},
        all_symbols=all_symbols,
        known_modules={"pkg.mod"},
    )

    assert unresolved == []
    assert edges == [
        e for e in edges if e.caller == "pkg.mod.Foo.a" and e.callee == "pkg.mod.Foo.b"
    ]
    assert len(edges) == 1


def test_resolves_module_attribute_call_through_import() -> None:
    source = b"""
def caller():
    utils.helper()
"""
    symbols = extract_symbols(source, "pkg.mod", "pkg/mod.py")
    utils_helper = Symbol(
        qualified_name="pkg.utils.helper",
        name="helper",
        kind=SymbolKind.FUNCTION,
        file_path="pkg/utils.py",
        line_start=1,
        line_end=2,
    )
    all_symbols = {s.qualified_name: s for s in symbols}
    all_symbols[utils_helper.qualified_name] = utils_helper

    edges, unresolved = resolve_calls(
        source,
        module_name="pkg.mod",
        file_path="pkg/mod.py",
        module_symbols=symbols,
        local_scope={},
        import_module_scope={"utils": "pkg.utils"},
        all_symbols=all_symbols,
        known_modules={"pkg.mod", "pkg.utils"},
    )

    assert unresolved == []
    assert len(edges) == 1
    assert edges[0].callee == "pkg.utils.helper"


def test_resolves_classmethod_or_staticmethod_call_via_class_name() -> None:
    source = b"""
class Foo:
    def s(self):
        pass

def caller():
    Foo.s()
"""
    symbols = extract_symbols(source, "pkg.mod", "pkg/mod.py")
    all_symbols = {s.qualified_name: s for s in symbols}
    local_scope = {"Foo": "pkg.mod.Foo"}

    edges, unresolved = resolve_calls(
        source,
        module_name="pkg.mod",
        file_path="pkg/mod.py",
        module_symbols=symbols,
        local_scope=local_scope,
        import_module_scope={},
        all_symbols=all_symbols,
        known_modules={"pkg.mod"},
    )

    assert unresolved == []
    assert len(edges) == 1
    assert edges[0].callee == "pkg.mod.Foo.s"


def test_call_through_local_variable_is_unresolved_not_guessed() -> None:
    source = b"""
def caller():
    obj = get_thing()
    obj.method()
"""
    symbols = extract_symbols(source, "pkg.mod", "pkg/mod.py")
    all_symbols = {s.qualified_name: s for s in symbols}

    edges, unresolved = resolve_calls(
        source,
        module_name="pkg.mod",
        file_path="pkg/mod.py",
        module_symbols=symbols,
        local_scope={},
        import_module_scope={},
        all_symbols=all_symbols,
        known_modules={"pkg.mod"},
    )

    method_call_unresolved = [u for u in unresolved if u.name == "obj.method"]
    assert len(method_call_unresolved) == 1
    assert "type inference" in method_call_unresolved[0].reason
    assert edges == []


def test_attribute_chain_deeper_than_one_hop_is_unresolved() -> None:
    source = b"""
def caller():
    a.b.c()
"""
    symbols = extract_symbols(source, "pkg.mod", "pkg/mod.py")
    all_symbols = {s.qualified_name: s for s in symbols}

    _edges, unresolved = resolve_calls(
        source,
        module_name="pkg.mod",
        file_path="pkg/mod.py",
        module_symbols=symbols,
        local_scope={},
        import_module_scope={},
        all_symbols=all_symbols,
        known_modules={"pkg.mod"},
    )

    assert len(unresolved) == 1
    assert "one hop" in unresolved[0].reason


def test_builtin_call_is_labelled_as_builtin_not_a_gap() -> None:
    source = b"""
def caller():
    len([1, 2, 3])
"""
    symbols = extract_symbols(source, "pkg.mod", "pkg/mod.py")
    all_symbols = {s.qualified_name: s for s in symbols}

    _edges, unresolved = resolve_calls(
        source,
        module_name="pkg.mod",
        file_path="pkg/mod.py",
        module_symbols=symbols,
        local_scope={},
        import_module_scope={},
        all_symbols=all_symbols,
        known_modules={"pkg.mod"},
    )

    assert len(unresolved) == 1
    assert unresolved[0].reason == "python builtin"


def test_external_import_call_is_labelled_external_not_a_gap() -> None:
    source = b"""
def caller():
    HTTPException(404)
"""
    symbols = extract_symbols(source, "pkg.mod", "pkg/mod.py")
    all_symbols = {s.qualified_name: s for s in symbols}

    _edges, unresolved = resolve_calls(
        source,
        module_name="pkg.mod",
        file_path="pkg/mod.py",
        module_symbols=symbols,
        local_scope={},
        import_module_scope={},
        all_symbols=all_symbols,
        known_modules={"pkg.mod"},
        external_names=frozenset({"HTTPException"}),
    )

    assert len(unresolved) == 1
    assert unresolved[0].reason == "external import (module not indexed)"


def test_module_level_call_site_is_skipped_not_attributed_to_anything() -> None:
    source = b"""
def helper():
    pass

helper()
"""
    symbols = extract_symbols(source, "pkg.mod", "pkg/mod.py")
    all_symbols = {s.qualified_name: s for s in symbols}

    edges, unresolved = resolve_calls(
        source,
        module_name="pkg.mod",
        file_path="pkg/mod.py",
        module_symbols=symbols,
        local_scope={"helper": "pkg.mod.helper"},
        import_module_scope={},
        all_symbols=all_symbols,
        known_modules={"pkg.mod"},
    )

    assert edges == []
    assert unresolved == []
