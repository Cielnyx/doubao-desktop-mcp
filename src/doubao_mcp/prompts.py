"""Deterministic video request wrapper; the user's text remains unchanged."""
from __future__ import annotations


MAX_VIDEO_PROMPT_CHARS = 20000
VIDEO_PROMPT_RULES = (
    "请直接开始生成，不要再次询问是否需要生成或要求确认。"
    "严格按照以下用户提示词和已绑定参考图片执行，不改写、不扩写、不擅自增删内容；"
    "模型、比例和时长以本次已选参数为准。"
)
_MARKER = "\n" + VIDEO_PROMPT_RULES + "\n用户提示词：\n"


class PromptError(RuntimeError):
    def __init__(self, code, message, **details):
        self.code, self.details = code, details
        super().__init__(message)


def _validate_model(model):
    if (not isinstance(model, str) or not model or model != model.strip()
            or len(model) > 100 or not model.isprintable()):
        raise PromptError("INVALID_VIDEO_MODEL", "model 必须是客户端实际选项中的完整模型名称。")
    return model


def _validate_prompt(prompt):
    if not isinstance(prompt, str) or not prompt.strip() or "\0" in prompt:
        raise PromptError("INVALID_PROMPT", "用户提示词必须是非空字符串，不能包含空字符。")
    if len(prompt) > MAX_VIDEO_PROMPT_CHARS:
        raise PromptError("VIDEO_PROMPT_TOO_LONG", "加上生成指令后的完整提示词不能超过20000字符。",
                          max_chars=MAX_VIDEO_PROMPT_CHARS, actual_chars=len(prompt))


def _split_video_prompt(prompt):
    """Recognize only this module's complete, exact wrapper."""
    start = "帮我用"
    end = "生成视频。"
    if not isinstance(prompt, str) or not prompt.startswith(start):
        return None
    head, separator, body = prompt.partition(_MARKER)
    if not separator or not head.endswith(end) or "\n" in head or "\r" in head:
        return None
    model = head[len(start):-len(end)]
    if not model:
        return None
    return model, body


def extract_user_prompt(model: str, prompt: str) -> str:
    """Return the original body, or the unchanged input if no complete wrapper exists."""
    model = _validate_model(model)
    _validate_prompt(prompt)
    wrapped = _split_video_prompt(prompt)
    if wrapped is None:
        return prompt
    wrapped_model, user_prompt = wrapped
    if wrapped_model != model:
        raise PromptError("VIDEO_PROMPT_MODEL_MISMATCH", "完整生成指令中的模型与本次实际模型不一致。",
                          requested_model=model, prompt_model=wrapped_model)
    _validate_prompt(user_prompt)
    return user_prompt


def build_video_prompt(model: str, user_prompt: str) -> str:
    """Add one controlled prefix, preserving original text, line endings and @ markers."""
    model = _validate_model(model)
    _validate_prompt(user_prompt)
    if _split_video_prompt(user_prompt) is not None:
        extract_user_prompt(model, user_prompt)
        return user_prompt
    complete = "帮我用" + model + "生成视频。" + _MARKER + user_prompt
    _validate_prompt(complete)
    return complete
