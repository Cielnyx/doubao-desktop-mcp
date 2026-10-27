"""Read-only native checks for bootstrap and acceptance, never imported by MCP."""
from .messages import parse_messages


class NativeProbeError(RuntimeError):
    pass


class NativeDoubaoProbe:
    def __init__(self):
        import psutil
        from pywinauto import Desktop
        self.psutil = psutil
        self.desktop = Desktop(backend="uia")
        self.window = None

    def connect(self):
        processes = {p.pid for p in self.psutil.process_iter(["name"])
                     if (p.info["name"] or "").lower() == "doubao.exe"}
        windows = list({w.handle:w for w in self.desktop.windows()
                        if w.process_id() in processes and
                        (w.window_text() == "豆包" or w.window_text().endswith(" - 豆包"))}.values())
        if len(windows) != 1:
            raise NativeProbeError("请在桌面打开豆包，并只保留一个主窗口。")
        self.window = windows[0]

    def draft(self):
        containers = [e for e in self.window.descendants(control_type="Group")
                      if e.element_info.automation_id == "input-engine-container"]
        editors = [e for c in containers for e in c.descendants(control_type="Group")
                   if "ProseMirror" in e.element_info.class_name.split()]
        editors = list({tuple(e.element_info.runtime_id):e for e in editors}.values())
        if len(editors) != 1:
            raise NativeProbeError("无法确认豆包草稿状态，停止操作。请打开普通对话页面。")
        text = editors[0].iface_text.DocumentRange.GetText(-1).strip()
        return "" if text in ("发消息或按住空格说话...", "发消息...") else text

    def check_idle(self):
        import re
        if self.draft():
            raise NativeProbeError("输入框中有草稿，停止操作，避免丢失内容。")
        if any(re.fullmatch(r"停止(生成|回答|输出)?|Stop( generating)?", e.window_text(), re.I)
               for e in self.window.descendants(control_type="Button")):
            raise NativeProbeError("豆包正在生成，停止操作。")
        for panel in self.window.descendants(control_type="Group", title="doc_editor"):
            messages = parse_messages([{"type":e.element_info.control_type,"name":e.window_text()} for e in panel.children()])
            if messages and messages[-1].role != "assistant":
                raise NativeProbeError("豆包有未完成回复，停止操作。")
