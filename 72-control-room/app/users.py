"""People who may log in, and their roles.

The users file (`CONTROL_USERS_FILE`, default `/data/users.json` when `/data` exists) is JSON:

    {"users": [{"name": "alex", "display": "Alex", "role": "owner", "pw_hash": "scrypt$<salt>$<hash>"}]}

Roles, highest first: owner > approver > writer. Everyone logged in reads every page; what a role
may change is decided per route in app/main.py (`need(role)`).

No file (or no path) = one virtual user from CONTROL_USER / CONTROL_PASSWORD with the role
**owner** and the display name CONTROL_REVIEWER (or CONTROL_USER), exactly as before users existed.
Once the file exists it alone decides who can log in. The file is read at every login, so a user
added with the CLI can log in without a restart. A file that exists but can't be read refuses all
logins (fail closed) rather than falling back to the environment's password.

CLI (inside the container: `docker compose exec control-room python -m app.users ...`):

    python -m app.users add NAME ROLE [--display "Full Name"] [--password-stdin]
    python -m app.users passwd NAME [--password-stdin]
    python -m app.users remove NAME
    python -m app.users list

Passwords are asked with getpass (never echoed) or read from stdin; the file is written
atomically with mode 600 and holds only scrypt hashes.
"""
import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

ROLES = ("writer", "approver", "owner")
RANK = {r: i for i, r in enumerate(ROLES, 1)}
NAME = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
N, R, P, SALT_BYTES = 2 ** 14, 8, 1, 16
MIN_PASSWORD = 8


class UsersFileError(Exception):
    """The users file exists but is not a users file we can trust. Message is safe to show."""


@dataclass(frozen=True)
class User:
    name: str
    display: str
    role: str

    def can(self, role: str) -> bool:
        return RANK.get(self.role, 0) >= RANK.get(role, 99)


# ------------------------------------------------------------------ hashing

def _scrypt(password: str, salt: bytes, dklen: int = 32) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=dklen, maxmem=64 * 1024 * 1024)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    return f"scrypt${salt.hex()}${_scrypt(password, salt).hex()}"


@lru_cache(maxsize=1)
def _dummy() -> str:
    """A hash no password matches in practice: compared for unknown names so they cost the same."""
    return hash_password(secrets.token_urlsafe(24))


def verify(password: str, stored: str | None) -> bool:
    """Always runs exactly one scrypt, also for an unknown user or a malformed hash."""
    ok_format = True
    try:
        algo, salt_hex, hash_hex = (stored or "").split("$")
        salt, want = bytes.fromhex(salt_hex), bytes.fromhex(hash_hex)
        if algo != "scrypt" or len(salt) < 8 or not 16 <= len(want) <= 64:
            raise ValueError
    except ValueError:
        ok_format = False
        _, salt_hex, hash_hex = _dummy().split("$")
        salt, want = bytes.fromhex(salt_hex), bytes.fromhex(hash_hex)
    got = _scrypt(password, salt, len(want))
    return hmac.compare_digest(got, want) and ok_format


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(hashlib.sha256(a.encode()).digest(), hashlib.sha256(b.encode()).digest())


# ------------------------------------------------------------------ the file

def default_path() -> str:
    """CONTROL_USERS_FILE if set (empty = no file); else /data/users.json when /data exists."""
    v = os.environ.get("CONTROL_USERS_FILE")
    if v is not None:
        return v.strip()
    return "/data/users.json" if os.path.isdir("/data") else ""


def clean_display(v: str) -> str:
    return " ".join("".join(ch for ch in str(v) if unicodedata.category(ch)[0] != "C").split())[:80]


def _check_row(row) -> dict:
    if not isinstance(row, dict):
        raise UsersFileError("a user is not an object")
    name, role, pw = row.get("name"), row.get("role"), row.get("pw_hash")
    if not isinstance(name, str) or not NAME.match(name):
        raise UsersFileError("a user has no valid name")
    if role not in RANK:
        raise UsersFileError(f"user {name}: role must be owner, approver or writer")
    if not isinstance(pw, str) or not pw.startswith("scrypt$"):
        raise UsersFileError(f"user {name}: pw_hash is not a scrypt hash")
    display = clean_display(row.get("display") or "") or name
    return {"name": name, "display": display, "role": role, "pw_hash": pw}


_cache: dict[str, tuple] = {}


def read_users(path: str) -> list[dict] | None:
    """The users in the file; None when there is no file (or no path). Re-read when it changes."""
    if not path:
        return None
    try:
        st = os.stat(path)
        stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        hit = _cache.get(path)
        if hit and hit[0] == stamp:
            return [dict(u) for u in hit[1]]
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        _cache.pop(path, None)
        return None
    except OSError as e:
        raise UsersFileError(f"can't read it ({type(e).__name__})") from None
    rows = _parse(raw)
    _cache[path] = (stamp, [dict(u) for u in rows])
    return rows


