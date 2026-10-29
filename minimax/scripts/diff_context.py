#!/usr/bin/env python3
"""Compare two context packs and print the requirement entries that changed."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _key(entry: dict) -> str:
    return entry.get("requirement_id") or entry.get("title") or entry.get("citation", "")


def _describe(pack: dict) -> dict[str, dict]:
    return {_key(entry): entry for entry in pack.get("specs", []) if entry.get("requirement_id")}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: diff_context.py CONTEXT_A.json CONTEXT_B.json", file=sys.stderr)
        return 2
    a = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    b = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    specs_a, specs_b = _describe(a), _describe(b)

    added = sorted(set(specs_b) - set(specs_a))
    removed = sorted(set(specs_a) - set(specs_b))
    changed = sorted(
        key for key in set(specs_a) & set(specs_b)
        if specs_a[key].get("excerpt") != specs_b[key].get("excerpt")
    )

    print(f"context A: {argv[1]}")
    print(f"  specs={len(a.get('specs', []))} code={len(a.get('code', []))} tests={len(a.get('tests', []))} "
          f"sha256={a.get('context_sha256', '')[:16]}…")
    print(f"context B: {argv[2]}")
    print(f"  specs={len(b.get('specs', []))} code={len(b.get('code', []))} tests={len(b.get('tests', []))} "
          f"sha256={b.get('context_sha256', '')[:16]}…")
    print()
    print(f"requirement ids only in A : {removed or '-'}")
    print(f"requirement ids only in B : {added or '-'}")
    print(f"requirement ids changed   : {changed or '-'}")
    print()
    for key in added:
        entry = specs_b[key]
        print(f"+ {key}  {entry.get('citation')}  {entry.get('title', '')}")
    for key in removed:
        entry = specs_a[key]
        print(f"- {key}  {entry.get('citation')}  {entry.get('title', '')}")
    for key in changed:
        print(f"~ {key}  {specs_a[key].get('citation')} -> {specs_b[key].get('citation')}")

    same = a.get("context_sha256") == b.get("context_sha256")
    print()
    print("context changed:", "no (identical)" if same else "yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
