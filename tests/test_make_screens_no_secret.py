"""Review M3: docs/preview/make_screens.py must read the demo account's handle and secret from the
environment, never carry them as string literals (the old committed pair is burned)."""
import ast
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "docs" / "preview" / "make_screens.py"


def _assigned_values(tree, names):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                elts = tgt.elts if isinstance(tgt, ast.Tuple) else [tgt]
                vals = node.value.elts if isinstance(node.value, ast.Tuple) else [node.value]
                for t, v in zip(elts, vals):
                    if isinstance(t, ast.Name) and t.id in names:
                        yield t.id, v


def test_screens_script_takes_credentials_from_env_not_literals():
    src = SCRIPT.read_text()
    found = dict(_assigned_values(ast.parse(src), {"HANDLE", "SECRET"}))
    assert set(found) == {"HANDLE", "SECRET"}
    for name, value in found.items():
        assert not isinstance(value, ast.Constant), f"{name} is a committed string literal"
    assert "PW_SHOT_HANDLE" in src and "PW_SHOT_SECRET" in src
