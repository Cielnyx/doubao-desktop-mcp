"""Video status evidence and a durable local idempotency ledger, without sending."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid


class TaskError(RuntimeError):
    def __init__(self, code, message, **details):
        self.code, self.details = code, details
        super().__init__(message)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value):
    return " ".join(str(value or "").split())


def _remote_id(value):
    if value is None:
        return None
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_:\-]{1,200}", value):
        raise TaskError("INVALID_TASK_TARGET", "任务或消息标识格式不正确。")
    return value


class TaskStore:
    """Local request IDs are never presented as remote generation task IDs.

    Callers serialize reserve/mark_attempt/update using the desktop operation lock.
    mark_attempt must complete before clicking Send. An attempted request is never
    made sendable again, even when the remote acknowledgment was not observed.
    """
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("DOUBAO_TASK_DIR") or
                         Path(__file__).resolve().parents[2] / ".runtime" / "video_tasks")

    def _path(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", request_id):
            raise TaskError("INVALID_REQUEST_ID", "request_id 只能包含字母、数字、下划线和短横线（最多128字符）。")
        return self.root / (request_id + ".json")

    def get(self, request_id):
        path = self._path(request_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise TaskError("REQUEST_NOT_FOUND", "没有找到该本地请求记录。", request_id=request_id) from error
        except (OSError, ValueError) as error:
            raise TaskError("TASK_LEDGER_INCOMPLETE", "请求记录无法完整读取；请查询原任务，不要重新提交。", request_id=request_id) from error
        if record.get("request_id") != request_id:
            raise TaskError("TASK_LEDGER_INCOMPLETE", "请求记录标识不一致，已停止。")
        if path.with_suffix(".attempt").exists() and not record.get("submission_attempted"):
            # A crash after the durable marker but before the JSON update is
            # uncertain, never permission to click Send a second time.
            record.update(submission_attempted=True, attempt_count=1, state="uncertain", attempt_marker_only=True)
        if path.with_suffix(".confirm-attempt").exists() and not record.get("confirmation_attempted"):
            record.update(confirmation_attempted=True, confirmation_attempt_count=1,
                          state="confirmation_unknown", confirmation_marker_only=True)
        return record

    def _write(self, record):
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._path(record["request_id"])
        temporary = self.root / (".task-" + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()

    def reserve(self, request_id, payload, conversation_id=None, before_message_ids=None):
        target = self._path(request_id)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                          separators=(",", ":")).encode("utf-8")).hexdigest()
        if target.exists():
            record = self.get(request_id)
            if record.get("fingerprint") != digest:
                raise TaskError("REQUEST_ID_CONFLICT", "同一 request_id 已用于不同提示词、参数或图片，不能复用。", request_id=request_id)
            return {"created": False, "record": record}
        self.root.mkdir(parents=True, exist_ok=True)
        record = {"schema_version": 1, "request_id": request_id, "fingerprint": digest,
                  "payload": payload, "state": "prepared", "submission_attempted": False,
                  "attempt_count": 0, "conversation_id": _remote_id(conversation_id),
                  "user_message_id": None, "message_id": None, "task_id": None,
                  "confirmation_attempted": False, "confirmation_attempt_count": 0,
                  "confirmation_message_id": None, "confirmation_user_message_id": None,
                  "before_message_ids": [_remote_id(value) for value in (before_message_ids or [])],
                  "created_at": _now(), "updated_at": _now()}
        try:
            with target.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            return self.reserve(request_id, payload, conversation_id, before_message_ids)
        return {"created": True, "record": record}

    def update(self, request_id, **fields):
        record = self.get(request_id)
        immutable = {"request_id", "fingerprint", "payload", "created_at", "before_message_ids", "schema_version",
                     "confirmation_prompt", "confirmation_before_message_ids"}
        if immutable & fields.keys():
            raise TaskError("TASK_LEDGER_IMMUTABLE", "不能改写请求的原始内容或幂等标识。")
        if "submission_attempted" in fields and fields["submission_attempted"] is not record["submission_attempted"]:
            raise TaskError("TASK_LEDGER_IMMUTABLE", "提交尝试标记只能由 mark_attempt 写入，不能清除。")
        if "attempt_count" in fields and fields["attempt_count"] != record["attempt_count"]:
            raise TaskError("TASK_LEDGER_IMMUTABLE", "提交计数不能手动改写。")
        for key in ("confirmation_attempted", "confirmation_attempt_count"):
            if key in fields and fields[key] != record.get(key, False if key.endswith('attempted') else 0):
                raise TaskError("TASK_LEDGER_IMMUTABLE", "确认尝试标记和计数不能手动改写。")
        for key in ("conversation_id", "user_message_id", "message_id", "task_id", "confirmation_message_id", "confirmation_user_message_id"):
            if key not in fields:
                continue
            fields[key] = _remote_id(fields[key])
            if record.get(key) and fields[key] != record[key]:
                raise TaskError("TASK_BINDING_CONFLICT", "原请求已绑定其他真实标识，拒绝改绑。", field=key)
        record.update(fields, updated_at=_now())
        self._write(record)
        return record

    def mark_confirmation_attempt(self, request_id, prompt, before_message_ids):
        record = self.get(request_id)
        if record.get("confirmation_attempted"):
            raise TaskError("DUPLICATE_CONFIRMATION", "原请求已尝试确认，请查询原任务，禁止重复确认。", request_id=request_id)
        if record.get("state") != "confirmation_required" or not record.get("user_message_id") or not record.get("submission_attempted"):
            raise TaskError("CONFIRMATION_NOT_REQUIRED", "原请求尚未确认处于待确认状态，未发送确认。")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt)>20000:
            raise TaskError("INVALID_CONFIRMATION_PROMPT", "确认提示词必须为非空完整文本。")
        before=[_remote_id(value) for value in before_message_ids]
        marker=self._path(request_id).with_suffix(".confirm-attempt")
        try:
            with marker.open("x",encoding="utf-8") as stream:
                json.dump({"request_id":request_id,"confirmation_prompt_sha256":hashlib.sha256(prompt.encode()).hexdigest(),
                           "attempted_at":_now()},stream)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as error:
            raise TaskError("DUPLICATE_CONFIRMATION", "原请求已有确认尝试标记，禁止重复发送。") from error
        record.update(confirmation_attempted=True, confirmation_attempt_count=1, state="confirmation_attempted",
                      confirmation_prompt=prompt, confirmation_before_message_ids=before,
                      confirmation_attempted_at=_now(), updated_at=_now())
        self._write(record)
        return record

    def mark_attempt(self, request_id):
        record = self.get(request_id)
        if record.get("submission_attempted") or record.get("state") not in {"prepared"}:
            raise TaskError("DUPLICATE_SUBMISSION", "该请求已尝试提交或状态不确定，请查询原任务，禁止重发。",
                            request_id=request_id, record=record)
        marker = self._path(request_id).with_suffix(".attempt")
        try:
            with marker.open("x", encoding="utf-8") as stream:
                json.dump({"request_id":request_id,"fingerprint":record["fingerprint"],"attempted_at":_now()}, stream)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as error:
            raise TaskError("DUPLICATE_SUBMISSION", "该请求已有提交尝试标记，禁止重复发送。", request_id=request_id) from error
        record.update(submission_attempted=True, attempt_count=1, state="attempted", attempted_at=_now(), updated_at=_now())
        self._write(record)
        return record


TERMINAL = {"completed", "failed", "quota_exceeded"}
STATUSES = TERMINAL | {"submitted", "submission_unknown", "queued", "generating", "responding",
                       "confirmation_required", "unknown", "not_visible", "ambiguous"}


def classify_task(record):
    """Classify only an assistant task/message, using positive card evidence first."""
    if record.get("role") != "assistant":
        return {"status": "submitted", "terminal": False, "evidence": ["user_message_observed"]}
    body = _text(record.get("text"))
    card = record.get("card") or {}
    videos = record.get("videos") or []
    if card.get("is_success") is True:
        return {"status": "completed", "terminal": True, "evidence": ["client_card.is_success"]}
    if (record.get("message_meta") or {}).get("confirmation_required"):
        return {"status":"confirmation_required", "terminal":False, "evidence":["react.message.ext.is_creation_clarifying"]}
    if any(video.get("url") and (video.get("duration") or 0) > 0 and
           (video.get("ready_state", 1) or 0) >= 1 for video in videos):
        return {"status": "completed", "terminal": True, "evidence": ["video_resource_and_metadata"]}
    patterns = [
        ("quota_exceeded", r"(?:额度|积分|次数|余额).{0,16}(?:不足|已用完|用尽|耗尽)|(?:超出|超过).{0,12}(?:额度|次数|每日上限)|insufficient (?:quota|credits)|quota (?:exceeded|exhausted)"),
        ("failed", r"生成失败|任务失败|审核未通过|未通过审核|无法生成|generation failed|task failed"),
        ("confirmation_required", r"确认后.{0,12}(?:开始|生成)|确认.{0,10}再.{0,10}生成|是否.{0,10}(?:开始生成|确认生成)"),
        ("queued", r"排队中|正在排队|队列中|等待生成|前方.{0,10}(?:人|个)|\bqueued\b|in queue"),
        ("generating", r"生成中|正在生成|正在为你生成|视频制作中|渲染中|\bgenerating\b|\brendering\b"),
        ("completed", r"你的视频生成好了|视频已生成|视频生成完成|video generation completed"),
    ]
    for status, pattern in patterns:
        match = re.search(pattern, body, re.I)
        if match:
            return {"status": status, "terminal": status in TERMINAL, "evidence": ["assistant_text:" + match.group(0)]}
    if record.get("streaming"):
        return {"status": "responding", "terminal": False, "evidence": ["assistant_streaming"]}
    return {"status": "unknown", "terminal": False, "evidence": []}


class VideoTaskReader:
    def __init__(self, driver, store=None):
        self.driver = driver
        self.store = store or TaskStore()

    @staticmethod
    def _replies(rows, user_id):
        direct = [row for row in rows if row.get("role") == "assistant" and
                  (row.get("message_meta") or {}).get("reply_id") == user_id]
        if direct:
            return direct
        result, following = [], False
        for row in rows:
            if row.get("message_id") == user_id:
                following = True
                continue
            if following and row.get("role") == "user":
                break
            if following and row.get("role") == "assistant":
                result.append(row)
        return result

    @staticmethod
    def _input_matches(row, expected, assets):
        if row.get("role") != "user":
            return False
        meta = row.get("message_meta") or {}
        if all(_text(value) not in expected for value in (row.get("text"), meta.get("original_input"), meta.get("raw_text"))):
            return False
        if assets and meta.get("original_input") and set(meta.get("input_asset_ids", [])) != assets:
            return False
        return True

    def query(self, request_id=None, conversation_id=None, message_id=None, task_id=None):
        record = self.store.get(request_id) if request_id else None
        conversation_id = _remote_id(conversation_id or (record or {}).get("conversation_id"))
        message_id = _remote_id(message_id or (record or {}).get("message_id"))
        task_id = _remote_id(task_id or (record or {}).get("task_id"))
        if record:
            for key, value in (("conversation_id", conversation_id), ("message_id", message_id), ("task_id", task_id)):
                if record.get(key) and value != record[key]:
                    raise TaskError("TASK_BINDING_CONFLICT", "查询标识与原请求绑定不一致。", field=key)
        current = self.driver.status()
        if conversation_id and current.get("conversation_id") != conversation_id:
            self.driver.open_conversation(conversation_id=conversation_id)
        confirming = bool((record or {}).get("confirmation_attempted"))
        anchor_key = "confirmation_user_message_id" if confirming else "user_message_id"
        anchor = (record or {}).get(anchor_key)
        snapshot = self.driver.page("video_task_status", message_id=message_id or anchor or (record or {}).get("user_message_id"), task_id=task_id)
        if conversation_id and snapshot.get("conversation_id") != conversation_id:
            raise TaskError("CONVERSATION_CHANGED", "未能确认查询的是原任务对话。")
        rows = snapshot.get("records", [])
        updates = {}
        selected = []
        observed_user_id = (record or {}).get("user_message_id")
        if record and not anchor and record.get("submission_attempted"):
            before = set(record.get("confirmation_before_message_ids" if confirming else "before_message_ids", []))
            values = [record.get("confirmation_prompt")] if confirming else [record.get("draft_text"),
                      record.get("draft_visible_text"), record.get("draft_serialized_text"),
                      record.get("payload", {}).get("draft_text"), record.get("payload", {}).get("prompt")]
            expected = {_text(value) for value in values if value}
            assets = set() if confirming else set(record.get("payload", {}).get("image_asset_ids", []))
            matches = set()
            for row in rows:
                if row.get("message_id") in before or not self._input_matches(row, expected, assets):
                    continue
                actual = (row.get("message_meta") or {}).get("primary_message_id") or row["message_id"]
                if actual not in before:
                    matches.add(actual)
            if len(matches) == 1:
                anchor = next(iter(matches))
                updates[anchor_key] = anchor
                updates["conversation_id"] = snapshot.get("conversation_id")
                if not confirming:
                    observed_user_id = anchor
        scope = self._replies(rows, anchor) if anchor else []
        if task_id:
            selected = [row for row in rows if (row.get("card") or {}).get("task_id") == task_id or
                        (not (row.get("card") or {}).get("task_id") and any(t.get("id") == task_id for t in row.get("task_ids", [])))]
        elif message_id:
            selected = [row for row in rows if row.get("message_id") == message_id]
        elif record:
            selected = scope
        else:
            selected = [row for row in rows if row.get("role") == "assistant" and
                        (row.get("task_ids") or row.get("card") or row.get("videos"))]
        if record and ((task_id and not record.get("task_id")) or (message_id and not record.get("message_id"))):
            allowed = {row.get("message_id") for row in scope} | ({anchor} if anchor else set())
            if not anchor or not allowed:
                raise TaskError("TASK_BINDING_UNVERIFIED", "原请求尚未关联到真实用户消息，请先只按 request_id 查询。")
            if any(row.get("message_id") not in allowed for row in selected):
                raise TaskError("TASK_BINDING_CONFLICT", "指定任务不属于原请求的用户消息，拒绝改绑。")
        if len(selected) == 1 and selected[0].get("role") == "user":
            observed_user_id = selected[0]["message_id"]
            selected = self._replies(rows, observed_user_id)
        identities = {item["id"] for row in selected for item in row.get("task_ids", [])}
        if len(identities) > 1 and not task_id:
            result = {"status":"ambiguous", "terminal":False, "tasks":sorted(identities), "videos":[], "evidence":[]}
        elif selected:
            with_cards = [row for row in selected if row.get("card") or row.get("videos") or row.get("task_ids")]
            if with_cards:
                selected = with_cards
            # Multiple plain replies without a card cannot be silently rebound.
            if len({row.get("message_id") for row in selected}) > 1 and not identities:
                result = {"status":"ambiguous", "terminal":False, "tasks":[], "videos":[], "evidence":[]}
            else:
                chosen = selected[-1]
                result = {**classify_task(chosen), "message_id":chosen.get("message_id"),
                          "task_id":task_id or next(iter(identities), None), "videos":chosen.get("videos", []),
                          "message_text":chosen.get("text", ""), "task_id_sources":chosen.get("task_ids", [])}
                if record:
                    updates.update(task_id=result["task_id"], state=result["status"], last_status=result)
                    if result["status"] == "confirmation_required" and not confirming:
                        updates.update(confirmation_message_id=result["message_id"], confirmation_text=result["message_text"])
                    elif chosen.get("card") or chosen.get("task_ids") or chosen.get("videos") or result["status"] in TERMINAL:
                        updates["message_id"] = result["message_id"]
        else:
            user_observed = bool(anchor)
            status = "not_visible" if task_id or message_id else "submitted" if user_observed else "submission_unknown" if record and record.get("submission_attempted") else "not_visible"
            result = {"status":status, "terminal":False, "videos":[], "evidence":[], "last_known_status":(record or {}).get("state")}
        if record and updates:
            self.store.update(request_id, **{k:v for k,v in updates.items() if v is not None})
        return {"ok":True, "request_id":request_id, "conversation_id":snapshot.get("conversation_id"),
                "user_message_id":observed_user_id,
                "confirmation_user_message_id":anchor if confirming else (record or {}).get("confirmation_user_message_id"),
                "confirmation_attempted":confirming, "phase":"confirmation" if confirming else "submission",
                "message_id":message_id, "task_id":task_id, "scan_complete":snapshot.get("scan_complete", False),
                "submitted":bool((record or {}).get("submission_attempted")), **result}
