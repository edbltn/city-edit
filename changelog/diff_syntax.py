"""Extension-based syntax highlighting for the changelog diff reports.

Stdlib-only (regex tokenizers), shared by the build_*_report.py scripts:

    from diff_syntax import colorize_diff, SYNTAX_CSS
    ...
    <pre class="diff">{colorize_diff(chunk, path)}</pre>   # path picks the language
    ...
    <style>...{SYNTAX_CSS}...</style>

Token colors ride on top of the d-add/d-del line backgrounds (GitHub-style):
the line span keeps the green/red background, inner .s-* spans set foreground.
"""
import html
import re

LANG_BY_EXT = {
    ".ts": "js", ".tsx": "js", ".js": "js", ".jsx": "js", ".mjs": "js",
    ".py": "py",
    ".css": "css",
    ".html": "html", ".xml": "html",
    ".json": "json",
    ".md": "md",
    ".sh": "sh", ".bash": "sh",
    ".yml": "yaml", ".yaml": "yaml",
    ".lua": "lua",
    ".sql": "sql",
}

JS_KEYWORDS = {
    "abstract", "as", "async", "await", "break", "case", "catch", "class",
    "const", "continue", "debugger", "declare", "default", "delete", "do",
    "else", "enum", "export", "extends", "finally", "for", "from", "function",
    "get", "if", "implements", "import", "in", "instanceof", "interface",
    "keyof", "let", "namespace", "new", "of", "override", "private",
    "protected", "public", "readonly", "return", "satisfies", "set", "static",
    "super", "switch", "this", "throw", "try", "type", "typeof", "var",
    "void", "while", "yield",
}
JS_LITERALS = {"true", "false", "null", "undefined", "NaN", "Infinity"}

PY_KEYWORDS = {
    "and", "as", "assert", "async", "await", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "finally", "for", "from",
    "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or",
    "pass", "raise", "return", "try", "while", "with", "yield", "match", "case",
}
PY_LITERALS = {"True", "False", "None", "self", "cls"}

SH_KEYWORDS = {
    "if", "then", "else", "elif", "fi", "for", "while", "do", "done", "case",
    "esac", "function", "in", "local", "export", "return", "set", "source",
}
LUA_KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "if", "in", "local", "nil", "not", "or", "repeat", "return", "then",
    "true", "until", "while",
}
SQL_KEYWORDS = {
    "select", "from", "where", "insert", "into", "values", "update", "set",
    "delete", "create", "table", "index", "on", "join", "left", "right",
    "inner", "outer", "group", "by", "order", "limit", "and", "or", "not",
    "null", "as", "distinct", "having", "union", "all", "exists", "in",
    "primary", "key", "foreign", "references", "constraint", "alter", "add",
    "drop", "begin", "commit", "rollback", "conflict", "do", "nothing", "returning",
}

_STR = r""""(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?"""

TOKEN_RES = {
    "js": re.compile(
        r"(?P<com>//[^\n]*|/\*.*?\*/)"
        r"|(?P<str>`(?:\\.|[^`\\])*`?|" + _STR + r")"
        r"|(?P<num>\b(?:0[xXbBoO][\da-fA-F_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)n?\b)"
        r"|(?P<word>[A-Za-z_$][\w$]*)"
    ),
    "py": re.compile(
        r"(?P<com>#[^\n]*)"
        r"|(?P<str>[rRbBfFuU]{0,2}(?:\"\"\".*?(?:\"\"\"|$)|'''.*?(?:'''|$)|" + _STR + r"))"
        r"|(?P<dec>@[\w.]+)"
        r"|(?P<num>\b(?:0[xXbBoO][\da-fA-F_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)\b)"
        r"|(?P<word>[A-Za-z_]\w*)"
    ),
    "css": re.compile(
        r"(?P<com>/\*.*?(?:\*/|$))"
        r"|(?P<str>" + _STR + r")"
        r"|(?P<dec>@[\w-]+)"
        r"|(?P<num>#[0-9a-fA-F]{3,8}\b|(?<![\w-])\.?\d[\d.]*(?:px|em|rem|s|ms|%|vh|vw|vmin|vmax|fr|deg|ch|ex)?\b)"
        r"|(?P<prop>[-a-zA-Z]+(?=\s*:))"
    ),
    "html": re.compile(
        r"(?P<com><!--.*?(?:-->|$))"
        r"|(?P<str>" + _STR + r")"
        r"|(?P<tag></?[a-zA-Z][\w-]*|/?>|<!\w+)"
        r"|(?P<attr>\b[a-zA-Z-]+(?==))"
    ),
    "json": re.compile(
        r"(?P<prop>\"(?:\\.|[^\"\\])*\"(?=\s*:))"
        r"|(?P<str>\"(?:\\.|[^\"\\])*\"?)"
        r"|(?P<num>-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)"
        r"|(?P<word>\b(?:true|false|null)\b)"
    ),
    "sh": re.compile(
        r"(?P<com>#[^\n]*)"
        r"|(?P<str>" + _STR + r")"
        r"|(?P<dec>\$\{?\w+\}?)"
        r"|(?P<num>\b\d+\b)"
        r"|(?P<word>[A-Za-z_][\w-]*)"
    ),
    "lua": re.compile(
        r"(?P<com>--[^\n]*)"
        r"|(?P<str>" + _STR + r")"
        r"|(?P<num>\b\d[\d.]*\b)"
        r"|(?P<word>[A-Za-z_]\w*)"
    ),
    "sql": re.compile(
        r"(?P<com>--[^\n]*)"
        r"|(?P<str>'(?:''|[^'])*'?)"
        r"|(?P<num>\b\d[\d.]*\b)"
        r"|(?P<word>[A-Za-z_]\w*)"
    ),
    "yaml": re.compile(
        r"(?P<com>#[^\n]*)"
        r"|(?P<prop>[\w.-]+(?=\s*:))"
        r"|(?P<str>" + _STR + r")"
        r"|(?P<num>\b\d[\d.]*\b)"
        r"|(?P<word>\b(?:true|false|null|yes|no)\b)"
    ),
}

