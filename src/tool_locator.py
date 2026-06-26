"""
Language-aware MCP tool definition locator.

Given a cloned repo directory, finds tool definitions across:
  Python      — AST walk for @X.tool() decorators and Tool(...) instantiations
  TypeScript  — regex for server.tool() calls and tool-object arrays
  JavaScript  — same patterns as TypeScript
  Go          — regex for mcp.NewTool() and WithDescription()
  Rust        — regex for #[tool(description=...)] attribute macros
  Jupyter     — extract Python cells from .ipynb, apply Python extractor
  Generic     — fallback: locate any file containing "inputSchema" key

Returns a list of LocatedTool dataclasses, intentionally lightweight (no
Pydantic) so this module has no schema.py dependency and stays importable
on its own.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator


# ── result type ───────────────────────────────────────────────────────────────

@dataclass
class LocatedTool:
    tool_name: str
    description: str
    input_schema: dict
    source_file: str   # relative path within the repo
    extractor: str     # which method found this (for debugging)

    def preview(self, max_desc: int = 120) -> str:
        desc = (self.description[:max_desc] + "…") if len(self.description) > max_desc else self.description
        props = list(self.input_schema.get("properties", {}).keys())
        return f"{self.tool_name!r}  |  {desc!r}  |  props={props}"


# ── helpers ───────────────────────────────────────────────────────────────────

_SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "__pycache__",
    ".venv", "venv", "env", "vendor", "target",
}

def _iter_files(repo_path: Path, *suffixes: str) -> Generator[Path, None, None]:
    """Yield files with any of the given suffixes, skipping common noise dirs."""
    for p in repo_path.rglob("*"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix in suffixes:
            yield p


def _safe_read(path: Path) -> str | None:
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, OSError):
            continue
    return None


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


# ── Python extractor (AST) ────────────────────────────────────────────────────

_PY_TYPE_MAP: dict[str, dict] = {
    "str":   {"type": "string"},
    "int":   {"type": "integer"},
    "float": {"type": "number"},
    "bool":  {"type": "boolean"},
    "list":  {"type": "array"},
    "dict":  {"type": "object"},
    "bytes": {"type": "string", "format": "byte"},
}


def _annotation_to_schema(node: ast.expr | None) -> dict:
    if node is None:
        return {}
    if isinstance(node, ast.Name):
        return _PY_TYPE_MAP.get(node.id, {})
    if isinstance(node, ast.Constant):
        return _PY_TYPE_MAP.get(str(node.value), {})
    if isinstance(node, ast.Subscript):
        name = getattr(node.value, "id", "")
        if name == "Optional":
            return _annotation_to_schema(node.slice)
        if name in ("List", "list"):
            return {"type": "array", "items": _annotation_to_schema(node.slice)}
        if name in ("Dict", "dict"):
            return {"type": "object"}
    return {}


def _is_tool_decorator(node: ast.expr) -> tuple[bool, str | None]:
    """
    Return (True, description_or_None) if node is a @X.tool() decorator,
    or @tool() / @tool.
    """
    inner = node.func if isinstance(node, ast.Call) else node
    is_tool = (
        (isinstance(inner, ast.Attribute) and inner.attr == "tool") or
        (isinstance(inner, ast.Name)      and inner.id  == "tool")
    )
    if not is_tool:
        return False, None
    # look for description= kwarg
    if isinstance(node, ast.Call):
        for kw in node.keywords:
            if kw.arg == "description" and isinstance(kw.value, ast.Constant):
                return True, str(kw.value.value)
    return True, None


def _build_schema_from_funcdef(func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    """Build an input_schema dict from a function's argument list."""
    props: dict[str, dict] = {}
    required: list[str] = []
    args = func.args

    # positional args with annotations (skip 'self', 'ctx', 'context')
    skip = {"self", "cls", "ctx", "context", "mcp_context"}
    all_args = args.args + getattr(args, "posonlyargs", [])
    defaults_offset = len(all_args) - len(args.defaults)

    for i, arg in enumerate(all_args):
        if arg.arg in skip:
            continue
        schema = _annotation_to_schema(arg.annotation)
        props[arg.arg] = schema if schema else {}
        has_default = i >= defaults_offset
        if not has_default:
            required.append(arg.arg)

    if not props:
        return {"type": "object", "properties": {}}
    result: dict = {"type": "object", "properties": props}
    if required:
        result["required"] = required
    return result


