from services.cognitive.providers.gemini_provider import GeminiProvider


def test_function_declaration_types_are_not_all_string():
    # Regression test: generate_stream() used to hardcode every parameter's
    # Gemini type as STRING regardless of the JSON Schema "type" field, which
    # silently broke any tool with int/bool/array params (e.g. MCP tools like
    # vscode_read_range(start_line: int, end_line: int)).
    provider = GeminiProvider.__new__(GeminiProvider)  # skip __init__, no API client needed

    tools_schema = [{
        "name": "vscode_read_range",
        "description": "read a line range",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "verbose": {"type": "boolean"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["path", "start_line", "end_line"],
        },
    }]

    decls = provider._build_function_declarations(tools_schema)
    assert len(decls) == 1

    props = decls[0].parameters.properties
    assert str(props["path"].type) == "Type.STRING"
    assert str(props["start_line"].type) == "Type.INTEGER"
    assert str(props["end_line"].type) == "Type.INTEGER"
    assert str(props["verbose"].type) == "Type.BOOLEAN"
    assert str(props["tags"].type) == "Type.ARRAY"
    assert str(props["tags"].items.type) == "Type.STRING"


def test_unknown_json_type_falls_back_to_string():
    provider = GeminiProvider.__new__(GeminiProvider)
    tools_schema = [{
        "name": "weird_tool",
        "description": "d",
        "parameters": {
            "type": "object",
            "properties": {"x": {"type": "something-unrecognized"}},
            "required": [],
        },
    }]
    decls = provider._build_function_declarations(tools_schema)
    assert str(decls[0].parameters.properties["x"].type) == "Type.STRING"
