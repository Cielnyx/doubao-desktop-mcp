"""Test formal video MCP tools against an existing completed task; never generate.

--closed-start closes only the read-only Images and Videos tab before invoking
the default download. Run from a native Windows shell with another app focused
to verify that background operations do not activate Doubao.
"""
import argparse
import asyncio
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import parse_qs, urlparse
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import win32gui

from doubao_mcp.background import BackgroundDoubao
from doubao_mcp.locking import desktop_lock
from smoke_mcp import payload


def doubao_handles():
    result=[]
    def visit(handle, _):
        title=win32gui.GetWindowText(handle)
        if title == "豆包" or title.endswith(" - 豆包"):
            result.append(handle)
    win32gui.EnumWindows(visit,None)
    return result


def close_readonly_viewer():
    with desktop_lock():
        driver=BackgroundDoubao()
        try:
            driver.page("check_idle")
            point=driver.client.evaluate('''(()=>{
              const p=document.querySelector('[data-testid="right-panel-container"]');
              if(!p?.querySelector('video'))return null;
              const editing=[...p.querySelectorAll('[contenteditable="true"],textarea,input:not([type="range"]):not([type="button"]):not([type="hidden"])')].some(e=>e.getClientRects().length);
              if(editing)throw Error('Editable panel present; viewer was not closed.');
              const b=p.querySelector('button[aria-label="关闭标签页：图片与视频"]');
              if(!b)throw Error('Read-only media tab close control unavailable.');
              const r=b.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
              if(!r.width||!r.height||!b.contains(document.elementFromPoint(x,y)))throw Error('Close control is blocked.');
              return {x,y};})()''')
            if point:
                for kind in ("mouseMoved","mousePressed","mouseReleased"):
                    args={"type":kind,**point}
                    if kind != "mouseMoved":args.update(button="left",clickCount=1)
                    driver.client.call("Input.dispatchMouseEvent",args)
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                if not driver.client.evaluate('!!document.querySelector(\'[data-testid="right-panel-container"] video\')'):
                    return True
                time.sleep(.1)
            raise AssertionError("Media panel did not close; no test download attempted.")
        finally:
            driver.close()


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("conversation-id","message-id","task-id","directory"):
        parser.add_argument("--"+name,required=True)
    parser.add_argument("--closed-start",action="store_true")
    parser.add_argument("--evidence",default=".runtime/detail-smoke.json")
    options=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    directory=Path(options.directory)
    assert directory.is_absolute(), "directory must be absolute"
    assert win32gui.GetForegroundWindow() not in doubao_handles(), "Start from another app to verify background operation."
    observations=[]
    done=asyncio.Event()

    async def watch():
        while not done.is_set():
            observations.append(win32gui.GetForegroundWindow() in doubao_handles())
            await asyncio.sleep(.05)

    watcher=asyncio.create_task(watch())
    evidence={"closed_start_requested":options.closed_start,"generation_submitted":False}
    try:
        if options.closed_start:
            evidence["closed_before"]=await asyncio.to_thread(close_readonly_viewer)
        params=StdioServerParameters(command=sys.executable,args=["-m","doubao_mcp"],cwd=str(root),
            env={**os.environ,"PYTHONUTF8":"1","PYTHONIOENCODING":"utf-8"})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=360)) as session:
                await session.initialize()
                names={t.name for t in (await session.list_tools()).tools}
                assert len(names)==22 and {"doubao_video_open_detail","doubao_video_download"}<=names,names
                target={"conversation_id":options.conversation_id,"message_id":options.message_id,"task_id":options.task_id}
                filename="detail-smoke-"+uuid.uuid4().hex[:12]+".mp4"
                receipt=payload(await session.call_tool("doubao_video_download",{**target,"directory":str(directory),"filename":filename}))
                evidence["download"]=receipt
                assert receipt.get("downloaded") and receipt.get("identity_verified"),receipt
                assert receipt.get("url_lr")=="unwatermarked" and parse_qs(urlparse(receipt["source_url"]).query).get("lr")==["unwatermarked"]
                assert all(receipt[key]==value for key,value in target.items()),receipt
                assert hashlib.sha256(Path(receipt["path"]).read_bytes()).hexdigest()==receipt["sha256"]
                opened=payload(await session.call_tool("doubao_video_open_detail",target))
                evidence["open_detail"]=opened
                assert opened.get("identity_verified") and opened.get("already_open"),opened
                assert all(opened[key]==value for key,value in target.items()),opened
                evidence["tool_count"]=len(names)
        assert observations and not any(observations), "Doubao became foreground during the test."
        evidence["passed"]=True
    finally:
        done.set()
        await watcher
        evidence["native_monitor"]={"samples":len(observations),"doubao_foreground_observed":any(observations)}
        output=Path(options.evidence)
        if not output.is_absolute():output=root/output
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"passed":True,"tools":evidence["tool_count"],"closed_before":evidence.get("closed_before"),
        "downloaded":True,"url_lr":receipt["url_lr"],"bytes":receipt["bytes"],"sha256":receipt["sha256"],
        "foreground_samples":len(observations),"doubao_activated":False},ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
