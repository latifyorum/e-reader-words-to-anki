#!/usr/bin/env python3
"""
Import "word: meaning" lines into Anki as flashcards.

Workflow:
  1) export_words.py  -> <output>/2026-11-03/words.txt
  2) Translate words.txt (LLM, DeepL, ...) and save the result next to it as
     words-translated.md, one "word: meaning" per line
  3) This script adds the cards to Anki

Anki is reached through the AnkiConnect add-on (Anki must be running).
  Install: Anki > Tools > Add-ons > Get Add-ons > code 2055492159 > restart Anki.
If Anki cannot be reached, an anki_import.txt (tab-separated) is written instead, which
you can load with Anki's File > Import.

Running it again never creates duplicates (Anki's duplicate check is used).
A small report, anki_import.json, is written into each export folder after an import.

Usage:
    python anki_import.py                          # newest export folder
    python anki_import.py path/to/words-translated.md
    python anki_import.py --all                    # every export folder
    python anki_import.py --deck "English"
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ANKI_URL = "http://127.0.0.1:8765"
NOTE_TYPE = "E-Reader Vocabulary"
TRANSLATED_NAMES = ("words-translated.md", "words_translated.txt", "words_tr.txt")
CSS = ".card { font-family: arial; font-size: 24px; text-align: center; }"
DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}(_\d{6})?$")
LIST_MARK = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+")


# ------------------------------------------------------------------ AnkiConnect
def anki(action, **params):
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode("utf-8")
    req = urllib.request.Request(ANKI_URL, payload, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data["result"]


def ensure_note_type():
    if NOTE_TYPE not in anki("modelNames"):
        anki("createModel",
             modelName=NOTE_TYPE,
             inOrderFields=["Word", "Meaning"],
             css=CSS,
             cardTemplates=[{
                 "Name": "Word -> Meaning",
                 "Front": "{{Word}}",
                 "Back": "{{FrontSide}}<hr id=answer>{{Meaning}}",
             }])


def push(cards, deck, tag):
    anki("createDeck", deck=deck)
    ensure_note_type()
    notes = [{
        "deckName": deck,
        "modelName": NOTE_TYPE,
        "fields": {"Word": w, "Meaning": m},
        "options": {"allowDuplicate": False, "duplicateScope": "deck"},
        "tags": ["ereader", tag],
    } for w, m in cards]
    res = anki("addNotes", notes=notes)
    added = sum(1 for r in res if r)
    return added, len(res) - added


# ---------------------------------------------------------------- file handling
def parse(path):
    cards, skipped = [], []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = LIST_MARK.sub("", raw.strip())
        if not line:
            continue
        word, sep, meaning = line.partition(":")
        word, meaning = word.strip().strip("*` ").strip(), meaning.strip()
        if not sep or not word or not meaning:
            skipped.append(raw.strip())
            continue
        cards.append((word, meaning))
    return cards, skipped


def write_tsv(cards, folder, deck):
    out = folder / "anki_import.txt"
    lines = ["#separator:tab", "#html:false", f"#deck:{deck}"]
    lines += [f"{w}\t{m}" for w, m in cards]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def write_status(folder, deck, method, **info):
    """Leave a small report in the export folder so you can see what was imported."""
    status = {"deck": deck, "method": method,
              "timestamp": datetime.now().isoformat(timespec="seconds"), **info}
    (folder / "anki_import.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def targets(args):
    """Return a list of (file, tag)."""
    if args.file:
        p = Path(args.file).expanduser()
        return [(p, "ereader-" + p.parent.name)]
    out_dir = Path(args.out).expanduser()
    folders = sorted(d for d in out_dir.iterdir() if d.is_dir() and DATE_DIR.match(d.name)) \
        if out_dir.is_dir() else []
    if not folders:
        sys.exit(f"No export folders found in: {out_dir}")
    if not args.all:
        folders = folders[-1:]
    found = []
    for d in folders:
        for name in TRANSLATED_NAMES:
            if (d / name).is_file():
                found.append((d / name, "ereader-" + d.name))
                break
    if not found:
        sys.exit(f"{TRANSLATED_NAMES[0]} not found. Translate words.txt first and save the "
                 f"result as '{TRANSLATED_NAMES[0]}' in the same folder.")
    return found


def main():
    ap = argparse.ArgumentParser(description='Import "word: meaning" lines into Anki.')
    ap.add_argument("file", nargs="?", help='File with "word: meaning" lines')
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent),
                    help="Export root folder (default: the folder containing this script)")
    ap.add_argument("--deck", default="E-Reader Vocabulary", help="Anki deck name")
    ap.add_argument("--all", action="store_true", help="Import every export folder")
    args = ap.parse_args()

    anki_ok = True
    for path, tag in targets(args):
        cards, skipped = parse(path)
        print(f"\n{path}: {len(cards)} cards read")
        for s in skipped:
            print(f"  skipped (missing meaning / bad format): {s}")
        if not cards:
            continue

        if anki_ok:
            try:
                added, dup = push(cards, args.deck, tag)
                print(f"  Added to Anki: {added}, already present: {dup}")
                write_status(path.parent, args.deck, "ankiconnect", cards=len(cards),
                             added=added, already_present=dup, skipped_lines=len(skipped))
                continue
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                anki_ok = False
                print("  Could not reach Anki (is it running with AnkiConnect installed?).")
            except RuntimeError as e:
                anki_ok = False
                print(f"  AnkiConnect error: {e}")

        tsv = write_tsv(cards, path.parent, args.deck)
        write_status(path.parent, args.deck, "tsv_written", cards=len(cards),
                     file=tsv.name, skipped_lines=len(skipped))
        print(f"  Fallback file written: {tsv}\n"
              f"  Load it in Anki via File > Import (choose the Basic note type).")


if __name__ == "__main__":
    main()
