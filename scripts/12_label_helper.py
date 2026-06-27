#!/usr/bin/env python3
"""
Script 12: Interactive terminal labeling helper for human_validation_sample.csv.

Labels are saved incrementally after every entry — quitting mid-session loses
nothing. Resume by running again; already-labeled events are skipped by default.

Usage
─────
  python scripts/12_label_helper.py [--csv PATH] [--all]

  --csv PATH   CSV to label (default: data/processed/human_validation_sample.csv)
  --all        Re-show all events, including already-labeled ones (for review/correction)

Keybindings during labeling
────────────────────────────
  c   → COSMETIC
  l   → CLARIFICATION
  e   → SCHEMA_EXPANSION
  r   → SCHEMA_CONTRACTION
  d   → BEHAVIORAL_DRIFT
  b   → go back to previous event
  n   → skip this event for now (leave unlabeled)
  ?   → show category legend
  q   → quit and save
"""

import argparse
import csv
import difflib
import os
import shutil
import sys
import textwrap
from pathlib import Path

DEFAULT_CSV = Path("data/processed/human_validation_sample.csv")

LABELS = {
    "c": "COSMETIC",
    "l": "CLARIFICATION",
    "e": "SCHEMA_EXPANSION",
    "r": "SCHEMA_CONTRACTION",
    "d": "BEHAVIORAL_DRIFT",
}

# ── Legend ────────────────────────────────────────────────────────────────────

LEGEND = """\
┌─────────────────────────────────────────────────────────────────────────────┐
│  CLASSIFICATION LEGEND                                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│  [c] COSMETIC          wording/formatting only, meaning unchanged           │
│                                                                             │
│  [l] CLARIFICATION     explains EXISTING behavior more clearly;             │
│                        no new outcome possible                              │
│                                                                             │
│  [e] SCHEMA_EXPANSION  new input fields added, same core purpose            │
│                                                                             │
│  [r] SCHEMA_CONTRACTION  input fields removed                               │
│                                                                             │
│  [d] BEHAVIORAL_DRIFT  tool's actual scope/capability changed —             │
│                        a new kind of outcome is now possible that           │
│                        wasn't before                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│  TIE-BREAKER  CLARIFICATION vs BEHAVIORAL_DRIFT                            │
│  Ask: "Could the exact same tool-call, same parameters, have produced      │
│  this result BEFORE the change?"                                            │
│    Yes  →  CLARIFICATION                                                   │
│    No, something new is now possible  →  BEHAVIORAL_DRIFT                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  Keys: c l e r d = label   b = back   n = skip   ? = legend   q = quit    │
└─────────────────────────────────────────────────────────────────────────────┘"""


def print_legend() -> None:
    print(LEGEND)


# ── Display helpers ───────────────────────────────────────────────────────────

def _width() -> int:
    return min(shutil.get_terminal_size((80, 24)).columns, 100)


def _wrap(text: str, indent: int = 4) -> str:
    w = _width() - indent
    prefix = " " * indent
    if not text or not text.strip():
        return prefix + "(none)"
    lines = text.strip().splitlines()
    wrapped = []
    for line in lines:
        if len(line) <= w:
            wrapped.append(prefix + line)
        else:
            wrapped.extend(
                textwrap.fill(line, width=w, initial_indent=prefix,
                              subsequent_indent=prefix).splitlines()
            )
    return "\n".join(wrapped)


def _hr(char: str = "─") -> str:
    return char * _width()


