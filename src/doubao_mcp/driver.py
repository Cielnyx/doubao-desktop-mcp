"""Subprocess dispatcher. Every MCP action uses the background-only driver."""
import json
import sys

from .background import BackgroundDoubao, BackgroundError
from .cdp import CDPError
from .locking import desktop_lock


def main():
    driver = None
    try:
        if sys.platform != "win32":
            raise BackgroundError("UNSUPPORTED_PLATFORM", "此 MCP 仅支持 Windows 豆包桌面端。")
        request = json.load(sys.stdin)
        with desktop_lock():
            driver = BackgroundDoubao()
            action = request.get("action")
            if action in ("status", "inspect", "new_chat", "read_reply"):
                result = getattr(driver, action)()
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
    except BackgroundError as error:
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
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
