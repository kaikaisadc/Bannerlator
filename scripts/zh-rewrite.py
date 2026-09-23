#!/usr/bin/env python3
"""Position-aware Simplified-Chinese display-text rewriter for the zh-cn fork.

Git sources stay English (merge-friendly); this script rewrites display-position
string literals to Chinese at BUILD TIME via the preBuild hook in
app/build.gradle. Only display positions are touched - identifiers, keys,
state values, log messages and comparisons are never modified. Rewriting is
idempotent: a literal that is already Chinese (no English word) is skipped.

Modes:
  --extract [--out F]   scan sources, print stats, write numbered flat list
                        (idx<TAB>key) of literals needing translation
  --check               exit 1 if any display literal has no map entry
  (default)             rewrite in place using scripts/zh-map.json
"""

import json
import re
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parent.parent
SRC = ROOT / "app/src/main/java"
RES = ROOT / "app/src/main/res"
MAP = SCRIPT.parent / "zh-map.json"

KT_FILES = sorted(SRC.rglob("*.kt"))
JAVA_FILES = sorted(SRC.rglob("*.java"))
XML_FILES = sorted(
    list((RES / "layout").glob("*.xml"))
    + [
        p
        for d in RES.iterdir()
        if d.is_dir() and d.name.startswith("layout-")
        for p in d.glob("*.xml")
    ]
    + list((RES / "menu").glob("*.xml"))
)