def _desc_diff_lines(before: str, after: str) -> list[str]:
    """
    Return a list of display lines showing the full description with diff markers.

    Unchanged lines:  "    <text>"
    Removed lines:    "  - <text>"
    Added lines:      "  + <text>"

    For single-line descriptions, also appends a word-level diff summary so
    the exact words added/removed are immediately visible.
    """
    w = _width() - 4  # indent of 4
    indent_plain   = "    "
    indent_removed = "  - "
    indent_added   = "  + "

    def wrap_with_prefix(text: str, prefix: str) -> list[str]:
        if not text:
            return [prefix + "(empty)"]
        lines = []
        for physical_line in text.splitlines():
            if len(physical_line) <= w:
                lines.append(prefix + physical_line)
            else:
                chunks = textwrap.wrap(physical_line, width=w)
                lines.append(prefix + chunks[0])
                for chunk in chunks[1:]:
                    lines.append(" " * len(prefix) + chunk)
        return lines

    before_lines = before.splitlines()
    after_lines  = after.splitlines()

    output: list[str] = []

    # Use ndiff for a line-level view that interleaves context and changes
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, before_lines, after_lines, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            for line in before_lines[i1:i2]:
                output.extend(wrap_with_prefix(line, indent_plain))
        elif tag in ("replace", "delete"):
            for line in before_lines[i1:i2]:
                output.extend(wrap_with_prefix(line, indent_removed))
            if tag == "replace":
                for line in after_lines[j1:j2]:
                    output.extend(wrap_with_prefix(line, indent_added))
        elif tag == "insert":
            for line in after_lines[j1:j2]:
                output.extend(wrap_with_prefix(line, indent_added))

    # For single-line descriptions, append a word-level summary
    is_single_line = len(before_lines) <= 1 and len(after_lines) <= 1
    if is_single_line:
        before_words = before.split()
        after_words  = after.split()
        removed_words = []
        added_words   = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, before_words, after_words, autojunk=False
        ).get_opcodes():
            if tag in ("replace", "delete"):
                removed_words.extend(before_words[i1:i2])
            if tag in ("replace", "insert"):
                added_words.extend(after_words[j1:j2])
        if removed_words or added_words:
            output.append("")
            output.append("  ── word diff ──")
            if removed_words:
                output.append(f"  - {' '.join(removed_words)}")
            if added_words:
                output.append(f"  + {' '.join(added_words)}")

    return output


def display_event(row: dict, index: int, total: int, already_labeled: int) -> None:
    os.system("clear" if os.name == "posix" else "cls")
    w = _width()

    labeled_str = f"{already_labeled} labeled" if already_labeled else "none labeled yet"
    header = f"  Event {row['event_id']}  [{index + 1} of {total} remaining | {labeled_str}]"
    print(_hr("═"))
    print(header)
    print(_hr("═"))

    repo_short = row["repo_url"].replace("https://github.com/", "")
    print(f"  repo : {repo_short}")
    print(f"  tool : {row['tool_name']}")
    print(f"  type : {row['structural_change_type']}")
    print(f"  date : {row['from_date'][:10]} → {row['to_date'][:10]}")
    print(_hr())

    # Description
    bd = row["before_description"].strip()
    ad = row["after_description"].strip()
    same_desc = bd == ad

    print("  DESCRIPTION")
    print(_hr("·"))
    if same_desc:
        print("  [unchanged]")
        print(_wrap(bd))
    else:
        print("  [full text · unchanged lines indented 4, removed = '- ', added = '+ ']")
        for line in _desc_diff_lines(bd, ad):
            print(line)
    print()

    # Schema
    added   = row["schema_fields_added"].strip()
    removed = row["schema_fields_removed"].strip()
    types   = row["schema_type_changes"].strip()
    b_req   = row["before_required"].strip()
    a_req   = row["after_required"].strip()
    b_all   = row["before_all_fields"].strip()
    a_all   = row["after_all_fields"].strip()

    has_schema_change = added or removed or types or (b_req != a_req)

    if has_schema_change:
        print("  SCHEMA CHANGES")
        print(_hr("·"))
        if added:
            for f in added.split(";"):
                f = f.strip()
                if f:
                    print(f"  + {f}")
        if removed:
            for f in removed.split(";"):
                f = f.strip()
                if f:
                    print(f"  - {f}")
        if types:
            for t in types.split(";"):
                t = t.strip()
                if t:
                    print(f"  ~ {t}")
        if b_req != a_req:
            print(f"  required BEFORE: {b_req}")
            print(f"  required AFTER : {a_req}")
        print()

    # Full field lists (collapsed unless schema changed)
    if has_schema_change and (b_all or a_all):
        print("  ALL FIELDS")
        print(_hr("·"))
        print(f"  before: {_wrap(b_all, indent=10).lstrip()}")
        print(f"  after : {_wrap(a_all, indent=10).lstrip()}")
        print()

    # Current label if any
    existing = row.get("human_label", "").strip()
    if existing:
        print(f"  Current label: {existing}")
        print()

    print(_hr())
    print("  Label: [c]osmetic  [l]arification  [e]xpansion  [r]emoval  [d]rift  [?] legend  [b]ack  [n]skip  [q]uit")
    print(_hr())


