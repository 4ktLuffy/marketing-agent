"""Access-log redaction: a client link's token is in its path (/c/<token>), so uvicorn's access
log would keep a working link. The filter rewrites it to /c/*** before anything is written.
Media paths (/c/m/<kind>/<name>) carry no token and are left as they are."""
import logging
import re

_TOKEN_PATH = re.compile(r"^/c/(?!m/)[^/?#]+")


class RedactClientTokens(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = args[:2] + (_TOKEN_PATH.sub("/c/***", args[2]),) + args[3:]
        return True


def install() -> None:
    log = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactClientTokens) for f in log.filters):
        log.addFilter(RedactClientTokens())
