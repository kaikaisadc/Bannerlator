#!/usr/bin/env python3
"""Report drift between upstream values/strings.xml and the zh-CN translation.

After merging upstream into this branch, run:

    python3 scripts/zh-sync.py

It lists keys that upstream added (with no translation yet; Android shows the
English value until one is added) and keys that upstream removed (stale
translations, safe to delete). Exit code is non-zero when there is drift, so it
can also be wired into CI.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "app/src/main/res/values/strings.xml"
ZH = ROOT / "app/src/main/res/values-zh-rCN/strings.xml"


def collect(path):
    raw = path.read_text(encoding="utf-8")
    keys = {}
    for m in re.finditer(r"<string(?![-\w])([^>]*)>(.*?)</string>", raw, re.S):
        attrs, inner = m.group(1), m.group(2)
        if 'translatable="false"' in attrs:
            continue
        nm = re.search(r'name="([^"]+)"', attrs)
        if nm:
            keys[nm.group(1)] = inner
    for m in re.finditer(r"<plurals(?![-\w])([^>]*)>", raw):
        nm = re.search(r'name="([^"]+)"', m.group(1))
        if nm:
            keys[nm.group(1)] = "<plurals>"
    for m in re.finditer(r"<string-array(?![-\w])([^>]*)>", raw):
        nm = re.search(r'name="([^"]+)"', m.group(1))
        if nm:
            keys[nm.group(1)] = "<string-array>"
    return keys


def main():
    if not SRC.exists() or not ZH.exists():
        print(f"missing {SRC if not SRC.exists() else ZH}", file=sys.stderr)
        return 2
    src, zh = collect(SRC), collect(ZH)
    missing = sorted(set(src) - set(zh))
    stale = sorted(set(zh) - set(src))
    print(f"upstream: {len(src)} keys | zh-CN: {len(zh)} keys")
    print(
        f"\nmissing translations ({len(missing)}) - add these to values-zh-rCN/strings.xml:"
    )
    for k in missing:
        print(f"  - {k}")
    print(
        f"\nstale translations ({len(stale)}) - no longer in upstream, safe to delete:"
    )
    for k in stale:
        print(f"  - {k}")
    if missing or stale:
        print(
            "\nNew keys fall back to English automatically, so nothing breaks; "
            "translate them when convenient."
        )
        return 1
    print("\ntranslation is in sync with upstream.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
