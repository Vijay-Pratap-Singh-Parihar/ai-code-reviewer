from pathlib import Path

from revu.index.symbols import discover_source_roots, extract_symbols, module_name_for_file
from revu.index.types import SymbolKind

SOURCE = b'''
def top_level():
    def nested():
        pass
    return nested


class Foo:
    def method_one(self):
        pass

    class Inner:
        def inner_method(self):
            pass


class Bar:
    pass
'''


def test_extract_symbols_finds_functions_classes_and_methods() -> None:
    symbols = extract_symbols(SOURCE, "pkg.mod", "pkg/mod.py")
    by_name = {s.qualified_name: s for s in symbols}

    assert by_name["pkg.mod.top_level"].kind == SymbolKind.FUNCTION
    assert by_name["pkg.mod.top_level.nested"].kind == SymbolKind.FUNCTION
    assert by_name["pkg.mod.Foo"].kind == SymbolKind.CLASS
    assert by_name["pkg.mod.Foo.method_one"].kind == SymbolKind.METHOD
    assert by_name["pkg.mod.Foo.Inner"].kind == SymbolKind.CLASS
    assert by_name["pkg.mod.Foo.Inner.inner_method"].kind == SymbolKind.METHOD
    assert by_name["pkg.mod.Bar"].kind == SymbolKind.CLASS


def test_extract_symbols_line_ranges_are_one_indexed_and_cover_the_body() -> None:
    symbols = extract_symbols(SOURCE, "pkg.mod", "pkg/mod.py")
    top_level = next(s for s in symbols if s.qualified_name == "pkg.mod.top_level")
    assert top_level.line_start == 2
    assert top_level.line_end == 5
    assert top_level.file_path == "pkg/mod.py"


def test_extract_symbols_on_empty_file_returns_nothing() -> None:
    assert extract_symbols(b"", "pkg.empty", "pkg/empty.py") == []


def test_discover_source_roots_finds_src_layout(tmp_path: Path) -> None:
    src = tmp_path / "packages" / "engine" / "src"
    (src / "revu").mkdir(parents=True)
    (src / "revu" / "__init__.py").write_text("")
    (src / "revu" / "models.py").write_text("")
    (src / "revu" / "index").mkdir()
    (src / "revu" / "index" / "__init__.py").write_text("")

    roots = discover_source_roots(tmp_path)
    assert src in roots


def test_discover_source_roots_finds_flat_layout(tmp_path: Path) -> None:
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")

    roots = discover_source_roots(tmp_path)
    assert tmp_path in roots


def test_module_name_for_file_matches_real_import_path(tmp_path: Path) -> None:
    src = tmp_path / "packages" / "engine" / "src"
    pkg = src / "revu" / "index"
    pkg.mkdir(parents=True)
    (src / "revu" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "symbols.py").write_text("")

    roots = discover_source_roots(tmp_path)
    module_name, is_init = module_name_for_file(pkg / "symbols.py", roots)
    assert module_name == "revu.index.symbols"
    assert is_init is False

    module_name, is_init = module_name_for_file(pkg / "__init__.py", roots)
    assert module_name == "revu.index"
    assert is_init is True


def test_module_name_for_file_falls_back_to_repo_root(tmp_path: Path) -> None:
    (tmp_path / "script.py").write_text("")
    roots = discover_source_roots(tmp_path)
    module_name, is_init = module_name_for_file(tmp_path / "script.py", roots)
    assert module_name == "script"
    assert is_init is False
