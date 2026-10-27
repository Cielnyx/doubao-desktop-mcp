"""Parse the desktop client's flattened accessibility message panel.

Only a response with its own read-aloud toolbar is accepted as an assistant
message. Sidebar titles, recommended questions and unmarked text are excluded.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    role: str
    text: str


def parse_messages(entries: list[dict]) -> list[Message]:
    messages = []
    text = []
    buttons = []

    def flush():
        if text:
            role = "assistant" if any(b in ("朗读", "停止朗读") for b in buttons) else "user"
            messages.append(Message(role, "\n".join(text)))
        text.clear()
        buttons.clear()

    for entry in entries:
        kind, name = entry["type"], entry.get("name", "").strip()
        if kind == "Text":
            if buttons:
                flush()
            if name:
                text.append(name)
        elif kind == "Button":
            # Keep empty buttons: their existence separates adjacent messages.
            buttons.append(name)
        else:
            flush()
    flush()
    return messages

