from coding_agent.pdf_tools import read_pdf


SPEC = {
    "type": "function",
    "function": {
        "name": "read_pdf",
        "description": "Read bounded text from a PDF inside the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "page_start": {"type": "integer", "default": 1},
                "page_end": {"type": "integer", "default": 20},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}


def register(registry):
    registry.register_plugin_tool("read_pdf", SPEC, lambda **kwargs: read_pdf(registry.root, **kwargs))
