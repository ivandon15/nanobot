from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool


class SampleTool(Tool):
    @property
    def name(self) -> str:
        return "sample"

    @property
    def description(self) -> str:
        return "sample tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
                "mode": {"type": "string", "enum": ["fast", "full"]},
                "meta": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string"},
                        "flags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["tag"],
                },
            },
            "required": ["query", "count"],
        }

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def test_validate_params_missing_required() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi"})
    assert "missing required count" in "; ".join(errors)


def test_validate_params_type_and_range() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 0})
    assert any("count must be >= 1" in e for e in errors)

    errors = tool.validate_params({"query": "hi", "count": "2"})
    assert any("count should be integer" in e for e in errors)


def test_validate_params_enum_and_min_length() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "h", "count": 2, "mode": "slow"})
    assert any("query must be at least 2 chars" in e for e in errors)
    assert any("mode must be one of" in e for e in errors)


def test_validate_params_nested_object_and_array() -> None:
    tool = SampleTool()
    errors = tool.validate_params(
        {
            "query": "hi",
            "count": 2,
            "meta": {"flags": [1, "ok"]},
        }
    )
    assert any("missing required meta.tag" in e for e in errors)
    assert any("meta.flags[0] should be string" in e for e in errors)


def test_validate_params_ignores_unknown_fields() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 2, "extra": "x"})
    assert errors == []


async def test_registry_returns_validation_error() -> None:
    reg = ToolRegistry()
    reg.register(SampleTool())
    result = await reg.execute("sample", {"query": "hi"})
    assert "Invalid parameters" in result


def test_validate_params_non_dict_input() -> None:
    """validate_params should return an error for non-dict input, not crash."""
    tool = SampleTool()
    errors = tool.validate_params(["query", "hi"])  # type: ignore[arg-type]
    assert errors and "object" in errors[0]


def test_cast_params_string_to_integer() -> None:
    """cast_params should coerce '5' -> 5 for integer schema fields."""
    tool = SampleTool()
    result = tool.cast_params({"query": "hi", "count": "5"})
    assert result["count"] == 5
    assert isinstance(result["count"], int)


def test_cast_params_string_to_boolean() -> None:
    """cast_params should coerce 'true'/'false' strings to bool."""
    class BoolTool(Tool):
        @property
        def name(self) -> str: return "bool_tool"
        @property
        def description(self) -> str: return "bool tool"
        @property
        def parameters(self) -> dict[str, Any]:
            return {"type": "object", "properties": {"flag": {"type": "boolean"}}, "required": ["flag"]}
        async def execute(self, **kwargs: Any) -> str: return "ok"

    tool = BoolTool()
    assert tool.cast_params({"flag": "true"})["flag"] is True
    assert tool.cast_params({"flag": "false"})["flag"] is False


def test_exec_extract_absolute_paths_keeps_full_windows_path() -> None:
    cmd = r"type C:\user\workspace\txt"
    paths = ExecTool._extract_absolute_paths(cmd)
    assert paths == [r"C:\user\workspace\txt"]


def test_exec_extract_absolute_paths_ignores_relative_posix_segments() -> None:
    cmd = ".venv/bin/python script.py"
    paths = ExecTool._extract_absolute_paths(cmd)
    assert "/bin/python" not in paths


def test_exec_extract_absolute_paths_captures_posix_absolute_paths() -> None:
    cmd = "cat /tmp/data.txt > /tmp/out.txt"
    paths = ExecTool._extract_absolute_paths(cmd)
    assert "/tmp/data.txt" in paths
    assert "/tmp/out.txt" in paths
