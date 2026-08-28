#!/usr/bin/env python3
"""
scripts/graph_lint.py — M5 step 11: verify publish path.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from graph.pipeline_graph import STAGE_SEQUENCE

def lint_publish_path():
    stages = [s[0] for s in STAGE_SEQUENCE]
    findings = []

    if stages[-1] != "S100":
        findings.append(("ERROR", f"S100 must be last, got {stages[-1]}"))
    else:
        findings.append(("PASS", "S100 is the last stage"))

    if "G90" in stages and "S100" in stages:
        if stages.index("G90") != stages.index("S100") - 1:
            findings.append(("ERROR", "G90 must immediately precede S100"))
        else:
            findings.append(("PASS", "G90 immediately precedes S100"))
    else:
        findings.append(("ERROR", "G90 missing from STAGE_SEQUENCE"))

    if "G80" in stages:
        findings.append(("ERROR", "G80 must NOT be in STAGE_SEQUENCE — it's a signal wait"))
    else:
        findings.append(("PASS", "G80 is not in STAGE_SEQUENCE (signal wait)"))

    vendors = {"openai","elevenlabs","did","d-id","google","gemini","kokoro"}
    for sid, cap in STAGE_SEQUENCE:
        for v in vendors:
            if v in cap.lower():
                findings.append(("ERROR", f"{sid} capability '{cap}' has vendor name '{v}'"))
    if not any(s == "ERROR" for s, _ in findings):
        findings.append(("PASS", "No vendor names in capabilities"))

    if all(s in stages for s in ["S70","G90","S100"]):
        between = stages[stages.index("S70")+1:stages.index("S100")]
        if "G90" in between:
            findings.append(("PASS", "S70 → G90 → S100: one path through disclosure"))
        else:
            findings.append(("ERROR", "Path from S70 to S100 doesn't go through G90"))

    return findings

def lint_graph_directory():
    findings = []
    vendors = ["openai","elevenlabs","did","d-id","google","gemini","gpt-4","claude"]
    for f in (Path(__file__).resolve().parent.parent / "graph").glob("*.py"):
        if f.name.startswith("__"): continue
        content = f.read_text().lower()
        for v in vendors:
            if v in content:
                findings.append(("ERROR", f"graph/{f.name} has vendor name '{v}'"))
    if not any(s == "ERROR" for s, _ in findings):
        findings.append(("PASS", "No vendor names in graph/"))
    return findings

if __name__ == "__main__":
    print("M5 Graph Lint\n" + "="*60)
    findings = lint_publish_path() + lint_graph_directory()
    errors = 0
    for sev, msg in findings:
        icon = {"PASS":"✓","ERROR":"✗"}.get(sev,"?")
        print(f"  {icon} [{sev}] {msg}")
        if sev == "ERROR": errors += 1
    print(f"\nRESULT: {'FAIL' if errors else 'PASS'}")
    sys.exit(1 if errors else 0)