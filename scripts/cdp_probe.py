"""Read-only development probe for Doubao's local DOM."""
import json
import sys
from pathlib import Path
from doubao_mcp.cdp import CDP

client = CDP().open()
try:
    expression = Path(sys.argv[1]).read_text(encoding="utf-8") if len(sys.argv) > 1 else """(() => ({
      url: location.href,
      testids: [...new Set([...document.querySelectorAll('[data-testid]')].map(e=>e.dataset.testid))],
      editors: [...document.querySelectorAll('[contenteditable],textarea')].map(e=>({tag:e.tagName,cls:e.className,id:e.id,testid:e.dataset.testid,text:e.innerText})),
      links: [...document.querySelectorAll('[id^=conversation_]')].slice(0,5).map(e=>({tag:e.tagName,id:e.id,text:e.innerText,href:e.getAttribute('href')}))
    }))()"""
    data = client.evaluate(expression)
    print(json.dumps(data, ensure_ascii=False, indent=2))
finally:
    client.close()
