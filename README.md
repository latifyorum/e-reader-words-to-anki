# e-reader-words-to-anki

Export the words you looked up on your e-reader and turn them into Anki flashcards.

Works with **Kobo**, **Amazon Kindle** and, through **KOReader**, many other readers (PocketBook, Android-based readers such as Boox, and more). See [Supported e-readers](#supported-e-readers).

## Features

- **Finds your reader automatically** on Windows, macOS and Linux (with `--mount` and `--db` fallbacks).
- **Incremental exports:** words exported in earlier runs are never exported again, even if you look them up a second time or move to another reader.
- **Dated output folders:** every export goes into its own folder named after the export date (`2026-11-03/`).
- **Alphabetical, de-duplicated** `words.txt`, plus a `words.csv` with the date added, the book, the sentence the word came from (when the reader stores it) and the source reader.
- **Run summary:** new words, previously exported words, total exported, and words currently on the device.
- **Optional Anki import** of `word: meaning` lists through [AnkiConnect](https://ankiweb.net/shared/info/2055492159), with a tab-separated fallback file.
- **Safe for your device:** the script reads a temporary *copy* of the database and never writes to the reader.
- No third-party Python packages needed.

## Supported e-readers

| Reader | Where the words come from | Notes |
| --- | --- | --- |
| **Kobo** eReaders | `.kobo/KoboReader.sqlite` (the "My Words" list) | Tested on a Kobo Aura H2O Edition 2. Stores the word and the book, not the sentence. |
| **Amazon Kindle** | `system/vocabulary/vocab.db` (Vocabulary Builder) | Includes the sentence each word came from. Implemented from the documented database layout; not yet tested on a real Kindle. |
| **KOReader** (runs on Kobo, Kindle, PocketBook, Android-based readers such as Boox, and other devices it supports) | `koreader/settings/vocabulary_builder.sqlite3` | KOReader's Vocabulary Builder must hold your words. By default, KOReader does not add looked-up words automatically: tap *Add to vocabulary builder* in the lookup window or enable auto-add. Implemented from the documented layout; not yet tested on a real device. |

In short, compatible brands are **Kobo** and **Amazon Kindle** out of the box, and **PocketBook, Boox and any other device that can run KOReader** through KOReader. If a device has several of these (for example a Kobo that also runs KOReader), all of them are read and merged.

Readers that do not keep a readable word list in one of the files above are not supported. If your reader stores lookups in a SQLite file, please open an issue and describe the table layout.

Readers that connect over MTP instead of USB storage (many Android-based ones) do not show up as drives. Copy the database file to your computer and use `--db`:

```bash
python export_words.py --db path/to/vocabulary_builder.sqlite3
```

The type of database (Kobo, Kindle, KOReader) is detected from its tables.

## Requirements

- Python 3.8 or newer
- A supported e-reader connected by USB (on a Kobo, tap **Connect** on the device screen), or a database file copied from it
- For the Anki step only: Anki desktop with the AnkiConnect add-on

## Quick start

```bash
git clone https://github.com/latifyorum/e-reader-words-to-anki.git
cd e-reader-words-to-anki
python export_words.py
```

Connect your reader and run the script. You will get something like:

```
Found Kobo: E:\.kobo\KoboReader.sqlite
Output folder: C:\Users\you\e-reader-words-to-anki
First export: all words will be exported.
Exported 42 new words -> C:\Users\you\e-reader-words-to-anki\2026-11-03

--- Summary ---
New words in this export     : 42
Previously exported          : 0
Total exported words         : 42
Words on device (unique)     : 42

Press Enter to close...
```

### Output layout

```
2026-11-03/
  words.txt     one word per line, alphabetical
  words.csv     word, date_added, book, context, source
  words-translated.md   added by you or your translator: "word: meaning" lines
  anki_import.json      written by anki_import.py: import report
.ereader_export_state.json    remembers what has been exported (do not delete)
```

By default everything is written next to the script. Use `--out` to choose another folder.

In `words.csv`, `context` holds the sentence (Kindle) or the surrounding text (KOReader) when the reader saves it, and is empty otherwise (Kobo). `source` says which reader the word came from.

### Options

| Option | Description |
| --- | --- |
| `--out PATH` | Folder where dated export folders are written (default: the script's folder) |
| `--mount PATH` | Drive or mount path of the reader if it is not found automatically (e.g. `E:\`, `/Volumes/Kindle`) |
| `--db FILE` | Use a database file copied from the reader instead of scanning for a device |
| `--reset` | Forget previous exports and export all words again |
| `--no-pause` | Do not wait for Enter at the end (useful for scheduled or scripted runs) |

## How incremental export works

The script keeps a small state file (`.ereader_export_state.json`) in the output folder with the list of words already exported. On every run it reads all words on the device, skips the ones in that list, and exports the rest, so looking up an old word again does not create a duplicate. If there is nothing new, no folder is created. Earlier versions of the tool stored their state in `.kobo_export_state.json`; that file is still read if the new one does not exist.

## Turn the words into Anki cards

E-readers only store the words, not their meanings, so translation is a separate step.

1. Translate `words.txt` with the tool of your choice (an LLM, DeepL, a dictionary, ...). Ask for one `word: meaning` pair per line, for example:

   > Translate the following words into Turkish. Output one line per word in the format `word: meaning`. If a word has several meanings, give the 1-3 most common ones separated by commas. No numbering, bullets or extra commentary.

   Replace "Turkish" with your language. Giving the translator the `context` and `book` columns from `words.csv` helps it pick the right sense of ambiguous words.

2. Save the result in the same dated folder as `words-translated.md` (`words_translated.txt` and `words_tr.txt` are accepted too). Keep it to plain `word: meaning` lines, with no headings, tables or bullets:

   ```
   apple: elma
   bark: ağaç kabuğu, havlamak
   ```

3. Install AnkiConnect (one time): in Anki go to **Tools > Add-ons > Get Add-ons**, enter code `2055492159`, and restart Anki.

4. Keep Anki open and run:

   ```bash
   python anki_import.py
   ```

The script creates a deck called `E-Reader Vocabulary` and a note type with the fields `Word` and `Meaning` (front: word, back: meaning), tags each card with `ereader` and the export date, and reports how many cards were added and how many already existed. Running it again is safe because Anki's duplicate check is used. After each import a small `anki_import.json` report (deck, cards added, already present) is written into the dated folder.

| Option | Description |
| --- | --- |
| `FILE` | Import a specific `word: meaning` file instead of the newest export folder |
| `--all` | Import the translation file from every export folder |
| `--deck NAME` | Anki deck to use (default: `E-Reader Vocabulary`) |
| `--out PATH` | Export root folder (default: the script's folder) |

Lines without a meaning or in the wrong format are skipped and listed on screen. Leading list markers such as `- ` or `1. ` are ignored.

If Anki is not running or AnkiConnect is missing, the script writes `anki_import.txt` next to the translation file. Load it with **File > Import** in Anki and pick the *Basic* note type (requires Anki 2.1.54 or newer).

## Limitations

- A reader only records a word when you use its lookup feature (Kobo and Kindle: look the word up in the dictionary; KOReader: add it to the Vocabulary Builder). Highlighting alone does not add it. If the word list on the device is empty, the export will be empty too.
- Kobo does not record the sentence a word appeared in, only the word and the book, so translations of ambiguous words may not match the meaning in your book. Kindle and KOReader keep context, which `words.csv` includes.
- Kindle and KOReader words are exported in the form they were looked up, not reduced to a base form.
- Words from all languages on the device are exported together.
- The script must run on the computer the reader is plugged into. Remote servers, VMs, Docker containers and sandboxed agents cannot see USB devices. Under WSL, mount the drive manually (see the hint printed by the script).

## Troubleshooting

- **"No supported e-reader found"**: make sure the reader is connected in USB storage mode (on a Kobo, tap **Connect**) and appears in your file manager. If it does, pass its path with `--mount`. If it connects over MTP, copy the database file and use `--db`.
- **"No new words; nothing exported"**: everything on the device has already been exported, or the device's word list is empty.
- **Nothing appears after a run**: the output goes next to the script by default. The script prints the full output folder path at the start.

## Privacy

Your exports and the state file are personal data. The included `.gitignore` keeps them out of Git when they sit next to the scripts, but check before you push if you use a different output folder inside the repository.

If you want to back up your word lists in Git, use a **separate private repository** for the data and point the scripts at it:

```bash
python export_words.py --out /path/to/my-private-vocab-repo
python anki_import.py --out /path/to/my-private-vocab-repo
```

In that private repository, commit `.ereader_export_state.json` too: it is what lets you continue incremental exports from another machine.

## Disclaimer

This is an independent project. It is not affiliated with or endorsed by Rakuten Kobo Inc., Amazon, KOReader, or Anki. All product names are trademarks of their respective owners.

## License

[MIT](LICENSE) © 2026 Latif Yorum ([@latifyorum](https://github.com/latifyorum))
