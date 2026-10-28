"""Subprocess dispatcher. Every MCP action uses the background-only driver."""
import json
import sys

from .background import BackgroundDoubao, BackgroundError
from .cdp import CDPError
from .locking import desktop_lock
from .media import MediaDriver, MediaError
from .tasks import TaskError
from .downloads import DownloadError
from .workflow import VideoWorkflow
from .prompts import PromptError


def main():
    driver = None
    request = {}
    try:
        if sys.platform != "win32":
            raise BackgroundError("UNSUPPORTED_PLATFORM", "此 MCP 仅支持 Windows 豆包桌面端。")
        request = json.load(sys.stdin)
        with desktop_lock():
            driver = BackgroundDoubao()
            action = request.get("action")
            if action in ("status", "inspect", "new_chat", "read_reply", "video_options", "video_exit"):
                result = getattr(driver, action)()
            elif action == "video_configure":
                result = driver.video_configure(request.get("model"), request.get("ratio"), request.get("duration"))
            elif action == "image_upload":
                result = MediaDriver(driver).upload(request.get("paths"), request.get("timeout",90))
            elif action == "image_bind":
                result = MediaDriver(driver).bind(request.get("asset_id"), request.get("label"), request.get("timeout",10))
            elif action == "media_inspect":
                result = MediaDriver(driver).inspect()
            elif action == "video_prepare":
                result = VideoWorkflow(driver).prepare(request.get("prompt"), request.get("image_asset_ids",[]), request.get("model"))
            elif action == "video_submit":
                result = VideoWorkflow(driver).submit(request.get("request_id"), request.get("prompt"), request.get("model"),
                    request.get("ratio"), request.get("duration"), request.get("image_asset_ids"), request.get("wait_seconds",15))
            elif action == "video_status":
                result = VideoWorkflow(driver).status(request.get("request_id"),request.get("conversation_id"),
                    request.get("message_id"),request.get("task_id"))
            elif action == "video_confirm":
                result = VideoWorkflow(driver).confirm(request.get("request_id"))
            elif action == "video_download":
                result = VideoWorkflow(driver).download(request.get("directory"),request.get("request_id"),
                    request.get("conversation_id"),request.get("message_id"),request.get("task_id"),
                    request.get("filename"),request.get("video_index",0),request.get("timeout",180))
            elif action == "ask":
                result = driver.ask(request["prompt"], request.get("new_chat", False), request.get("timeout", 120),
                                    request.get("conversation_id"), request.get("title"))
            elif action == "list_conversations":
                result = driver.list_conversations(request.get("query", ""), request.get("limit", 50),
                                                   request.get("offset", 0), request.get("load_more", True))
            elif action == "open_conversation":
                result = driver.open_conversation(request.get("conversation_id"), request.get("title"))
            elif action == "read_history":
                result = driver.read_history(request.get("conversation_id"), request.get("title"),
                                             request.get("limit", 20), request.get("offset", 0), request.get("max_chars", 6000))
            else:
                raise BackgroundError("UNKNOWN_ACTION", "不支持的操作。")
    except (BackgroundError, MediaError, TaskError, DownloadError, PromptError) as error:
        result = {"ok": False, "code": error.code, "message": str(error),
                  "sent": bool(driver and driver.sent), **error.details}
    except CDPError as error:
        result = {"ok": False, "code": "BACKGROUND_ERROR", "message": str(error),
                  "sent": bool(driver and driver.sent)}
    except Exception as error:
        result = {"ok": False, "code": "DRIVER_ERROR", "message": str(error),
                  "sent": bool(driver and driver.sent)}
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass
    if isinstance(request, dict) and request.get("request_id"):
        result.setdefault("request_id", request["request_id"])
        if request.get("action") in {"video_submit", "video_confirm"}:
            result.setdefault("no_resend", True)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