def _parse(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except ValueError:
        raise UsersFileError("it is not valid JSON") from None
    rows = data.get("users") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise UsersFileError('it has no "users" list')
    out = [_check_row(r) for r in rows]
    if len({u["name"] for u in out}) != len(out):
        raise UsersFileError("a name appears twice")
    return out


def write_users(path: str, users: list[dict]) -> None:
    """Atomic replace, mode 600."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".users-", suffix=".json", dir=d)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"users": users}, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ------------------------------------------------------------------ login

def state(settings) -> str:
    """"file" (users file decides), "env" (the single CONTROL_USER), "none" (nobody can log in),
    or "bad" (the file exists but can't be used)."""
    try:
        rows = read_users(settings.users_file)
    except UsersFileError:
        return "bad"
    if rows is not None:
        return "file" if rows else "none"
    return "env" if settings.password else "none"


def env_user(settings) -> User:
    return User(settings.user, settings.reviewer or settings.user, "owner")


def authenticate(settings, name: str, password: str) -> User | None:
    """The user, or None. Raises UsersFileError when the file exists but is unusable."""
    name = name.strip()
    rows = read_users(settings.users_file)
    if rows is None:
        ok_user = _same(name, settings.user)
        ok_pass = _same(password, settings.password)   # always evaluated: no early exit on the name
        return env_user(settings) if settings.password and ok_user and ok_pass else None
    match = None
    for r in rows:   # every row compared: no early exit on the name
        if _same(name, r["name"]):
            match = r
    ok = verify(password, match["pw_hash"] if match else None)   # one scrypt either way
    if match is None or not ok or not password:
        return None
    return User(match["name"], match["display"], match["role"])


def lookup(settings, name: str) -> User | None:
    """Who a session's user is now (role changes and removals apply at the next request).
    Raises UsersFileError when the file exists but is unusable."""
    rows = read_users(settings.users_file)
    if rows is None:
        return env_user(settings) if settings.password and name == settings.user else None
    for r in rows:
        if r["name"] == name:
            return User(r["name"], r["display"], r["role"])
    return None


# ------------------------------------------------------------------ CLI

def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        pw = sys.stdin.readline().rstrip("\r\n")
    else:
        pw = getpass.getpass("Password: ")
        if getpass.getpass("Again: ") != pw:
            raise SystemExit("The two passwords differ; nothing changed.")
    if len(pw) < MIN_PASSWORD:
        raise SystemExit(f"A password needs at least {MIN_PASSWORD} characters; nothing changed.")
    if len(pw) > 1000:
        raise SystemExit("A password is at most 1000 characters; nothing changed.")
    return pw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.users", description="People who may log in to the control room.")
    ap.add_argument("--file", default=None, help="users file (default: CONTROL_USERS_FILE or /data/users.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="add a person")
    a.add_argument("name")
    a.add_argument("role", choices=ROLES)
    a.add_argument("--display", default="", help='name shown on decisions, e.g. "Alex Morgan"')
    a.add_argument("--password-stdin", action="store_true")
    p = sub.add_parser("passwd", help="set a new password")
    p.add_argument("name")
    p.add_argument("--password-stdin", action="store_true")
    r = sub.add_parser("remove", help="remove a person")
    r.add_argument("name")
    sub.add_parser("list", help="list people and roles (no hashes)")
    args = ap.parse_args(argv)

    path = args.file if args.file is not None else default_path()
    if not path:
        raise SystemExit("No users file: set CONTROL_USERS_FILE (or mount /data).")
    try:
        users = read_users(path)
    except UsersFileError as e:
        raise SystemExit(f"{path}: {e}") from None
    new_file = users is None
    users = users or []
    by_name = {u["name"]: u for u in users}

    if args.cmd == "list":
        if not users:
            print("No users file yet: CONTROL_USER / CONTROL_PASSWORD is the only login (owner)." if new_file
                  else "The users file has nobody in it: nobody can log in.")
        for u in users:
            print(f"{u['name']}\t{u['role']}\t{u['display']}")
        return 0

    if not NAME.match(args.name):
        raise SystemExit("A name is 1-64 letters, digits, dots, dashes, underscores or @.")

    if args.cmd == "add":
        if args.name in by_name:
            raise SystemExit(f"{args.name} exists already; use passwd to change the password.")
        display = clean_display(args.display) or args.name
        pw = _read_password(args.password_stdin)
        users.append({"name": args.name, "display": display, "role": args.role, "pw_hash": hash_password(pw)})
        write_users(path, users)
        print(f"Added {args.name} ({args.role}).")
        if new_file:
            print("The users file now decides who can log in: CONTROL_USER / CONTROL_PASSWORD no longer work.",
                  file=sys.stderr)
            if args.role != "owner":
                print("Nobody is owner yet: add an owner too.", file=sys.stderr)
        return 0

    if args.name not in by_name:
        raise SystemExit(f"No user {args.name}.")

    if args.cmd == "passwd":
        pw = _read_password(args.password_stdin)
        by_name[args.name]["pw_hash"] = hash_password(pw)
        write_users(path, users)
        print(f"Password changed for {args.name}.")
        return 0

    # remove
    owners = [u for u in users if u["role"] == "owner"]
    if by_name[args.name]["role"] == "owner" and len(owners) == 1:
        raise SystemExit("That is the last owner; add another owner first.")
    write_users(path, [u for u in users if u["name"] != args.name])
    print(f"Removed {args.name}. They are logged out at their next click.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
