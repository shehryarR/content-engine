#!/usr/bin/env python3
"""
scripts/classify_contract_drift.py

M4 Day 1 (Owner A / Ammar) — baseline drift classifier.

`git diff --exit-code m0-contract-freeze -- contracts/ graph/` answers
"is there drift" but not "does the drift break v1". M0's rule is that
after the tag v1 is read-only and *breaking* changes require v2, so a
non-zero diff is only fatal if it actually broke something.

This script classifies every JSON Schema delta between a baseline ref and
the working tree into:

  BREAKING     - field removed, field made required, type narrowed,
                 enum value removed. These violate the M0 freeze.
  ADDITIVE     - new optional field, new schema file, description change.
                 v1 semantics preserved.

Exit code 0 iff there are zero BREAKING deltas.

Usage:
    python scripts/classify_contract_drift.py m0-contract-freeze
    python scripts/classify_contract_drift.py m0-contract-freeze --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def _existed_at(ref: str, path: str) -> bool:
    """True if `path` was tracked at `ref`. Separate from parsing, so a
    .py source that exists but isn't JSON isn't mislabelled as new."""
    return subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{path}"],
        capture_output=True,
    ).returncode == 0


def _schema_at(ref: str, path: str) -> dict | None:
    """Return the parsed schema at `ref`, or None if absent/unparseable."""
    try:
        blob = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            capture_output=True, text=True, check=True,
        ).stdout
    except subprocess.CalledProcessError:
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


def _schema_now(path: str) -> dict | None:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _type_set(spec: dict) -> set[str]:
    """Normalise a property spec to the set of JSON types it accepts.

    Handles the anyOf[{type: X}, {type: null}] shape Pydantic emits for
    Optional[X], so `str` -> `Optional[str]` reads as a widening, not a
    type change.
    """
    if "anyOf" in spec:
        out: set[str] = set()
        for branch in spec["anyOf"]:
            out |= _type_set(branch)
        return out
    t = spec.get("type")
    if t is None:
        return set()
    return {t} if isinstance(t, str) else set(t)


def classify(old: dict, new: dict, path: str) -> list[tuple[str, str]]:
    """Return a list of (severity, message) deltas between two schemas."""
    deltas: list[tuple[str, str]] = []

    old_props = old.get("properties", {})
    new_props = new.get("properties", {})
    old_req = set(old.get("required", []))
    new_req = set(new.get("required", []))

    # 1. Removed fields — breaking: existing documents lose a guarantee.
    for name in sorted(set(old_props) - set(new_props)):
        deltas.append(("BREAKING", f"{path}: field removed -> {name}"))

    # 2. Newly required fields — breaking: old documents stop validating.
    for name in sorted(new_req - old_req):
        deltas.append(("BREAKING", f"{path}: field became required -> {name}"))

    # 3. Relaxed requirements — additive.
    for name in sorted(old_req - new_req):
        deltas.append(("ADDITIVE", f"{path}: field no longer required -> {name}"))

    # 4. Added fields — additive only if optional.
    for name in sorted(set(new_props) - set(old_props)):
        sev = "BREAKING" if name in new_req else "ADDITIVE"
        suffix = " (REQUIRED)" if name in new_req else " (optional)"
        deltas.append((sev, f"{path}: field added -> {name}{suffix}"))

    # 5. Type / enum changes on surviving fields.
    for name in sorted(set(old_props) & set(new_props)):
        o, n = old_props[name], new_props[name]

        o_types, n_types = _type_set(o), _type_set(n)
        if o_types and n_types and o_types != n_types:
            if o_types - n_types:
                deltas.append((
                    "BREAKING",
                    f"{path}: type narrowed on {name} -> "
                    f"{sorted(o_types)} became {sorted(n_types)}",
                ))
            else:
                deltas.append((
                    "ADDITIVE",
                    f"{path}: type widened on {name} -> "
                    f"{sorted(o_types)} became {sorted(n_types)}",
                ))

        o_enum, n_enum = set(o.get("enum", [])), set(n.get("enum", []))
        if o_enum - n_enum:
            deltas.append((
                "BREAKING",
                f"{path}: enum values removed on {name} -> "
                f"{sorted(o_enum - n_enum)}",
            ))
        if n_enum - o_enum:
            deltas.append((
                "ADDITIVE",
                f"{path}: enum values added on {name} -> "
                f"{sorted(n_enum - o_enum)}",
            ))

    return deltas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("baseline", help="git ref to classify against, e.g. m0-contract-freeze")
    ap.add_argument("--paths", nargs="*", default=["contracts/", "graph/"])
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    changed = [
        p for p in _git(
            "diff", "--name-only", args.baseline, "--", *args.paths
        ).splitlines() if p.strip()
    ]

    all_deltas: list[tuple[str, str]] = []
    new_files: list[str] = []
    non_schema: list[str] = []

    for path in changed:
        existed = _existed_at(args.baseline, path)

        if not path.endswith(".json"):
            # .py sources generate the schemas; the schema comparison is the
            # authoritative check, so record the source file but don't
            # double-count it as a delta.
            non_schema.append(path if existed else f"{path} (new)")
            continue

        new = _schema_now(path)
        if not existed:
            new_files.append(path)
            continue

        old = _schema_at(args.baseline, path)
        if old is None:
            all_deltas.append(("BREAKING", f"{path}: baseline schema unparseable"))
            continue
        if new is None:
            all_deltas.append(("BREAKING", f"{path}: schema file deleted or unparseable"))
            continue

        all_deltas.extend(classify(old, new, path))

    breaking = [m for sev, m in all_deltas if sev == "BREAKING"]
    additive = [m for sev, m in all_deltas if sev == "ADDITIVE"]

    if args.as_json:
        print(json.dumps({
            "baseline": args.baseline,
            "changed_files": changed,
            "new_schema_files": new_files,
            "non_schema_files": non_schema,
            "breaking": breaking,
            "additive": additive,
            "verdict": "ADDITIVE_ONLY" if not breaking else "BREAKING",
        }, indent=2))
        return 0 if not breaking else 1

    print(f"Contract drift classification vs {args.baseline}")
    print("=" * 64)
    print(f"\nChanged paths under {', '.join(args.paths)}: {len(changed)}")

    if new_files:
        print(f"\nNEW SCHEMA FILES ({len(new_files)}) — additive by construction:")
        for f in new_files:
            print(f"  + {f}")

    if non_schema:
        print(f"\nNON-SCHEMA SOURCES ({len(non_schema)}) — schema diff is authoritative:")
        for f in non_schema:
            print(f"  . {f}")

    if additive:
        print(f"\nADDITIVE DELTAS ({len(additive)}):")
        for m in additive:
            print(f"  ~ {m}")

    if breaking:
        print(f"\nBREAKING DELTAS ({len(breaking)}):")
        for m in breaking:
            print(f"  ! {m}")
    else:
        print("\nBREAKING DELTAS (0): none")

    print("\n" + "=" * 64)
    if breaking:
        print("VERDICT: BREAKING — v1 semantics violated. M0 freeze broken.")
        return 1
    print("VERDICT: ADDITIVE_ONLY — no field removed, none made required,")
    print("         no type narrowed, no enum value removed. v1 readers of")
    print("         M0-era documents still validate. Freeze intact in substance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
