"""Background-only conversation driver using the existing desktop renderer."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time

from .cdp import CDP, CDPError

PAGE = "\n".join(Path(__file__).with_name(name).read_text(encoding="utf-8")
                 for name in ("page.js", "video.js", "media.js", "tasks.js", "submission.js", "accounts.js"))


class BackgroundError(RuntimeError):
    def __init__(self, code, message, **details):
        self.code, self.details = code, details
        super().__init__(message)


def normalize_id(value):
    value = str(value).removeprefix("conversation_")
    if not re.fullmatch(r"\d{1,30}", value):
        raise BackgroundError("INVALID_ID", "对话 ID 必须为数字，或 conversation_数字。")
    return value


class BackgroundDoubao:
    def __init__(self):
        self.client = None
        self.sent = False

    def connect(self):
        if self.client is None:
            try:
                self.client = CDP().open()
            except (CDPError, OSError, TimeoutError) as error:
                raise BackgroundError("BACKGROUND_UNAVAILABLE", str(error)) from error

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None

    def page(self, action, **params):
        self.connect()
        expression = ("(async()=>{" + PAGE + "\ntry { return await doubao[" + json.dumps(action)
                      + "](" + json.dumps(params, ensure_ascii=True) + "); }"
                      "catch(e){return {ok:false,code:e.code||'PAGE_ERROR',message:e.message,details:e.details||{}};}})()")
        result = self.client.evaluate(expression, timeout=55 if action.startswith("video_") else 15)
        if isinstance(result, dict) and result.get("ok") is False:
            raise BackgroundError(result["code"], result["message"], **result.get("details", {}))
        return result

    def status(self):
        return self.page("status")

    def inspect(self):
        return self.page("inspect")

    def video_options(self):
        return self.page("video_options")

    def video_configure(self, model=None, ratio=None, duration=None):
        if model is not None and (not isinstance(model, str) or not model.strip() or len(model) > 100):
            raise BackgroundError("INVALID_VIDEO_MODEL", "model 应为查询工具返回的模型名称。")
        if ratio == "自动":
            ratio = "auto"
        if ratio is not None and (not isinstance(ratio, str) or not re.fullmatch(r"auto|[1-9]\d?:[1-9]\d?", ratio)):
            raise BackgroundError("INVALID_VIDEO_RATIO", "ratio 应为 auto 或 16:9 等比例字符串。")
        if duration is not None and (type(duration) is not int or not 1 <= duration <= 300):
            raise BackgroundError("INVALID_VIDEO_DURATION", "duration 应为 1–300 的整数秒数，实际范围以当前模型选项为准。")
        return self.page("video_configure", model=model, ratio=ratio, duration=duration)

    def video_exit(self):
        return self.page("video_exit")

    def _wait_ready(self, target_id=None, blank=False):
        deadline = time.monotonic() + 15
        stable = 0
        while time.monotonic() < deadline:
            try:
                status = self.status()
                snapshot = self.page("snapshot")
                ready = status["editor_available"] and (status["conversation_id"] == target_id)
                ready = ready and (not snapshot["messages"] if blank else bool(snapshot["messages"]))
                stable = stable + 1 if ready else 0
                if stable >= 2:
                    return {"ok": True, "conversation_id": target_id, "title": status["title"], "background": True}
            except CDPError:
                stable = 0  # A direct ID navigation may replace the document.
            time.sleep(0.3)
        raise BackgroundError("OPEN_TIMEOUT", "对话页面未在时限内就绪；请检查 ID、登录状态或网络。")

    def new_chat(self):
        self.page("new_chat")
        return self._wait_ready(blank=True)

    def list_conversations(self, query="", limit=50, offset=0, load_more=True, _exhaustive=False):
        if not 1 <= limit <= 200 or not 0 <= offset <= 5000 or len(query) > 200:
            raise BackgroundError("INVALID_PAGING", "limit 为 1–200，offset 为 0–5000，query 不超过 200 字符。")
        first = self.page("conversations")
        initial_top = first["scroll"]["top"]
        collected = {}
        complete = False
        deadline = time.monotonic() + 18
        stable_since = None
        previous_key = None
        try:
            if load_more:
                self.page("scroll_sidebar", position="bottom")
            while True:
                data = self.page("conversations")
                for item in data["items"]:
                    collected[item["id"]] = item
                filtered = [item for item in collected.values() if query.casefold() in item["title"].casefold()]
                scroll = data["scroll"]
                key = (len(collected), scroll["total"], scroll["top"])
                if scroll["bottom"] or not scroll["available"]:
                    if key != previous_key:
                        stable_since = time.monotonic()
                    if stable_since is not None and time.monotonic() - stable_since >= 1.5:
                        complete = True
                        break
                else:
                    stable_since = None
                exact_duplicates = _exhaustive and sum(item["title"].strip().casefold() == query.strip().casefold() for item in filtered) > 1
                if not load_more or exact_duplicates or (not _exhaustive and len(filtered) > offset + limit) or time.monotonic() >= deadline:
                    break
                previous_key = key
                # This desktop sidebar retains loaded rows in the DOM. Jumping
                # to its end loads the next page without walking cached rows.
                self.page("scroll_sidebar", position="bottom")
                time.sleep(0.2)
        finally:
            self.page("scroll_sidebar", position=initial_top)
        filtered = [item for item in collected.values() if query.casefold() in item["title"].casefold()]
        return {"ok": True, "conversations": filtered[offset:offset + limit], "offset": offset,
                "has_more": len(filtered) > offset + limit or not complete,
                "scan_complete": complete, "loaded_count": len(collected),
                "matched_loaded_count": len(filtered), "background": True}

    def open_conversation(self, conversation_id=None, title=None):
        if bool(conversation_id) == bool(title):
            raise BackgroundError("INVALID_TARGET", "请提供 conversation_id 或 title 中的一项。")
        if title:
            data = self.list_conversations(query=title, limit=200, _exhaustive=True)
            matches = [item for item in data["conversations"] if item["title"].strip().casefold() == title.strip().casefold()]
            if len(matches) > 1:
                raise BackgroundError("AMBIGUOUS_TITLE", "多个对话标题相同，请使用 ID 指定。", candidates=matches)
            if not data["scan_complete"]:
                raise BackgroundError("SEARCH_INCOMPLETE", "历史列表尚未完全加载，无法唯一确认标题；请使用对话 ID。", candidates=matches)
            if not matches:
                raise BackgroundError("CONVERSATION_NOT_FOUND", "没有找到标题完全匹配的对话。")
            conversation_id = matches[0]["id"]
        conversation_id = normalize_id(conversation_id)
        self.page("open", id=conversation_id)
        return self._wait_ready(target_id=conversation_id)

    def read_history(self, conversation_id=None, title=None, limit=20, offset=0, max_chars=6000):
        if not 1 <= limit <= 200 or not 0 <= offset <= 5000 or not 100 <= max_chars <= 20000:
            raise BackgroundError("INVALID_PAGING", "limit 为 1–200，offset 为 0–5000，max_chars 为 100–20000。")
        if conversation_id or title:
            self.open_conversation(conversation_id, title)
        initial = self.page("snapshot")
        target = initial["conversation_id"]
        scroll = self.page("scroll_messages")
        original_top = scroll["top"]
        collected = {}
        reached_start = False
        deadline = time.monotonic() + 20
        previous_key = None
        stable_since = None
        try:
            self.page("scroll_messages", position="bottom")
            time.sleep(0.2)
            while True:
                data = self.page("snapshot")
                if data["conversation_id"] != target:
                    raise BackgroundError("CONVERSATION_CHANGED", "读取期间对话被切换，已停止。")
                for message in data["messages"]:
                    collected[message["id"]] = message
                scroll = self.page("scroll_messages")
                key = (min(collected, key=int) if collected else None, scroll["total"], scroll["top"])
                if scroll["top"] <= 2 or not scroll["available"]:
                    if key != previous_key:
                        stable_since = time.monotonic()
                    if stable_since is not None and time.monotonic() - stable_since >= 1.5:
                        reached_start = True
                        break
                else:
                    stable_since = None
                if len(collected) >= offset + limit + 1 or time.monotonic() >= deadline:
                    break
                previous_key = key
                self.page("scroll_messages", position=max(0, scroll["top"] - max(100, scroll["height"] * 0.75)))
                time.sleep(0.2)
        finally:
            self.page("scroll_messages", position=original_top)
        ordered = sorted(collected.values(), key=lambda m: int(m["id"]))
        end = max(0, len(ordered) - offset)
        selected = ordered[max(0, end-limit):end]
        for item in selected:
            item.pop("row", None)
            item["text_truncated"] = len(item["text"]) > max_chars
            item["text"] = item["text"][:max_chars]
        return {"ok": True, "conversation_id": target, "messages": selected,
                "order": "oldest_to_newest", "offset_from_latest": offset,
                "has_older": not reached_start or len(ordered) > offset + limit,
                "reached_start": reached_start, "loaded_messages": len(ordered), "background": True}

    def read_reply(self):
        self.page("scroll_messages", position="bottom")
        time.sleep(0.05)
        data = self.page("snapshot")
        last = data["messages"][-1] if data["messages"] else None
        reply = last if last and last["role"] == "assistant" else None
        return {"ok": True, "reply": reply["text"] if reply else None,
                "message_id": reply["id"] if reply else None,
                "complete": bool(reply and reply["complete"] and not data["generating"]),
                "generating": data["generating"], "conversation_id": data["conversation_id"],
                "background": True}

    def ask(self, prompt, new_chat=False, timeout=120, conversation_id=None, title=None):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000 or '\x00' in prompt:
            raise BackgroundError("INVALID_PROMPT", "prompt 必须为 1–20000 字符的非空文本，不能包含空字符。")
        if not 10 <= timeout <= 300:
            raise BackgroundError("INVALID_TIMEOUT", "timeout 为 10–300 秒。")
        if new_chat and (conversation_id or title):
            raise BackgroundError("INVALID_TARGET", "指定已有对话时不能同时要求新建对话。")
        if conversation_id or title:
            self.open_conversation(conversation_id, title)
        if new_chat:
            self.new_chat()
        self.page("check_text_mode")
        self.page("scroll_messages", position="bottom")
        time.sleep(0.2)
        before = self.page("snapshot")
        previous_ids = {m["id"] for m in before["messages"]}
        self.page("focus_editor")  # Document focus only; never native window focus.
        self.client.call("Input.insertText", {"text": prompt.replace('\r\n','\n').replace('\r','\n')})
        for _ in range(20):
            if self.page("draft")["text"] == prompt.replace('\r\n','\n').replace('\r','\n').strip():
                break
            time.sleep(0.1)
        else:
            raise BackgroundError("INPUT_MISMATCH", "后台输入回读不一致，草稿已保留，未发送。")
        self.sent = True
        self.page("submit", prompt=prompt)
        deadline = time.monotonic() + timeout
        target = before["conversation_id"]
        stable_id = None
        stable_since = 0
        last = None
        while time.monotonic() < deadline:
            time.sleep(0.4)
            last = self.read_reply()
            if target and last["conversation_id"] != target:
                raise BackgroundError("CONVERSATION_CHANGED", "等待时对话发生切换；消息可能已发送，请勿自动重发。")
            target = target or last["conversation_id"]
            if last["complete"] and last["message_id"] not in previous_ids:
                key = (last["message_id"], last["reply"])
                if stable_id != key:
                    stable_id, stable_since = key, time.monotonic()
                elif time.monotonic() - stable_since >= 0.8:
                    return {**last, "sent": True}
            else:
                stable_id = None
        return {"ok": False, "code": "REPLY_TIMEOUT", "sent": True, "conversation_id": target,
                "message": "消息已提交，未在时限内确认新回复。请先读取回复，不要重复发送。",
                "reply": last.get("reply") if last else None, "background": True}
