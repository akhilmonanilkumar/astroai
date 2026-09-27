"""astro/ must stay pure: no I/O, config or AGPL imports (see CLAUDE.md)."""

import ast
from pathlib import Path

import guruji.astro

ASTRO_DIR = Path(guruji.astro.__file__).parent
FORBIDDEN_MODULES = {
    "httpx",
    "requests",
    "urllib",
    "socket",
    "redis",
    "os",
    "pathlib",
    "subprocess",
    "swisseph",
    "guruji.config",
    "guruji.ephemeris",
    "guruji.db",
}
FORBIDDEN_CALLS = {"open", "print", "input"}


def _imports(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_astro_has_no_io() -> None:
    files = sorted(ASTRO_DIR.glob("*.py"))
    assert len(files) >= 8
    for path in files:
        tree = ast.parse(path.read_text("utf-8"))
        for mod in _imports(tree):
            root_hit = {m for m in FORBIDDEN_MODULES if mod == m or mod.startswith(m + ".")}
            assert not root_hit, f"{path.name} imports {mod}"
        calls = {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert not calls & FORBIDDEN_CALLS, f"{path.name} calls {calls & FORBIDDEN_CALLS}"
