from revu.index.imports import extract_imports


def test_plain_import() -> None:
    edges, unresolved = extract_imports(
        b"import os\n", module_name="pkg.mod", is_package_init=False, file_path="pkg/mod.py"
    )
    assert unresolved == []
    assert len(edges) == 1
    edge = edges[0]
    assert edge.target_module == "os"
    assert edge.imported_name == "os"
    assert edge.local_name == "os"
    assert edge.line == 1


def test_aliased_plain_import() -> None:
    edges, _ = extract_imports(
        b"import os.path as osp\n",
        module_name="pkg.mod",
        is_package_init=False,
        file_path="pkg/mod.py",
    )
    assert len(edges) == 1
    assert edges[0].target_module == "os.path"
    assert edges[0].local_name == "osp"


def test_from_import_multiple_names() -> None:
    edges, _ = extract_imports(
        b"from foo.baz import qux as q, other\n",
        module_name="pkg.mod",
        is_package_init=False,
        file_path="pkg/mod.py",
    )
    assert len(edges) == 2  # not 3 — the module name itself must not be double-counted
    by_local = {e.local_name: e for e in edges}
    assert by_local["q"].target_module == "foo.baz"
    assert by_local["q"].imported_name == "qux"
    assert by_local["other"].target_module == "foo.baz"
    assert by_local["other"].imported_name == "other"


def test_star_import_is_logged_unresolved() -> None:
    edges, unresolved = extract_imports(
        b"from foo import *\n", module_name="pkg.mod", is_package_init=False, file_path="pkg/mod.py"
    )
    assert edges == []
    assert len(unresolved) == 1
    assert "star import" in unresolved[0].reason


def test_relative_import_single_dot_from_regular_module() -> None:
    # pkg.sub is a regular module (not __init__.py) inside package "pkg";
    # `from . import sibling` means a sibling module of "pkg" itself.
    edges, unresolved = extract_imports(
        b"from . import sibling\n",
        module_name="pkg.sub",
        is_package_init=False,
        file_path="pkg/sub.py",
    )
    assert unresolved == []
    assert len(edges) == 1
    assert edges[0].target_module == "pkg"
    assert edges[0].imported_name == "sibling"


def test_relative_import_single_dot_from_package_init() -> None:
    # pkg/__init__.py *is* package "pkg"; `from . import sibling` means a
    # sibling submodule within "pkg" itself, not "pkg"'s parent.
    edges, _ = extract_imports(
        b"from . import sibling\n",
        module_name="pkg",
        is_package_init=True,
        file_path="pkg/__init__.py",
    )
    assert edges[0].target_module == "pkg"


def test_relative_import_with_module_name() -> None:
    edges, _ = extract_imports(
        b"from .relpkg import thing\n",
        module_name="pkg.sub",
        is_package_init=False,
        file_path="pkg/sub.py",
    )
    assert edges[0].target_module == "pkg.relpkg"
    assert edges[0].imported_name == "thing"


def test_relative_import_two_dots_climbs_one_more_package() -> None:
    edges, _ = extract_imports(
        b"from ..pkg2 import thing2\n",
        module_name="revu.index.imports",
        is_package_init=False,
        file_path="revu/index/imports.py",
    )
    # revu.index.imports' package is revu.index; one more level up is revu.
    assert edges[0].target_module == "revu.pkg2"


def test_relative_import_climbing_above_root_is_unresolved() -> None:
    edges, unresolved = extract_imports(
        b"from ..... import too_far\n",
        module_name="pkg.sub",
        is_package_init=False,
        file_path="pkg/sub.py",
    )
    assert edges == []
    assert len(unresolved) == 1
    assert "climbs above" in unresolved[0].reason


def test_imports_inside_function_bodies_are_still_found() -> None:
    source = b"""
def lazy():
    import json
    return json
"""
    edges, _ = extract_imports(
        source, module_name="pkg.mod", is_package_init=False, file_path="pkg/mod.py"
    )
    assert len(edges) == 1
    assert edges[0].target_module == "json"
