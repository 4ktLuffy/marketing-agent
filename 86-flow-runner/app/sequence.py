"""A flow version as one text for review, and back.

The calendar item (19, channel email_flow) holds the whole sequence in this format. A reviewer
may edit it in the control room; parse() reads the edited text back into steps. Exit conditions
are not part of the editable text: they stay as the version had them.

    Flow: welcome · version 2 · 3 emails
    Exit when: unsubscribed, purchased

    --- Email 1 of 3 · 0 hours after entry ---
    Subject: Welcome to Northwind

    Body in markdown ...
"""
import re

MARKER = re.compile(r"^---\s*Email\s+(\d+)\s+of\s+(\d+)\s*·\s*(\d+)\s+hours?\s+after\s+entry\s*---\s*$",
                    re.IGNORECASE)
SUBJECT = re.compile(r"^Subject:\s*(.+?)\s*$", re.IGNORECASE)


class SequenceError(ValueError):
    pass


def render(flow: str, version: int, steps: list[dict], exit_events: list[str]) -> str:
    n = len(steps)
    lines = [f"Flow: {flow} · version {version} · {n} email{'s' if n != 1 else ''}",
             f"Exit when: {', '.join(exit_events)}", ""]
    for i, s in enumerate(steps, 1):
        lines += [f"--- Email {i} of {n} · {s['delay_hours']} hours after entry ---",
                  f"Subject: {s['subject']}", "", s["body_markdown"].strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def parse(text: str) -> list[dict]:
    """Steps from a (possibly edited) sequence. Raises SequenceError with a reason a person
    can act on; never guesses."""
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[tuple[int, list[str]]] = []
    for line in lines:
        m = MARKER.match(line.strip())
        if m:
            blocks.append((int(m.group(3)), []))
        elif blocks:
            blocks[-1][1].append(line)
    if not blocks:
        raise SequenceError("no '--- Email N of M · H hours after entry ---' lines found")
    steps = []
    for i, (hours, body) in enumerate(blocks, 1):
        while body and not body[0].strip():
            body.pop(0)
        m = SUBJECT.match(body[0].strip()) if body else None
        if not m:
            raise SequenceError(f"email {i}: the first line after its marker must be 'Subject: ...'")
        text_body = "\n".join(body[1:]).strip()
        if not text_body:
            raise SequenceError(f"email {i}: the body is empty")
        steps.append({"delay_hours": hours, "subject": m.group(1), "body_markdown": text_body})
    return steps