def lex_string(s, i, kotlin=True):
    """s[i] is an opening quote. Return (start, end_after_close, raw_content)
    or None if unterminated. Handles escapes, Kotlin ${...} templates with
    nested strings, $ident templates and triple-quoted raw strings."""
    if s.startswith('"""', i):
        j = s.find('"""', i + 3)
        if j < 0:
            return None
        return i, j + 3, s[i + 3 : j]
    start = i
    i += 1
    out = []
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            out.append(s[i : i + 2])
            i += 2
            continue
        if c == '"':
            return start, i + 1, "".join(out)
        if kotlin and c == "$" and i + 1 < n and s[i + 1] == "{":
            tstart = i
            depth = 1
            i += 2
            while i < n and depth > 0:
                ch = s[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                elif ch == '"':
                    r = lex_string(s, i, kotlin)
                    if r is None:
                        return None
                    i = r[1]
                    continue
                i += 1
            out.append(s[tstart:i])
            continue
        out.append(c)
        i += 1
    return None


def _skip_ws(s, i):
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    return i


def _parse_lit_or_chain(s, i, kotlin=True):
    """Parse `lit` or `lit + lit + ...` starting at s[i] (must be a quote).
    Return (kind, pieces, next) where kind is 'chain' (pure literals) or
    'chain-mixed' (leading literals of a chain with non-literal pieces)."""
    if i >= len(s) or s[i] != '"':
        return None
    r = lex_string(s, i, kotlin)
    if r is None:
        return None
    pieces = [(r[0], r[2])]
    i = _skip_ws(s, r[1])
    while i < len(s) and s[i] == "+":
        j = _skip_ws(s, i + 1)
        if j >= len(s) or s[j] != '"':
            return "chain-mixed", pieces, i
        r = lex_string(s, j, kotlin)
        if r is None:
            return None
        pieces.append((r[0], r[2]))
        i = _skip_ws(s, r[1])
    return "chain", pieces, i


def classify_expr(s, i, kotlin=True):
    """Classify the expression starting at s[i]. Return dict or None.
    kinds: chain (pure literal chain), chain-mixed, ifelse.
    'leaves' is a list of (kind, pieces) translatable units."""
    i = _skip_ws(s, i)
    if i >= len(s):
        return None
    if s[i] == '"':
        kind, pieces, nxt = _parse_lit_or_chain(s, i, kotlin)
        return {"kind": kind, "leaves": [(kind, pieces)], "next": nxt}
    m = re.match(r"if\s*\(", s[i:])
    if m:
        depth = 1
        j = i + m.end()
        while j < len(s) and depth > 0:
            ch = s[j]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == '"':
                r = lex_string(s, j, kotlin)
                if r is None:
                    return None
                j = r[1]
                continue
            j += 1
        if depth != 0:
            return None
        leaves = []
        rb = classify_expr(s, _skip_ws(s, j), kotlin)
        if rb is None:
            return None
        leaves.extend(rb["leaves"])
        k = _skip_ws(s, rb["next"])
        m2 = re.match(r"else\b", s[k:])
        if not m2:
            return None
        re_ = classify_expr(s, _skip_ws(s, k + m2.end()), kotlin)
        if re_ is None:
            return None
        leaves.extend(re_["leaves"])
        return {"kind": "ifelse", "leaves": leaves, "next": re_["next"]}
    return None


NAMED_PARAMS = {
    "text": None,
    "title": None,
    "subtitle": None,
    "label": None,
    "body": None,
    "message": None,
    "info": None,
    "status": None,
    "error": None,
    "hint": None,
    "desc": None,
    "placeholder": None,
    "actionLabel": None,
    "buttonText": None,
    "statusText": None,
    "installBtnText": None,
    "updateStatusText": None,
    "progressText": None,
    "verifyStatusText": None,
    "cloudSaveStatusText": None,
    "pauseBtnText": None,
    "resultBarMsg": None,
    "storeLabel": None,
    "msg": None,
    "disabledReason": None,
    "contentDescription": None,
    "value": "need_space",
}
NAMED_PARAM_RE = re.compile(
    r"(?<![\w.\"])(%s)\s*(:\s*String\s*)?=\s*" % "|".join(NAMED_PARAMS)
)

# extra display positions where only a filtered literal is translated
NAMED_EXTRA = [
    (
        re.compile(r"(?<=[\w\]\)])\.value\s*=\s*"),
        "need_space",
    ),  # StateFlow _x.value = "..."
    (re.compile(r"\?:\s*"), "need_space"),  # expr ?: "fallback"
]

CALLS = [
    (r"(?<![\w.])Text\s*\(", [0], None),
    (
        r"\.(setTitle|setMessage|setText|setPositiveButton|setNegativeButton|"
        r"setNeutralButton|setHint|setContentDescription)\s*\(",
        [0],
        None,
    ),
    (
        r"(?<!\w)(onMessage|onStatus|onError|onInstallError|onToast|setStatus|"
        r"setSync|showCloudStatus|showError|toast|showThemedMessage)\s*\(",
        [0],
        None,
    ),
    (r"(?<![\w.])XmbMenu\s*\(", [0], None),
    (
        r"\bXmbRow\.(Header|Toggle|Choice|Text|Slider|Link|Action|Info|External)\s*\(",
        [1],
        None,
    ),
    (r"\bXmbTools\.para\s*\(", [1], None),
    (r"(?<![\w.])XmbConfirm\s*\(", [0, 1, 2], None),
    (r"(?<![\w.])SettingDef\s*\(", [1, 2], None),
    (r"(?<![\w.])GlossaryEntry\s*\(", [0, 1], None),
    (r"(?<![\w.])SectionHeader\s*\(", [0], None),
    (r"(?<![\w.])StoreSectionHeader\s*\(", [0, 1], None),
    (r"(?<![\w.])LabeledSlider\s*\(", [0], None),
    (r"(?<![\w.])ToggleRow\s*\(", [0], None),
    (r"\bResult\.Error\s*\(", [0], "need_space"),
    (r"\bAppUtils\.showToast\s*\(", [0], None),
    (r"\bshowHelpBox\s*\(", [0], None),
    (r"\bshowAgentFailure\s*\(", [0, 1, 2], None),
    (r"\bToast\.makeText\s*\(", [0], None),
    (r"(?<!\w)showToast\s*\(", [0], None),
]
CALL_RES = [(re.compile(rx), idx, flt) for rx, idx, flt in CALLS]

XML_ATTR_RE = re.compile(
    r"android:(text|title|hint|contentDescription|summary|description|"
    r'textOn|textOff|prompt)\s*=\s*"([^"\\]|\\.)*"'
)

TEMPLATE_RE = re.compile(r"\$\{[^}]*\}|\$[A-Za-z_]\w*")
ESCAPE_RE = re.compile(r"\\u[0-9a-fA-F]{4}|\\[ntr\"\'$\\]")


def is_translatable(raw, flt=None):
    if "\n" in raw or "\r" in raw:
        return False
    t = ESCAPE_RE.sub("", TEMPLATE_RE.sub("", raw))
    if not re.search(r"[A-Za-z]{2,}", t):
        return False
    if flt == "need_space" and " " not in t:
        return False
    return True


def leaf_key(pieces):
    return "".join(r for _, r in pieces)


def call_args(s, open_paren, kotlin=True):
    """Top-level argument spans of the call whose '(' is at s[open_paren]."""
    args = []
    depth = 1
    i = open_paren + 1
    arg_start = i
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            r = lex_string(s, i, kotlin)
            if r is None:
                return None
            i = r[1]
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                if s[arg_start:i].strip():
                    args.append((arg_start, i))
                return args
        elif c == "," and depth == 1:
            args.append((arg_start, i))
            arg_start = i + 1
        i += 1
    return None


def exclusion_spans(s, kotlin=True):
    """Spans of string literals and comments - triggers inside them are ignored."""
    spans = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            r = lex_string(s, i, kotlin)
            if r is None:
                break
            spans.append((r[0], r[1]))
            i = r[1]
            continue
        if c == "/" and s.startswith("//", i):
            j = s.find("\n", i)
            j = n if j < 0 else j
            spans.append((i, j))
            i = j
            continue
        if s.startswith("/*", i):
            j = s.find("*/", i + 2)
            j = n if j < 0 else j + 2
            spans.append((i, j))
            i = j
            continue
        i += 1
    return spans


def in_spans(pos, spans):
    for a, b in spans:
        if a <= pos < b:
            return True
        if pos < a:
            return False
    return False


def scan_code(s, kotlin=True, spans=None, keys=None, hits=None):
    """Walk one code file. spans+hits -> rewrite; keys -> collect literals."""
    excl = exclusion_spans(s, kotlin)

    def process_leaf(pieces, flt, tag):
        raw = leaf_key(pieces)
        if not is_translatable(raw, flt):
            return
        if keys is not None:
            e = keys.setdefault(raw, [0, tag])
            e[0] += 1
        if spans is not None and hits is not None:
            val = spans.get(raw)
            if val:
                r = lex_string(s, pieces[0][0], kotlin)
                if r is None:
                    return
                hits.append((r[0], r[1], '"' + val + '"'))
                for ps, _ in pieces[1:]:
                    r2 = lex_string(s, ps, kotlin)
                    if r2 is None:
                        return
                    hits.append((r2[0], r2[1], '""'))

    def process_expr(expr, flt, tag):
        for kind, pieces in expr["leaves"]:
            process_leaf(pieces, flt, tag)

    for m in NAMED_PARAM_RE.finditer(s):
        if in_spans(m.start(), excl):
            continue
        expr = classify_expr(s, m.end(), kotlin)
        if expr:
            process_expr(expr, NAMED_PARAMS[m.group(1)], "param:" + m.group(1))

    for cre, flt in NAMED_EXTRA:
        for m in cre.finditer(s):
            if in_spans(m.start(), excl):
                continue
            expr = classify_expr(s, m.end(), kotlin)
            if expr:
                process_expr(expr, flt, "extra")

    for cre, idxs, flt in CALL_RES:
        for m in cre.finditer(s):
            if in_spans(m.start(), excl):
                continue
            args = call_args(s, m.end() - 1, kotlin)
            if args is None:
                continue
            lit_no = 0
            for a, b in args:
                expr = classify_expr(s, a, kotlin)
                if not expr:
                    continue
                if lit_no not in idxs:
                    lit_no += 1
                    continue
                process_expr(
                    expr, flt, "call:" + m.group(0).split("(")[0].split(".")[-1]
                )
                lit_no += 1
    return None


def scan_xml(s, spans=None, keys=None, hits=None):
    for m in XML_ATTR_RE.finditer(s):
        vs = m.group(0)
        q = vs.find('"')
        raw = vs[q + 1 : -1]
        if raw.startswith("@") or raw.startswith("tools:"):
            continue
        if not is_translatable(raw):
            continue
        if keys is not None:
            e = keys.setdefault(raw, [0, "xml"])
            e[0] += 1
        if spans is not None and hits is not None:
            v = spans.get(raw)
            if v:
                hits.append((m.start() + q + 1, m.end() - 1, v))


def apply_hits(s, hits):
    seen = set()
    for a, b, rep in sorted(hits, key=lambda h: -h[0]):
        if (a, b) in seen:
            continue
        seen.add((a, b))
        s = s[:a] + rep + s[b:]
    return s


def load_map():
    if not MAP.exists():
        sys.exit("zh-map.json not found at %s" % MAP)
    with open(MAP, encoding="utf-8") as f:
        return json.load(f)


def scan_all(spans=None, keys=None):
    hits = []
    for f in KT_FILES:
        s = f.read_text(encoding="utf-8")
        scan_code(s, kotlin=True, spans=spans, keys=keys)
        if spans is not None:
            h = []
            scan_code(s, kotlin=True, spans=spans, hits=h)
            if h:
                s2 = apply_hits(s, h)
                if s2 != s:
                    f.write_text(s2, encoding="utf-8")
    for f in JAVA_FILES:
        s = f.read_text(encoding="utf-8")
        scan_code(s, kotlin=False, spans=spans, keys=keys)
        if spans is not None:
            h = []
            scan_code(s, kotlin=False, spans=spans, hits=h)
            if h:
                s2 = apply_hits(s, h)
                if s2 != s:
                    f.write_text(s2, encoding="utf-8")
    for f in XML_FILES:
        s = f.read_text(encoding="utf-8")
        scan_xml(s, spans=spans, keys=keys)
        if spans is not None:
            h = []
            scan_xml(s, spans=spans, hits=h)
            if h:
                s2 = apply_hits(s, h)
                if s2 != s:
                    f.write_text(s2, encoding="utf-8")


def cmd_extract(out_path):
    keys = {}
    scan_all(keys=keys)
    lines = []
    meta = []
    for i, k in enumerate(sorted(keys), 1):
        lines.append("%d\t%s" % (i, k.replace("\t", "\\t")))
        meta.append("%d\t%s\t%d" % (i, keys[k][1], keys[k][0]))
    text = "\n".join(lines) + "\n"
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8")
        Path(str(out_path) + ".meta").write_text(
            "\n".join(meta) + "\n", encoding="utf-8"
        )
    else:
        sys.stdout.write(text)
    total = sum(v[0] for v in keys.values())
    print(
        "sources: %d kt, %d java, %d xml"
        % (len(KT_FILES), len(JAVA_FILES), len(XML_FILES)),
        file=sys.stderr,
    )
    print(
        "distinct literals: %d, total occurrences: %d" % (len(keys), total),
        file=sys.stderr,
    )


def cmd_check():
    spans = load_map()
    keys = {}
    scan_all(keys=keys)
    missing = sorted(k for k in keys if k not in spans)
    stale = sorted(k for k in spans if k not in keys)
    for k in missing:
        print("MISSING %s  (%d uses, first: %s)" % (k, keys[k][0], keys[k][1]))
    for k in stale:
        print("stale (not found in sources): %s" % k)
    print(
        "check: %d missing, %d stale, %d mapped"
        % (len(missing), len(stale), len(keys) - len(missing)),
        file=sys.stderr,
    )
    sys.exit(1 if missing else 0)


def cmd_rewrite():
    spans = load_map()
    before = {
        f: f.read_text(encoding="utf-8") for f in KT_FILES + JAVA_FILES + XML_FILES
    }
    scan_all(spans=spans)
    changed = 0
    hits_count = 0
    for f in before:
        s2 = f.read_text(encoding="utf-8")
        if s2 != before[f]:
            changed += 1
    print("zh-rewrite: %d files rewritten" % changed)


def main():
    args = sys.argv[1:]
    if args and args[0] == "--extract":
        out = args[args.index("--out") + 1] if "--out" in args else None
        cmd_extract(out)
    elif args and args[0] == "--check":
        cmd_check()
    else:
        cmd_rewrite()


if __name__ == "__main__":
    main()