# ── CSV I/O ───────────────────────────────────────────────────────────────────

def load_csv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def save_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(csv_path: Path, show_all: bool) -> None:
    if not csv_path.exists():
        sys.exit(f"ERROR: {csv_path} not found.\nRun: python scripts/11_human_validation.py")

    rows = load_csv(csv_path)
    # Map event_id → row index for easy mutation
    id_to_idx = {r["event_id"]: i for i, r in enumerate(rows)}

    # Build queue: unlabeled (or all if --all)
    queue = [r for r in rows
             if show_all or not r.get("human_label", "").strip()]

    if not queue:
        print("All events are already labeled. Use --all to review/edit them.")
        return

    already_labeled = sum(1 for r in rows if r.get("human_label", "").strip())

    # Show legend once at startup
    os.system("clear" if os.name == "posix" else "cls")
    print_legend()
    print()
    total_events = len(rows)
    print(f"  {len(queue)} events to label  ({already_labeled}/{total_events} already done)")
    print()
    input("  Press Enter to begin...")

    pos = 0  # position in queue

    while pos < len(queue):
        row = queue[pos]
        row_idx = id_to_idx[row["event_id"]]

        display_event(row, pos, len(queue), already_labeled)

        while True:
            try:
                raw = input("  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  Saving and quitting...")
                save_csv(csv_path, rows)
                return

            if raw == "q":
                print("  Saving and quitting...")
                save_csv(csv_path, rows)
                return

            if raw == "?":
                print()
                print_legend()
                print()
                continue

            if raw == "b":
                if pos == 0:
                    print("  (already at first event)")
                    continue
                pos -= 1
                break

            if raw == "n":
                # Skip: clear any existing label if re-reviewing
                pos += 1
                break

            if raw in LABELS:
                label = LABELS[raw]
                notes_prompt = "  Notes (optional, Enter to skip): "
                try:
                    notes = input(notes_prompt).strip()
                except (EOFError, KeyboardInterrupt):
                    notes = ""

                # Update the row in-place
                rows[row_idx]["human_label"] = label
                rows[row_idx]["human_notes"] = notes
                queue[pos]["human_label"] = label  # keep queue in sync for display
                already_labeled = sum(1 for r in rows if r.get("human_label", "").strip())

                save_csv(csv_path, rows)
                pos += 1
                break

            # Unrecognised input — show reminder
            print("  ? for legend  |  c=COSMETIC  l=CLARIFICATION  e=EXPANSION  r=CONTRACTION  d=DRIFT  b=back  n=skip  q=quit")

    # Finished queue
    os.system("clear" if os.name == "posix" else "cls")
    print_legend()
    print()
    labeled_now = sum(1 for r in rows if r.get("human_label", "").strip())
    print(f"  Session complete.  {labeled_now}/{total_events} events labeled.")
    remaining = total_events - labeled_now
    if remaining > 0:
        print(f"  {remaining} events still unlabeled — run again to continue.")
    else:
        print()
        print("  All 75 events labeled!")
        print("  Next: python scripts/11_human_validation.py --compute-kappa")
    print()
    save_csv(csv_path, rows)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Interactive terminal labeler for human_validation_sample.csv"
    )
    ap.add_argument("--csv", default=str(DEFAULT_CSV),
                    help=f"CSV to label (default: {DEFAULT_CSV})")
    ap.add_argument("--all", action="store_true",
                    help="Show all events including already-labeled ones")
    args = ap.parse_args()

    run(Path(args.csv), args.all)


if __name__ == "__main__":
    main()
