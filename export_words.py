#!/usr/bin/env python3
"""
Export the words you looked up on an e-reader.

Supported sources (found automatically on a connected device):
  - Kobo           .kobo/KoboReader.sqlite                          ("My Words" list)
  - Amazon Kindle  system/vocabulary/vocab.db                       (Vocabulary Builder)
  - KOReader       <koreader>/settings/vocabulary_builder.sqlite3   (KOReader runs on many
                   devices: Kobo, Kindle, PocketBook, Android readers such as Boox, ...)

What it does:
- Finds the device automatically (Windows / macOS / Linux).
- Skips words exported in earlier runs; only new words are exported.
- Writes every export to a date-named folder:  <output>/2026-11-03/
    words.txt  -> one word per line (alphabetical)
    words.csv  -> word, date added, book, context sentence (if the reader stores it), source
- Prints new / total counts at the end and waits for Enter.

Usage:
    python export_words.py
    python export_words.py --out ~/ReaderWords
    python export_words.py --mount E:\\            (give the path manually if the device is not found)
    python export_words.py --db path/to/vocab.db  (use a database file you copied from the device)
    python export_words.py --reset                (forget previous exports, export everything again)
    python export_words.py --no-pause             (do not wait for Enter at the end)

Connect the e-reader with a USB cable first (on a Kobo, tap "Connect" on its screen).
"""

import argparse
import csv
import json
import os
import platform
import shutil
import sqlite3
import string
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = ".ereader_export_state.json"
LEGACY_STATE_FILE = ".kobo_export_state.json"  # name used by earlier versions

# (source type, path relative to the root of the device)
SOURCES = (
    ("kobo", ".kobo/KoboReader.sqlite"),
    ("kindle", "system/vocabulary/vocab.db"),
    ("koreader", ".adds/koreader/settings/vocabulary_builder.sqlite3"),
    ("koreader", "koreader/settings/vocabulary_builder.sqlite3"),
    ("koreader", "applications/koreader/settings/vocabulary_builder.sqlite3"),
    ("koreader", "settings/vocabulary_builder.sqlite3"),  # when pointing at KOReader's own folder
)
LABELS = {"kobo": "Kobo", "kindle": "Kindle", "koreader": "KOReader"}
FOLDER_HINTS = (".kobo", "system/vocabulary", ".adds/koreader", "koreader", "applications/koreader")


# ---------------------------------------------------------- device detection
def is_wsl():
    return "microsoft" in platform.uname().release.lower()


def candidate_mounts():
    system = platform.system()
    if system == "Windows":
        for letter in string.ascii_uppercase:
            if letter in "AB":
                continue
            root = f"{letter}:\\"
            if os.path.exists(root):
                yield Path(root)
    elif system == "Darwin":
        vol = Path("/Volumes")
        if vol.exists():
            yield from (p for p in vol.iterdir() if p.is_dir())
    else:  # Linux (under WSL, /mnt/<letter> is scanned as part of /mnt)
        user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
        for base in (Path("/media") / user, Path("/run/media") / user,
                     Path("/media"), Path("/mnt")):
            if base.exists():
                yield from (p for p in base.iterdir() if p.is_dir())


def find_sources(root):
    """Return [(source type, database path)] found under a device root."""
    found = []
    for kind, rel in SOURCES:
        p = root.joinpath(*rel.split("/"))
        try:
            if p.is_file():
                found.append((kind, p))
        except OSError:
            continue
    return found


def find_device(manual=None):
    """Return (root or None, sources, scanned locations)."""
    scanned = []
    roots = [Path(manual).expanduser()] if manual else candidate_mounts()
    for root in roots:
        scanned.append(root)
        sources = find_sources(root)
        if sources:
            return root, sources, scanned
    return None, [], scanned


def explain_not_found(scanned, manual):
    print("No supported e-reader found.")
    if scanned:
        print("Scanned locations: " + ", ".join(str(p) for p in scanned))
    else:
        print("No drives or locations to scan were found.")
    for p in scanned:
        for hint in FOLDER_HINTS:
            try:
                if p.joinpath(*hint.split("/")).is_dir():
                    print(f"  {p}: has a '{hint}' folder but no vocabulary database inside. "
                          "Has the reader recorded any looked-up words yet?")
                    break
            except OSError:
                pass
    print("\nThings to check:")
    print("  1) Is the reader connected in USB storage mode? (Kobo: tap 'Connect' on the "
          "screen. If it is only charging, the computer will not show it as a drive.)")
    print("  2) Is the cable a data cable? Does the reader show up as a drive in your file manager?")
    print("  3) Readers that connect over MTP (many Android-based ones) do not appear as drives. "
          "Copy the database file to your computer and run with --db <file>.")
    print("  4) The script must run on the computer the reader is plugged into. "
          "Remote servers, VMs, Docker and sandboxed agents cannot see USB devices.")
    if is_wsl():
        print("  5) WSL: mount the removable drive manually, e.g. "
              "'sudo mkdir -p /mnt/e && sudo mount -t drvfs E: /mnt/e', "
              "then run with --mount /mnt/e.")
    if not manual:
        print("  If you know the drive path, pass it with --mount <path> "
              "(e.g. E:\\ or /Volumes/KOBOeReader).")


