"""Local image upload and verified current-attachment mention binding."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time


MEDIA = Path(__file__).with_name("media.js").read_text(encoding="utf-8")
MAX_IMAGE_BYTES = 100 * 1024 * 1024


class MediaError(RuntimeError):
    def __init__(self, code, message, **details):
        self.code, self.details = code, details
        super().__init__(message)


def validate_image(path):
    if not isinstance(path, str) or not path.strip() or "\0" in path:
        raise MediaError("INVALID_IMAGE_PATH", "请提供图片的本地绝对路径。")
    source = Path(path)
    if not source.is_absolute():
        raise MediaError("INVALID_IMAGE_PATH", "图片路径必须为本地绝对路径。")
    try:
        source = source.resolve()
        size = source.stat().st_size
    except (OSError, ValueError) as error:
        raise MediaError("IMAGE_NOT_FOUND", "无法读取指定的图片文件。") from error
    if not source.is_file() or not 0 < size <= MAX_IMAGE_BYTES:
        raise MediaError("INVALID_IMAGE_FILE", "图片必须为非空普通文件，最大100 MiB。")
    suffix = source.suffix.lower()
    with source.open("rb") as stream:
        head = stream.read(32)
    valid = ((suffix in {".png", ".apng"} and head.startswith(b"\x89PNG\r\n\x1a\n")) or
             (suffix in {".jpg", ".jpeg"} and head.startswith(b"\xff\xd8\xff")) or
             (suffix == ".webp" and head[:4] == b"RIFF" and head[8:12] == b"WEBP"))
    if not valid:
        raise MediaError("UNSUPPORTED_IMAGE", "仅支持文件内容与扩展名一致的 PNG/APNG、JPEG、WEBP 图片。")
    sha, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
    with source.open("rb") as stream:
        while data := stream.read(1024 * 1024):
            sha.update(data)
            md5.update(data)
    return {"path":str(source),"name":source.name,"size":size,"sha256":sha.hexdigest(),"md5":md5.hexdigest()}


class MediaDriver:
    def __init__(self, driver):
        self.driver = driver

    def _call(self, action, **params):
        from .background import PAGE
        self.driver.connect()
        source = PAGE if "const doubaoMedia =" in PAGE else PAGE + "\n" + MEDIA
        expression = ("(async()=>{" + source + "\ntry{return await doubaoMedia[" + json.dumps(action)
                      + "](" + json.dumps(params, ensure_ascii=True) + ");}catch(e){return {ok:false,code:e.code||'MEDIA_PAGE_ERROR',message:e.message,details:e.details||{}};}})()")
        result = self.driver.client.evaluate(expression, timeout=20)
        if not isinstance(result, dict):
            raise MediaError("MEDIA_PAGE_ERROR", "图片操作没有返回可验证结果。")
        if result.get("ok") is False:
            raise MediaError(result["code"], result["message"], **result.get("details", {}))
        return result

    def inspect(self):
        return self._call("inspect")

    def verify(self, prompt, asset_ids):
        return self._call("verify", prompt=prompt, asset_ids=asset_ids)

    def upload(self, paths, timeout=90):
        if not isinstance(paths, list) or not 1 <= len(paths) <= 10:
            raise MediaError("INVALID_IMAGE_PATHS", "paths 应为1–10个本地图片绝对路径的数组。")
        if type(timeout) is not int or not 5 <= timeout <= 300:
            raise MediaError("INVALID_TIMEOUT", "图片上传 timeout 应在5–300秒之间。")
        files = [validate_image(path) for path in paths]
        if len({f["path"].casefold() for f in files}) != len(files):
            raise MediaError("DUPLICATE_IMAGE_PATH", "同一图片路径不能重复上传。")
        before = self._call("uploadTarget")
        existing, pending = [], []
        for file in files:
            matches = [a for a in before["assets"] if a["name"] == file["name"] and a.get("size") == file["size"]
                       and (a.get("md5") == file["md5"] or not a["uploaded"])]
            if len(matches) > 1:
                raise MediaError("AMBIGUOUS_IMAGE_UPLOAD", "当前存在同名同内容的多个附件，请先处理重复图片。")
            if matches:
                if not matches[0]["uploaded"]:
                    raise MediaError("IMAGE_UPLOAD_IN_PROGRESS", "同名图片仍在上传中，请先检查原上传，不重复选择文件。", assets=matches)
                existing.append({**matches[0],"local_path":file["path"],"sha256":file["sha256"],"already_uploaded":True})
            else:
                pending.append(file)
        if not pending:
            return {"ok":True,"background":True,"submitted":False,"context":before["context"],"assets":existing,"upload_verified":True}
        if len(before["assets"]) + len(pending) > 10:
            raise MediaError("TOO_MANY_IMAGES", "当前对话图片加本次上传不能超过10张。")
        accepted = {part.strip().lower() for part in before["accept"].split(",")}
        if any(Path(f["path"]).suffix.lower() not in accepted for f in pending):
            raise MediaError("IMAGE_NOT_ACCEPTED", "当前客户端上传控件不接受某张图片的文件类型。")
        document = self.driver.client.call("DOM.getDocument", {"depth":1})
        node = self.driver.client.call("DOM.querySelector", {"nodeId":document["root"]["nodeId"],"selector":before["selector"]})
        if not node.get("nodeId"):
            raise MediaError("IMAGE_UPLOAD_UNAVAILABLE", "上传前图片选择控件已变化。")
        self.driver.client.call("DOM.setFileInputFiles", {"nodeId":node["nodeId"],"files":[f["path"] for f in pending]})
        previous = {a["local_key"] for a in before["assets"]}
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self.inspect()
            if last["context"] != before["context"]:
                raise MediaError("MEDIA_CONTEXT_CHANGED", "上传期间当前对话输入区已变化。", upload_started=True)
            fresh = [a for a in last["assets"] if a["local_key"] not in previous]
            uploaded = []
            for file in pending:
                matches = [a for a in fresh if a["name"] == file["name"] and a.get("md5") == file["md5"] and a.get("size") == file["size"]]
                if len(matches) == 1 and matches[0]["uploaded"]:
                    uploaded.append({**matches[0],"local_path":file["path"],"sha256":file["sha256"]})
            if len(uploaded) == len(pending):
                combined = {a["local_path"]:a for a in existing+uploaded}
                ordered = [combined[f["path"]] for f in files]
                return {"ok":True,"background":True,"submitted":False,"context":last["context"],"assets":ordered,"upload_verified":True}
            failed = [a for a in fresh if any(k in str(a["status"]).lower() for k in ("fail","error","reject"))]
            if failed:
                raise MediaError("IMAGE_UPLOAD_FAILED", "豆包报告图片上传失败。", assets=failed)
            time.sleep(.3)
        raise MediaError("IMAGE_UPLOAD_TIMEOUT", "图片上传尚未完成，请先检查当前附件，不要重复上传。", upload_started=True, assets=(last or {}).get("assets", []))

    def bind(self, asset_id, label=None, timeout=10):
        if not isinstance(asset_id, str) or not asset_id or len(asset_id) > 2000:
            raise MediaError("INVALID_IMAGE_ASSET", "请提供上传工具返回的真实图片 asset_id。")
        if type(timeout) is not int or not 2 <= timeout <= 60:
            raise MediaError("INVALID_TIMEOUT", "绑定 timeout 应在2–60秒之间。")
        state = self.inspect()
        matches = [a for a in state["assets"] if a["asset_id"] == asset_id and a["uploaded"]]
        if len(matches) != 1:
            raise MediaError("IMAGE_NOT_UPLOADED", "图片尚未在当前对话上传完成。")
        if label is not None and label != matches[0]["name"]:
            raise MediaError("IMAGE_LABEL_MISMATCH", "label 必须与真实图片候选的文件名一致，不能伪造引用标签。")
        before = self._call("beginBind", asset_id=asset_id)
        if before.get("already_bound"):
            return {**before,"background":True,"submitted":False,"binding_verified":True,"already_bound":True}
        if not before.get("trigger_already_present"):
            self.driver.client.call("Input.insertText", {"text":before.get("insert_text", "@")})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            options = self._call("candidates")
            # Candidates returns an object so every cross-process reply stays explicit.
            if any(o.get("resource_id") == asset_id for o in options.get("candidates", [])):
                self._call("selectCandidate", asset_id=asset_id, expected_context=before["context"])
                break
            time.sleep(.2)
        else:
            raise MediaError("IMAGE_CANDIDATE_TIMEOUT", "未找到匹配真实资源的 @ 图片候选；保留草稿供检查。", draft_may_have_changed=True)
        while time.monotonic() < deadline:
            state = self.inspect()
            if any(b["asset_id"] == asset_id for b in state["bindings"]):
                return {**state,"background":True,"submitted":False,"binding_verified":True,"already_bound":False}
            time.sleep(.1)
        raise MediaError("IMAGE_BINDING_UNVERIFIED", "点击候选后未回读到真实图片引用节点。", draft_may_have_changed=True)

    def prepare_prompt(self, prompt, asset_ids, timeout=15, source_prompt=None):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000 or "\0" in prompt:
            raise MediaError("INVALID_PROMPT", "完整提示词必须为1–20000字符非空字符串。")
        if not isinstance(asset_ids, list) or any(not isinstance(x, str) or not x for x in asset_ids) or len(set(asset_ids)) != len(asset_ids):
            raise MediaError("INVALID_IMAGE_ASSETS", "asset_ids 应为不重复的真实图片资源ID数组。")
        if type(timeout) is not int or not 2 <= timeout <= 300:
            raise MediaError("INVALID_TIMEOUT", "提示词准备 timeout 应在2–300秒之间。")
        prompt = prompt.replace("\r\n", "\n").replace("\r", "\n")
        if source_prompt is not None:
            if not isinstance(source_prompt, str) or not source_prompt.strip() or len(source_prompt) > 20000 or "\0" in source_prompt:
                raise MediaError("INVALID_SOURCE_PROMPT", "受控替换的原提示词必须是有效完整原文。")
            source_prompt = source_prompt.replace("\r\n", "\n").replace("\r", "\n")
            from .prompts import _split_video_prompt
            wrapped = _split_video_prompt(prompt)
            if wrapped is None or wrapped[1] != source_prompt:
                raise MediaError("INVALID_SOURCE_PROMPT", "受控替换只允许给同一份用户原文添加系统生成指令。")
        state = self.inspect()
        selected = []
        for asset_id in asset_ids:
            matches = [a for a in state["assets"] if a["asset_id"] == asset_id and a["uploaded"]]
            if len(matches) != 1:
                raise MediaError("IMAGE_NOT_UPLOADED", "图片尚未在当前对话上传完成。")
            asset = matches[0]
            if sum(a["name"] == asset["name"] for a in state["assets"]) != 1:
                raise MediaError("AMBIGUOUS_IMAGE_LABEL", "当前有同名图片，不能按文件名猜测引用。")
            if not re.search("@" + re.escape(asset["name"]) + r"(?![A-Za-z0-9_.-])", prompt):
                raise MediaError("IMAGE_REFERENCE_MISSING", "完整提示词必须包含真实图片占位符 @" + asset["name"])
            selected.append(asset)
        known = {a["name"] for a in selected}
        for match in re.finditer(r"@([^\s@]+?\.(?:png|jpe?g|webp|apng))(?![A-Za-z0-9_.-])", prompt, re.I):
            if match[1] not in known:
                raise MediaError("UNBOUND_IMAGE_REFERENCE", "提示词包含未绑定的图片引用：@" + match[1])
        def only_references(node):
            if node["type"] == "text":
                return not node.get("text", "").strip()
            if node["type"] == "inputEngineMention":
                return True
            return node["type"] in {"doc", "paragraph"} and all(only_references(n) for n in node.get("content", []))
        source_matches = source_prompt is not None and state["draft_text"] == source_prompt
        if state["draft_text"] != prompt and not only_references(state["editor_json"]) and not source_matches:
            raise MediaError("DRAFT_PRESENT", "输入框已有其他提示词，未覆盖现有草稿。")
        if state.get("unresolved_mentions"):
            raise MediaError("UNRESOLVED_IMAGE_REFERENCE", "输入区有无法解析的引用，未覆盖草稿。")
        deadline = time.monotonic() + timeout
        for asset_id in asset_ids:
            if not any(b["asset_id"] == asset_id for b in state["bindings"]):
                remaining = int(deadline-time.monotonic())
                if remaining < 2:
                    raise MediaError("IMAGE_BINDING_TIMEOUT", "图片绑定未在时限内完成，草稿已保留。")
                state = self.bind(asset_id, timeout=min(remaining,60))
        parameters = {"prompt":prompt,"asset_ids":asset_ids}
        if source_prompt is not None:
            parameters["source_prompt"] = source_prompt
        prepared = self._call("prepare", **parameters)
        return {**prepared,"background":True,"submitted":False,"binding_verified":True}
