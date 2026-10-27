"""Verify direct ID navigation when the target has no sidebar DOM anchor."""
import asyncio
import json
from pathlib import Path

import win32gui
from doubao_mcp.bridge import invoke
from doubao_mcp.cdp import CDP
from doubao_mcp.native_probe import NativeDoubaoProbe


async def main():
    root=Path(__file__).resolve().parents[1]
    fixture=json.loads((root/".runtime/background-smoke.json").read_text(encoding="utf-8"))
    target=fixture["test_conversation_id"]
    assert target.isdigit()
    native=NativeDoubaoProbe()
    native.connect()
    handle=native.window.handle
    assert win32gui.IsIconic(handle), "Run after minimized acceptance"
    client=CDP().open()
    try:
        # This changes only a temporary DOM ID, never account/history data.
        changed=client.evaluate("""(() => {const e=document.getElementById(%s);
          if(e){e.id='mcp-test-detached-anchor';return true;}return false;})()""" % json.dumps("conversation_"+target))
    finally:
        client.close()
    observations=[]
    stop=asyncio.Event()
    async def watch():
        while not stop.is_set():
            observations.append((win32gui.GetForegroundWindow()!=handle,bool(win32gui.IsIconic(handle))))
            await asyncio.sleep(0.05)
    watcher=asyncio.create_task(watch())
    try:
        opened=await invoke("open_conversation",conversation_id=target)
        assert opened["ok"] and opened["conversation_id"]==target, opened
        history=await invoke("read_history",limit=20)
        assert history["ok"] and len(history["messages"])==4, history
    finally:
        stop.set()
        await watcher
        client=CDP().open()
        try:
            client.evaluate("""(() => {const e=document.getElementById('mcp-test-detached-anchor');
              if(e)e.id=%s;return true;})()""" % json.dumps("conversation_"+target))
        finally:
            client.close()
    assert all(not_active and minimized for not_active,minimized in observations)
    evidence={"ok":True,"forced_direct_route":changed,"conversation_id":target,
              "history_messages":len(history["messages"]),"doubao_not_activated":True,
              "doubao_still_minimized":bool(win32gui.IsIconic(handle))}
    (root/".runtime/direct-id-smoke.json").write_text(json.dumps(evidence,indent=2),encoding="utf-8")
    print(json.dumps(evidence,ensure_ascii=False))


if __name__=="__main__":
    asyncio.run(main())