def _extract_tool_objects_from_ast(tree: ast.Module) -> list[tuple[str, str, dict]]:
    """
    Find  types.Tool(name=..., description=..., inputSchema=...)  or
          Tool(name=..., description=..., inputSchema=...)  nodes.
    Returns list of (name, description, input_schema).
    """
    results = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_tool_class = (
            (isinstance(func, ast.Attribute) and func.attr == "Tool") or
            (isinstance(func, ast.Name)      and func.id   == "Tool")
        )
        if not is_tool_class:
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        name = desc = schema = None
        if "name" in kwargs and isinstance(kwargs["name"], ast.Constant):
            name = str(kwargs["name"].value)
        if "description" in kwargs and isinstance(kwargs["description"], ast.Constant):
            desc = str(kwargs["description"].value)
        if "inputSchema" in kwargs:
            try:
                schema = ast.literal_eval(kwargs["inputSchema"])
            except (ValueError, TypeError):
                schema = {}
        if name and desc:
            results.append((name, desc, schema or {}))
    return results


def _find_function_tool_registrations(tree: ast.Module) -> set[str]:
    """
    Collect function names passed to FunctionTool.from_function(func, ...).
    Handles patterns like:
        mcp.add_tool(FunctionTool.from_function(get_jobs, ...))
        FunctionTool.from_function(my_func)
    """
    registered: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_from_function = (
            (isinstance(func, ast.Attribute) and func.attr == "from_function") or
            (isinstance(func, ast.Name)      and func.id   == "from_function")
        )
        if is_from_function and node.args and isinstance(node.args[0], ast.Name):
            registered.add(node.args[0].id)
    return registered


def extract_python(src: str, rel_path: str) -> list[LocatedTool]:
    tools: list[LocatedTool] = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return tools

    # Pass 1: @X.tool() decorated functions
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            found, dec_desc = _is_tool_decorator(dec)
            if not found:
                continue
            name = node.name
            doc  = ast.get_docstring(node) or ""
            desc = dec_desc or doc or ""
            if not desc:
                continue  # skip tools with no description
            schema = _build_schema_from_funcdef(node)
            tools.append(LocatedTool(
                tool_name=name, description=desc,
                input_schema=schema, source_file=rel_path,
                extractor="python-ast-decorator",
            ))
            break  # one decorator match per function is enough

    # Pass 2: explicit Tool(...) instantiations (catches list_tools handlers)
    for name, desc, schema in _extract_tool_objects_from_ast(tree):
        if any(t.tool_name == name for t in tools):
            continue
        tools.append(LocatedTool(
            tool_name=name, description=desc,
            input_schema=schema, source_file=rel_path,
            extractor="python-ast-tool-object",
        ))

    # Pass 3: FunctionTool.from_function(func) registrations (FastMCP pattern)
    registered = _find_function_tool_registrations(tree)
    if registered:
        func_map: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_map[node.name] = node
        for func_name in registered:
            if func_name not in func_map:
                continue
            if any(t.tool_name == func_name for t in tools):
                continue
            node = func_map[func_name]
            doc  = ast.get_docstring(node) or ""
            if not doc:
                continue
            tools.append(LocatedTool(
                tool_name=func_name, description=doc,
                input_schema=_build_schema_from_funcdef(node),
                source_file=rel_path,
                extractor="python-ast-function-tool",
            ))

    return tools


# ── TypeScript / JavaScript extractor (regex) ────────────────────────────────

# Matches any quoted string: "...", '...', or `...` (template literals)
_STR = r'(?:"([^"]+)"|\'([^\']+)\'|`([^`]*?)`)'
_STR_D = r'(?:"([^"]*?)"|\'([^\']*?)\'|`([^`]*?)`)'   # description (may be empty)


