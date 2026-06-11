"""Guard the repository rule that Python tests document their intent."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOTS = (
    REPO_ROOT / "tests",
    REPO_ROOT / "runtime" / "daedalus-vllm" / "tests",
)
MAX_FIRST_LINE = 120


def _python_test_files() -> list[Path]:
    return sorted(path for root in TEST_ROOTS for path in root.glob("test_*.py"))


def _test_nodes(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        is_test_function = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        is_test_class = isinstance(node, ast.ClassDef) and node.name.startswith("Test")
        if is_test_function or is_test_class:
            yield node


def test_python_tests_have_short_human_docstrings():
    """Enforce short test docstrings so edge cases explain why they matter."""

    missing = []
    too_long = []
    for path in _python_test_files():
        relative = path.relative_to(REPO_ROOT)
        for node in _test_nodes(path):
            docstring = ast.get_docstring(node, clean=True)
            label = f"{relative}:{node.lineno}:{node.name}"
            if not docstring:
                missing.append(label)
                continue
            first_line = docstring.splitlines()[0]
            if len(first_line) > MAX_FIRST_LINE:
                too_long.append(f"{label} ({len(first_line)} chars)")

    assert missing == []
    assert too_long == []
