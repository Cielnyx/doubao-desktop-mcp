"""Switch only through the client's saved-account menu and verify the new identity."""
from __future__ import annotations

import re
import time

from websockets.exceptions import ConnectionClosed

from .background import BackgroundError
from .cdp import CDPError


class AccountDriver:
    def __init__(self, driver):
        self.driver = driver

    def list(self):
        return self.driver.page("account_list")

    def switch(self, account_id=None, nickname=None, timeout=30):
        if (account_id is None) == (nickname is None):
            raise BackgroundError("INVALID_ACCOUNT_TARGET", "account_id 与 nickname 必须且只能提供一个。")
        if account_id is not None and (not isinstance(account_id, str) or not re.fullmatch(r"\d{1,30}", account_id)):
            raise BackgroundError("INVALID_ACCOUNT_TARGET", "account_id 必须为列表返回的数字字符串。")
        if nickname is not None and (not isinstance(nickname, str) or not nickname.strip() or len(nickname) > 200):
            raise BackgroundError("INVALID_ACCOUNT_TARGET", "nickname 必须为完整的非空昵称，最多200字符。")
        if type(timeout) is not int or not 5 <= timeout <= 60:
            raise BackgroundError("INVALID_TIMEOUT", "timeout 必须为5–60秒整数。")
        accounts = self.list()
        matches = [p for p in accounts["accounts"] if
                   (p["account_id"] == account_id if account_id is not None else p["nickname"] == nickname)]
        if not matches:
            raise BackgroundError("ACCOUNT_NOT_FOUND", "未找到已添加的目标账号；新账号需先在豆包完成登录。",
                                  accounts=accounts["accounts"])
        if len(matches) != 1:
            raise BackgroundError("AMBIGUOUS_ACCOUNT", "昵称重名，请使用 account_id。", candidates=matches)
        target = matches[0]
        previous = accounts["current_account"]
        if target["account_id"] == previous["account_id"]:
            return {"ok":True, "background":True, "switched":False, "already_active":True,
                    "switch_attempted":False, "current_account":previous}
        check = self.driver.page("account_switch_preflight", account_id=target["account_id"])
        proof = check.get("proof")
        if (not isinstance(proof, dict) or proof.get("account_id") != target["account_id"] or
                proof.get("from_account_id") != previous["account_id"] or not isinstance(proof.get("route"),str)):
            raise BackgroundError("ACCOUNT_CONTEXT_CHANGED", "核验期间账号发生变化，未切换。")
        uncertain = None
        try:
            result = self.driver.page("account_switch_once", account_id=target["account_id"], proof=proof)
            if not isinstance(result, dict) or not result.get("clicked"):
                uncertain = "未收到完整的账号切换点击回执。"
                self.driver.close()
        except BackgroundError as error:
            if error.code in {"GENERATING", "DRAFT_PRESENT", "ATTACHMENT_PRESENT", "ACCOUNT_CONTEXT_CHANGED",
                              "ACCOUNT_SWITCH_UNVERIFIED", "ACCOUNT_SWITCH_BUSY", "ACCOUNT_MENU_UNAVAILABLE",
                              "ACCOUNT_ROW_UNAVAILABLE", "ACCOUNT_IDENTITY_UNAVAILABLE"}:
                error.details.setdefault("switch_attempted", False)
                raise  # Known renderer guards fail before clicking.
            uncertain = str(error)
            self.driver.close()
        except (CDPError, ConnectionClosed, OSError, TimeoutError) as error:
            uncertain = str(error)  # A document reload may interrupt the acknowledgment.
            self.driver.close()
        deadline = time.monotonic() + timeout
        last = {}
        while time.monotonic() < deadline:
            try:
                last = self.driver.page("account_observe")
                current = last.get("current_account")
                if last.get("login_required"):
                    return {"ok":False, "code":"ACCOUNT_LOGIN_REQUIRED", "background":True,
                            "switch_attempted":True, "no_retry":True, "target_account":target,
                            "current_account":current, "message":"目标账号需要重新登录，请在豆包完成扫码或验证码；未自动重试。"}
                if (current and current.get("account_id") == target["account_id"] and
                        last.get("editor_available") and not last.get("dialog_present")):
                    return {"ok":True, "background":True, "switched":True, "switch_attempted":True,
                            "previous_account":previous, "current_account":current,
                            "identity_verified":True, "no_retry":True}
            except (BackgroundError, CDPError, ConnectionClosed, OSError, TimeoutError) as error:
                uncertain = str(error)
                self.driver.close()  # Poll a replacement renderer without clicking again.
            time.sleep(.4)
        return {"ok":False, "code":"ACCOUNT_SWITCH_UNCONFIRMED", "background":True,
                "switch_attempted":True, "no_retry":True, "target_account":target,
                "current_account":last.get("current_account"), "reason":uncertain,
                "message":"已尝试切换，但未能确认目标账号就绪；请先列出账号检查当前身份，不要自动重试。"}