def _first_group(*groups: str | None) -> str:
    return next((g for g in groups if g is not None), "")


# server.tool("name", "description" | `desc`, schema, handler)
_TS_TOOL_CALL = re.compile(
    r'server\.tool\s*\(\s*' + _STR + r'\s*,\s*' + _STR_D,
    re.DOTALL,
)

# Factory wrappers: CreateXeroTool / CreateTool / makeTool / newTool / etc.
# Matches any identifier that looks like a tool factory.
_TS_FACTORY = re.compile(
    r'(?:create|Create|make|Make|new|New|define|Define|register|Register)'
    r'\w*[Tt]ool\s*\(\s*' + _STR + r'\s*,\s*' + _STR_D,
    re.DOTALL,
)

# { name: "...", description: "...", inputSchema: { ... } }
# We capture up to 3 levels of brace nesting for the schema.
_TS_TOOL_OBJ = re.compile(
    r'name\s*:\s*["\']([^"\']+)["\']\s*,'           # name:
    r'(?:[^{}]|\{[^{}]*\})*?'
    r'description\s*:\s*["\']([^"\']*)["\']'        # description:
    r'(?:[^{}]|\{[^{}]*\})*?'
    r'inputSchema\s*:\s*'
    r'(\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})',  # inputSchema: {...}
    re.DOTALL,
)