KEYWORDS = {"js": JS_KEYWORDS, "py": PY_KEYWORDS, "sh": SH_KEYWORDS, "lua": LUA_KEYWORDS}
LITERALS = {"js": JS_LITERALS, "py": PY_LITERALS}

# GitHub-light-ish token palette; foregrounds only so the add/del line
# backgrounds still carry the diff meaning.
SYNTAX_CSS = """
  .s-kw { color: #a52a5a; }
  .s-str { color: #0a3069; }
  .s-com { color: #77716a; font-style: italic; }
  .s-num { color: #0550ae; }
  .s-lit { color: #0550ae; }
  .s-fn { color: #6639ba; }
  .s-type { color: #953800; }
  .s-prop { color: #116329; }
  .s-tag { color: #116329; }
  .s-attr { color: #0550ae; }
  .s-dec { color: #6639ba; }
"""

GROUP_CLS = {
    "com": "s-com", "str": "s-str", "num": "s-num", "dec": "s-dec",
    "prop": "s-prop", "tag": "s-tag", "attr": "s-attr",
}


def lang_for_path(path: str) -> str | None:
    dot = path.rfind(".")
    if dot == -1:
        return None
    return LANG_BY_EXT.get(path[dot:].lower())


def _classify_word(word: str, lang: str, code: str, end: int) -> str | None:
    if word in KEYWORDS.get(lang, ()) or (lang == "sql" and word.lower() in SQL_KEYWORDS):
        return "s-kw"
    if word in LITERALS.get(lang, ()):
        return "s-lit"
    rest = code[end:end + 2].lstrip()
    if rest.startswith("("):
        return "s-fn"
    if word[0].isupper() and lang in ("js", "py"):
        return "s-type"
    return None


def highlight_code(code: str, lang: str | None) -> str:
    """Return HTML for one line of source code (no diff marker)."""
    rx = TOKEN_RES.get(lang or "")
    if rx is None:
        return html.escape(code)
    if lang == "js":
        # Stateless heuristic for multi-line block comments / JSDoc bodies.
        s = code.lstrip()
        if s.startswith(("* ", "*/")) or s == "*" or (s.startswith("/*") and "*/" not in s):
            return f'<span class="s-com">{html.escape(code)}</span>'
    out = []
    pos = 0
    for m in rx.finditer(code):
        if m.start() > pos:
            out.append(html.escape(code[pos:m.start()]))
        kind = m.lastgroup
        text = m.group()
        if kind == "word":
            cls = _classify_word(text, lang, code, m.end()) if lang != "json" and lang != "yaml" else "s-lit"
        else:
            cls = GROUP_CLS.get(kind)
        if cls:
            out.append(f'<span class="{cls}">{html.escape(text)}</span>')
        else:
            out.append(html.escape(text))
        pos = m.end()
    out.append(html.escape(code[pos:]))
    return "".join(out)


_META_PREFIXES = (
    "diff ", "index ", "new file", "deleted file", "similarity", "rename ",
    "copy ", "old mode", "new mode", "Binary", "\\ No newline",
)


def colorize_diff(diff_chunk: str, path: str = "") -> str:
    """Render a per-file diff chunk as HTML lines with syntax-highlighted code."""
    lang = lang_for_path(path)
    out = []
    for raw in diff_chunk.splitlines():
        if raw.startswith("+++") or raw.startswith("---"):
            cls, body = "d-meta", html.escape(raw)
        elif raw.startswith("@@"):
            cls, body = "d-hunk", html.escape(raw)
        elif raw.startswith(_META_PREFIXES):
            cls, body = "d-meta", html.escape(raw)
        elif raw.startswith("+"):
            cls, body = "d-add", "+" + highlight_code(raw[1:], lang)
        elif raw.startswith("-"):
            cls, body = "d-del", "-" + highlight_code(raw[1:], lang)
        else:
            cls, body = "d-ctx", html.escape(raw[:1]) + highlight_code(raw[1:], lang)
        out.append(f'<span class="{cls}">{body or "&nbsp;"}</span>')
    return "\n".join(out)
