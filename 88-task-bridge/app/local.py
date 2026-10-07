"""Optional local add-ons for one market, kept out of the code (LOCAL_DIR, e.g. a mounted /local).

    LOCAL_DIR/words.yaml       currency words, towns and local wording (all optional):
        currency:          {word: ISO code}      words said next to an amount ("1,700 kora", "kora 1,700")
        currency_names:    {ISO code: word}      how an amount in that currency is written back ("1,700 kora")
        places:            [town, ...]           towns the checker knows ("our lodge in <town>")
        not_places:        [word, ...]           capitalised words never read as a town
        disclosure_words:  [{pattern: regex, means: "per crate"}]   local ways to say a disclosure
    LOCAL_DIR/occasions.py     a calendar plugin: NAME, NOTE, pack_lines(day), occasions(day, days, past_days),
                               format_date(day)

Nothing here is required: with no LOCAL_DIR the checker uses only its built-in, market-neutral words.
Read once per process; `reload()` re-reads (tests).
"""
import importlib.util
import os
import re
from functools import lru_cache
from pathlib import Path

import yaml


def local_dir() -> Path | None:
    d = os.environ.get("LOCAL_DIR", "").strip()
    return Path(d) if d and Path(d).is_dir() else None


@lru_cache(maxsize=1)
def words() -> dict:
    d = local_dir()
    f = d / "words.yaml" if d else None
    if not f or not f.is_file():
        return {}
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def currency() -> dict[str, str]:
    return {str(k).lower(): str(v).upper() for k, v in (words().get("currency") or {}).items()}


def currency_names() -> dict[str, str]:
    return {str(k).upper(): str(v) for k, v in (words().get("currency_names") or {}).items()}


def places() -> tuple[str, ...]:
    return tuple(str(p) for p in words().get("places") or [])


def not_places() -> frozenset:
    return frozenset(str(p).lower() for p in words().get("not_places") or [])


def disclosure_words() -> list[tuple[re.Pattern, str]]:
    out = []
    for item in words().get("disclosure_words") or []:
        if isinstance(item, dict) and item.get("pattern") and item.get("means"):
            out.append((re.compile(str(item["pattern"])), str(item["means"])))
    return out


@lru_cache(maxsize=1)
def occasions_plugin():
    """The local calendar plugin module, or None."""
    d = local_dir()
    f = d / "occasions.py" if d else None
    if not f or not f.is_file():
        return None
    spec = importlib.util.spec_from_file_location("local_occasions", f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def reload() -> None:
    words.cache_clear()
    occasions_plugin.cache_clear()