def _try_parse_json5(s: str) -> dict:
    """Best-effort parse of a JS object literal fragment as JSON."""
    # Strip trailing commas and convert single-quotes
    cleaned = re.sub(r",\s*([}\]])", r"\1", s)
    cleaned = re.sub(r"'([^']*)'", r'"\1"', cleaned)
    # Quote bare keys
    cleaned = re.sub(r'(?<=[{,])\s*([a-zA-Z_]\w*)\s*:', r' "\1":', cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {}


def extract_typescript(src: str, rel_path: str) -> list[LocatedTool]:
    tools: list[LocatedTool] = []
    seen: set[str] = set()

    for m in _TS_TOOL_CALL.finditer(src):
        name = _first_group(m.group(1), m.group(2), m.group(3))
        desc = _first_group(m.group(4), m.group(5), m.group(6))
        if not name or name in seen:
            continue
        seen.add(name)
        tools.append(LocatedTool(
            tool_name=name, description=desc.strip(),
            input_schema={}, source_file=rel_path,
            extractor="ts-regex-server-tool",
        ))

    for m in _TS_FACTORY.finditer(src):
        name = _first_group(m.group(1), m.group(2), m.group(3))
        desc = _first_group(m.group(4), m.group(5), m.group(6))
        if not name or name in seen:
            continue
        seen.add(name)
        tools.append(LocatedTool(
            tool_name=name, description=desc.strip(),
            input_schema={}, source_file=rel_path,
            extractor="ts-regex-factory",
        ))

    for m in _TS_TOOL_OBJ.finditer(src):
        name, desc, schema_str = m.group(1), m.group(2), m.group(3)
        if name in seen:
            continue
        seen.add(name)
        schema = _try_parse_json5(schema_str)
        tools.append(LocatedTool(
            tool_name=name, description=desc,
            input_schema=schema, source_file=rel_path,
            extractor="ts-regex-tool-object",
        ))

    return tools


# ── Go extractor (regex) ──────────────────────────────────────────────────────

# mcp.NewTool("name", ..., mcp.WithDescription("description"), ...)
_GO_NEWTOOL = re.compile(
    r'NewTool\s*\(\s*"([^"]+)"\s*(?:,(?:[^)]+)WithDescription\s*\(\s*"([^"]*)")?',
    re.DOTALL,
)

# Tool{Name: "...", Description: "..."}
_GO_TOOL_STRUCT = re.compile(
    r'[Tt]ool\s*\{[^}]*Name\s*:\s*"([^"]+)"[^}]*Description\s*:\s*"([^"]*)"',
    re.DOTALL,
)

# AddTool / RegisterTool with a description string nearby
_GO_ADDTOOL = re.compile(
    r'(?:AddTool|RegisterTool)\s*\([^,]+,\s*"([^"]*)"',
)


def extract_go(src: str, rel_path: str) -> list[LocatedTool]:
    tools: list[LocatedTool] = []
    seen: set[str] = set()

    for m in _GO_NEWTOOL.finditer(src):
        name = m.group(1)
        desc = m.group(2) or ""
        if name in seen:
            continue
        seen.add(name)
        tools.append(LocatedTool(
            tool_name=name, description=desc,
            input_schema={}, source_file=rel_path,
            extractor="go-regex-newtool",
        ))

    for m in _GO_TOOL_STRUCT.finditer(src):
        name, desc = m.group(1), m.group(2)
        if name in seen:
            continue
        seen.add(name)
        tools.append(LocatedTool(
            tool_name=name, description=desc,
            input_schema={}, source_file=rel_path,
            extractor="go-regex-tool-struct",
        ))

    return tools


# ── Rust extractor ────────────────────────────────────────────────────────────

# Regex finding the start of a tool attribute (not _router/_handler)
_RUST_TOOL_ATTR_START = re.compile(r'#\[tool\s*\(')
_RUST_DESC_IN_ATTR    = re.compile(r'description\s*=\s*"([^"]*)"')
_RUST_FN_AFTER_ATTR   = re.compile(r'(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*[<(]')

# Tool::new("name").description("desc")
_RUST_TOOL_BUILDER = re.compile(
    r'Tool\s*::\s*new\s*\(\s*"([^"]+)"\s*\)'
    r'(?:[^\n;]*\n)*?[^\n;]*\.description\s*\(\s*"([^"]*)"',
    re.DOTALL,
)


def _rust_attr_body(src: str, open_pos: int) -> tuple[str, int]:
    """
    Starting just after the opening '(' of a Rust attribute, balance
    parentheses while respecting string literals, and return
    (content_inside_parens, position_after_closing_paren).
    """
    depth = 1
    pos = open_pos
    n = len(src)
    while pos < n and depth > 0:
        c = src[pos]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        elif c == '"':
            pos += 1
            while pos < n:
                if src[pos] == '\\':
                    pos += 1          # skip escaped char
                elif src[pos] == '"':
                    break
                pos += 1
        pos += 1
    return src[open_pos : pos - 1], pos


def extract_rust(src: str, rel_path: str) -> list[LocatedTool]:
    tools: list[LocatedTool] = []
    seen: set[str] = set()

    # Pass 1: #[tool(description = "...", ...)] attribute macros
    # Uses paren-balancing to handle nested attributes like annotations(...).
    for start_m in _RUST_TOOL_ATTR_START.finditer(src):
        attr_content, after_pos = _rust_attr_body(src, start_m.end())
        desc_m = _RUST_DESC_IN_ATTR.search(attr_content)
        if not desc_m:
            continue
        desc = desc_m.group(1)
        fn_m = _RUST_FN_AFTER_ATTR.search(src, after_pos, after_pos + 600)
        if not fn_m:
            continue
        fn_name = fn_m.group(1)
        if fn_name in seen:
            continue
        seen.add(fn_name)
        tools.append(LocatedTool(
            tool_name=fn_name, description=desc,
            input_schema={}, source_file=rel_path,
            extractor="rust-attr-macro",
        ))

    # Pass 2: Tool::new("name").description("desc") builder pattern
    for m in _RUST_TOOL_BUILDER.finditer(src):
        name, desc = m.group(1), m.group(2)
        if name in seen:
            continue
        seen.add(name)
        tools.append(LocatedTool(
            tool_name=name, description=desc,
            input_schema={}, source_file=rel_path,
            extractor="rust-tool-builder",
        ))

    return tools


# ── Jupyter Notebook extractor ────────────────────────────────────────────────

def extract_jupyter(src: str, rel_path: str) -> list[LocatedTool]:
    """Extract Python cells from a .ipynb file, then apply the Python extractor."""
    try:
        nb = json.loads(src)
    except json.JSONDecodeError:
        return []
    py_source = "\n".join(
        "".join(cell.get("source", []))
        for cell in nb.get("cells", [])
        if cell.get("cell_type") == "code"
    )
    return extract_python(py_source, rel_path)


# ── Generic fallback ──────────────────────────────────────────────────────────

# Look for any occurrence of  "inputSchema": { ... }  in any text file
_GENERIC_SCHEMA = re.compile(
    r'"inputSchema"\s*:\s*(\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})',
    re.DOTALL,
)
_GENERIC_NAME  = re.compile(r'"name"\s*:\s*"([^"]+)"')
_GENERIC_DESC  = re.compile(r'"description"\s*:\s*"([^"]+)"')


def extract_generic(src: str, rel_path: str) -> list[LocatedTool]:
    tools: list[LocatedTool] = []
    for m in _GENERIC_SCHEMA.finditer(src):
        # search backwards in a 2 KB window for name/description
        start = max(0, m.start() - 2000)
        window = src[start : m.end()]
        name_m = _GENERIC_NAME.search(window)
        desc_m = _GENERIC_DESC.search(window)
        if not name_m:
            continue
        schema = _try_parse_json5(m.group(1))
        tools.append(LocatedTool(
            tool_name=name_m.group(1),
            description=desc_m.group(1) if desc_m else "",
            input_schema=schema,
            source_file=rel_path,
            extractor="generic-inputSchema-search",
        ))
    return tools


# ── Entry point ───────────────────────────────────────────────────────────────

def locate_tools(repo_path: Path, language: str | None) -> list[LocatedTool]:
    """
    Scan *repo_path* for MCP tool definitions.

    Applies the language-specific extractor first; falls back to a generic
    inputSchema search if the language extractor finds nothing.
    """
    lang = (language or "").lower()
    tools: list[LocatedTool] = []

    if lang in ("python", "jupyter notebook", "jupyter"):
        for f in _iter_files(repo_path, ".py"):
            src = _safe_read(f)
            if src:
                tools += extract_python(src, _rel(f, repo_path))
        for f in _iter_files(repo_path, ".ipynb"):
            src = _safe_read(f)
            if src:
                tools += extract_jupyter(src, _rel(f, repo_path))

    elif lang in ("typescript", "javascript"):
        for f in _iter_files(repo_path, ".ts", ".tsx", ".js", ".mjs"):
            src = _safe_read(f)
            if src:
                tools += extract_typescript(src, _rel(f, repo_path))

    elif lang == "go":
        for f in _iter_files(repo_path, ".go"):
            src = _safe_read(f)
            if src:
                tools += extract_go(src, _rel(f, repo_path))

    elif lang == "rust":
        for f in _iter_files(repo_path, ".rs"):
            src = _safe_read(f)
            if src:
                tools += extract_rust(src, _rel(f, repo_path))

    # For any language: if specific extractor found nothing, try all files
    if not tools:
        for f in _iter_files(repo_path, ".py", ".ts", ".js", ".go", ".rs", ".json"):
            src = _safe_read(f)
            if src:
                tools += extract_generic(src, _rel(f, repo_path))
                tools += extract_python(src, _rel(f, repo_path)) if f.suffix == ".py" else []
                tools += extract_typescript(src, _rel(f, repo_path)) if f.suffix in (".ts", ".js") else []
                tools += extract_go(src, _rel(f, repo_path))       if f.suffix == ".go"            else []
                tools += extract_rust(src, _rel(f, repo_path))     if f.suffix == ".rs"            else []
            if tools:
                break  # stop after first file that yields something

    # Deduplicate by tool_name, keeping first occurrence
    seen: set[str] = set()
    deduped: list[LocatedTool] = []
    for t in tools:
        if t.tool_name not in seen:
            seen.add(t.tool_name)
            deduped.append(t)

    return deduped