# ------------------------------------------------------------------ reading
def iso_from_epoch(value):
    """Unix timestamp (seconds or milliseconds) -> ISO-8601 UTC string; '' if unusable."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    if v > 1e11:  # milliseconds
        v /= 1000.0
    return datetime.fromtimestamp(v, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def table_names(con):
    return {r[0].lower() for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def detect_kind(tables):
    if "wordlist" in tables:
        return "kobo"
    if {"words", "lookups"} <= tables:
        return "kindle"
    if "vocabulary" in tables:
        return "koreader"
    return None


def read_kobo(con, tables):
    book = ("COALESCE((SELECT c.Title FROM content c WHERE c.ContentID = w.VolumeId LIMIT 1), '')"
            if "content" in tables else "''")
    rows = con.execute(f"""
        SELECT w.Text, w.DateCreated, {book}
        FROM WordList w
        WHERE w.Text IS NOT NULL AND TRIM(w.Text) <> ''
        ORDER BY w.DateCreated
    """).fetchall()
    return [(t, d or "", b or "", "") for t, d, b in rows]


def read_kindle(con, tables):
    book_join = ("LEFT JOIN BOOK_INFO b ON (b.id = l.book_key OR b.guid = l.book_key)"
                 if "book_info" in tables else "")
    book = "COALESCE(b.title, '')" if book_join else "''"
    rows = con.execute(f"""
        SELECT w.word, COALESCE(l.timestamp, w.timestamp), {book}, COALESCE(l.usage, '')
        FROM WORDS w
        LEFT JOIN LOOKUPS l ON l.word_key = w.id
        {book_join}
        WHERE w.word IS NOT NULL AND TRIM(w.word) <> ''
        ORDER BY COALESCE(l.timestamp, w.timestamp)
    """).fetchall()
    return [(w, iso_from_epoch(ts), b or "", (u or "").strip()) for w, ts, b, u in rows]


def read_koreader(con, tables):
    cols = {r[1] for r in con.execute("PRAGMA table_info(vocabulary)")}
    prev = "v.prev_context" if "prev_context" in cols else "''"
    nxt = "v.next_context" if "next_context" in cols else "''"
    if "title" in tables and "title_id" in cols:
        join, book = "LEFT JOIN title t ON t.id = v.title_id", "COALESCE(t.name, '')"
    else:
        join, book = "", "''"
    rows = con.execute(f"""
        SELECT v.word, v.create_time, {book}, COALESCE({prev}, ''), COALESCE({nxt}, '')
        FROM vocabulary v
        {join}
        WHERE v.word IS NOT NULL AND TRIM(v.word) <> ''
        ORDER BY v.create_time
    """).fetchall()
    out = []
    for word, ts, book_name, before, after in rows:
        before, after = (before or "").strip(), (after or "").strip()
        context = " ".join(x for x in (before, word, after) if x) if (before or after) else ""
        out.append((word, iso_from_epoch(ts), book_name or "", context))
    return out


READERS = {"kobo": read_kobo, "kindle": read_kindle, "koreader": read_koreader}


def read_source(kind, path):
    """Read one database (a temporary copy; the device is never written to).
    kind may be None to detect it from the tables. Returns (kind, [(word, date, book, context)])."""
    tmpdir = Path(tempfile.mkdtemp(prefix="ereader_"))
    tmp_db = tmpdir / "copy.sqlite"
    try:
        shutil.copy2(path, tmp_db)
        for suffix in ("-wal", "-shm"):  # keep uncheckpointed data, if any
            side = Path(str(path) + suffix)
            if side.is_file():
                shutil.copy2(side, Path(str(tmp_db) + suffix))
        con = sqlite3.connect(str(tmp_db))
        try:
            tables = table_names(con)
            kind = kind or detect_kind(tables)
            if kind is None:
                raise ValueError("unrecognised database layout (no known vocabulary tables)")
            return kind, READERS[kind](con, tables)
        finally:
            con.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# -------------------------------------------------------------------- state
def load_state(out_dir):
    for name in (STATE_FILE, LEGACY_STATE_FILE):
        f = out_dir / name
        if f.is_file():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def save_state(out_dir, state):
    (out_dir / STATE_FILE).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def print_summary(new_count, prev_total, device_total):
    print("\n--- Summary ---")
    print(f"New words in this export     : {new_count}")
    print(f"Previously exported          : {prev_total}")
    print(f"Total exported words         : {prev_total + new_count}")
    print(f"Words on device (unique)     : {device_total}")


def pause(args):
    if args.no_pause or not sys.stdin or not sys.stdin.isatty():
        return
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass


# --------------------------------------------------------------------- main
def run(args):
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.db:
        db = Path(args.db).expanduser()
        if not db.is_file():
            print(f"Database file not found: {db}")
            return 1
        sources = [(None, db)]
    else:
        _, sources, scanned = find_device(args.mount)
        if not sources:
            explain_not_found(scanned, args.mount)
            if sys.stdin and sys.stdin.isatty():
                try:
                    typed = input("\nOptionally type the device's drive path or a database file "
                                  "(leave empty to quit): ").strip().strip('"')
                except EOFError:
                    typed = ""
                if typed:
                    p = Path(typed).expanduser()
                    sources = [(None, p)] if p.is_file() else find_sources(p)
                    if not sources:
                        print("No vocabulary database found at that path.")
            if not sources:
                return 1

    for kind, path in sources:
        print(f"Found {LABELS.get(kind, 'database')}: {path}")
    print(f"Output folder: {out_dir.resolve()}")

    entries, errors = [], 0
    for kind, path in sources:
        try:
            real_kind, rows = read_source(kind, path)
        except (sqlite3.Error, ValueError) as e:
            errors += 1
            print(f"Could not read {path}: {e}")
            continue
        entries += [row + (real_kind,) for row in rows]
    if errors and not entries:
        return 2
    entries.sort(key=lambda e: e[1])  # oldest first (ISO dates sort as text)

    state = {} if args.reset else load_state(out_dir)
    exported = set(state.get("exported_words", []))
    device_total = len({e[0].strip().casefold() for e in entries})
    print("Skipping words exported earlier." if exported
          else "First export: all words will be exported.")

    # Drop words exported before and duplicates within this run
    seen = set(exported)
    new_words = []
    position = {}
    for word, date, book, context, kind in entries:
        key = word.strip().casefold()
        if key not in seen:
            seen.add(key)
            position[key] = len(new_words)
            new_words.append([word.strip(), date, book, context, kind])
        elif key in position and context and not new_words[position[key]][3]:
            new_words[position[key]][3] = context  # keep a context sentence if any copy has one

    if not new_words:
        print("No new words; nothing exported (no folder or files created).")
        if device_total == 0:
            print("The word list on the device looks empty. Readers only record a word when you "
                  "look it up (Kobo, Kindle) or add it to the Vocabulary Builder (KOReader). "
                  "Check whether any words appear in the reader's own word list.")
        print_summary(0, len(exported), device_total)
        return 0

    # Sort alphabetically before writing (case-insensitive)
    new_words.sort(key=lambda r: r[0].casefold())

    now = datetime.now()
    folder = out_dir / now.strftime("%Y-%m-%d")
    if folder.exists():  # second export on the same day
        folder = out_dir / now.strftime("%Y-%m-%d_%H%M%S")
    folder.mkdir(parents=True)

    (folder / "words.txt").write_text(
        "\n".join(w[0] for w in new_words) + "\n", encoding="utf-8")
    with open(folder / "words.csv", "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f)
        wr.writerow(["word", "date_added", "book", "context", "source"])
        wr.writerows(new_words)

    state["exported_words"] = sorted(seen)
    state["last_export"] = now.isoformat(timespec="seconds")
    state.pop("last_word_date", None)  # no longer used
    save_state(out_dir, state)

    print(f"Exported {len(new_words)} new words -> {folder}")
    print_summary(len(new_words), len(exported), device_total)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Export looked-up words from an e-reader.")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent),
                    help="Folder where dated export folders are written "
                         "(default: the folder containing this script)")
    ap.add_argument("--mount", help="Drive/mount path of the reader if it is not found automatically")
    ap.add_argument("--db", help="Use this database file (copied from the device) instead of "
                                 "scanning for a device")
    ap.add_argument("--reset", action="store_true",
                    help="Forget previous exports and export all words again")
    ap.add_argument("--no-pause", action="store_true",
                    help="Do not wait for Enter at the end")
    args = ap.parse_args()

    try:  # avoid crashes on legacy consoles that cannot print some characters
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    code = 0
    try:
        code = run(args) or 0
    except KeyboardInterrupt:
        code = 130
    except Exception:
        traceback.print_exc()
        code = 3
    pause(args)
    sys.exit(code)


if __name__ == "__main__":
    main()
