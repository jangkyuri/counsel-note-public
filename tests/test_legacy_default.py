from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("selected,expected", [
    (None, "GPU 터보 모드"),
    ("빠른 모드", "빠른 모드"),
    ("HDH 로컬 화자 전사", "HDH 로컬 화자 전사"),
])
def test_fresh_legacy_session_preserves_available_default_and_user_choice(
    selected: str | None, expected: str,
) -> None:
    # Given: no private draft and the operational initializer from the real app.
    source = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    initializer = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "initialize_draft_state"
    )
    state = {} if selected is None else {"selected_mode": selected}
    namespace = {
        "st": SimpleNamespace(session_state=state),
        "load_draft": lambda: {},
        "get_today_kst": lambda: date(2026, 10, 19),
        "get_default_author_name": lambda: "synthetic",
    }
    module = ast.Module(body=[initializer], type_ignores=[])
    exec(compile(module, str(source), "exec"), namespace)

    # When: a new operational session initializes without downloading models.
    namespace["initialize_draft_state"]()

    # Then: the optional CUDA engine is not forced onto the current installation.
    assert state["selected_mode"] == expected
