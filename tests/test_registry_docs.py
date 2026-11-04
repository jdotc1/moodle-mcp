from moodle_academic_mcp.moodle.catalog import load_catalog
from moodle_academic_mcp.server import ops  # noqa: F401
from moodle_academic_mcp.server.permissions import TOOL_FUNCTIONS
from moodle_academic_mcp.server.registry import OPS


def test_every_tool_has_permission_entry_and_known_functions():
    assert set(TOOL_FUNCTIONS) == set(OPS)
    cat = load_catalog()
    for tool, fns in TOOL_FUNCTIONS.items():
        for f in fns:
            if f.startswith("(") or f == "webservice/upload.php":
                continue
            assert cat.get(f) is not None, f"{tool}: {f} not in any catalog"


def test_annotations_are_consistent_with_effects():
    cat = load_catalog()
    from moodle_academic_mcp.moodle.catalog import Effect

    for name, o in OPS.items():
        if o.read_only:
            for f in TOOL_FUNCTIONS[name]:
                info = cat.get(f)
                if info is not None:
                    assert info.effect is Effect.READ, f"read-only tool {name} calls non-read function {f}"
        assert o.description and "Args:" not in o.description


def test_bundled_catalog_has_provenance():
    cat = load_catalog()
    assert "ufsc_mobile" in cat.provenance and cat.provenance["ufsc_mobile"]["exported"] == "2026-11-03"
    assert cat.get("mod_assign_save_submission").params
