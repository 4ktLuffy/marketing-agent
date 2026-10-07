# Local add-ons (example)

Everything specific to one market or one business lives in a folder next to the stack, **outside the
code**: `01-marketing-stack/local/` by default (`LOCAL_ADDONS_DIR` to move it). It is in `.gitignore`,
so nothing in it is ever committed. Copy this folder to start: `cp -R local.example local`.

All files are optional. An empty folder means the agent uses only its built-in, market-neutral words
(£, $, €, English units).

| File | Read by | What it adds |
|---|---|---|
| `words.yaml` | task bridge (88) | your currency words, towns, and local ways to say a disclosure |
| `occasions.py` | task bridge (88) | a holiday / local-calendar plugin for packs and `GET /occasions` |
| `starter-kits/*.yaml` | brand service (05) | your own rule kits, offered next to the built-in ones |

## words.yaml

See `words.example.yaml`. Currency words in another script (Greek, Arabic, Ethiopic, …) are read only
when that script is in the text.

## occasions.py

A plain Python module with:

```python
NAME = "my-calendar"                  # shown by GET /occasions
NOTE = "One line for marketers."      # optional
PACK_NOTES = ["Write times in 24-hour format."]   # optional lines added to every task pack

def format_date(day):                 # optional: the date in your local calendar
    return "..."

def occasions_near(day, days=21, past_days=0):
    # dicts: name, date, end (datetime.date), local_date, kind, verified, notes, source
    return []
```

## starter-kits/

Same format as `05-brand-service/config/starter-kits/*.yaml`; the file name is the kit id.
