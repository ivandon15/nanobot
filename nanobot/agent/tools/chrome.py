"""Chrome browser tool via CDP (Chrome DevTools Protocol)."""
import json
import re
import socket
import time
import urllib.parse
from typing import Any

from nanobot.agent.tools.base import Tool


class ChromeTool(Tool):
    """Control Chrome browser via CDP for web browsing and search."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9222):
        self._host = host
        self._port = port

    @property
    def name(self) -> str:
        return "chrome"

    @property
    def description(self) -> str:
        return (
            "Control Chrome browser via CDP. "
            "Actions: navigate (go to URL and wait for load), "
            "get_content (get current page text), "
            "search (search Google and return results), "
            "evaluate (run JavaScript on current page), "
            "screenshot (capture page as PNG file). "
            "Requires Chrome running with --remote-debugging-port=9222."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["navigate", "get_content", "search", "evaluate", "screenshot"],
                    "description": "Operation to perform",
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to (for navigate action)",
                },
                "query": {
                    "type": "string",
                    "description": "Search query (for search action)",
                },
                "expression": {
                    "type": "string",
                    "description": "JavaScript expression to evaluate (for evaluate action)",
                },
                "wait": {
                    "type": "number",
                    "description": "Seconds to wait after navigation (default: 2)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, url: str = "", query: str = "",
                      expression: str = "", wait: float = 2.0, **kwargs: Any) -> str:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._run, action, url, query, expression, wait)

    def _is_chrome_running(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            try:
                s.connect((self._host, self._port))
                return True
            except (ConnectionRefusedError, socket.timeout, OSError):
                return False

    def _ensure_chrome(self) -> None:
        """Launch Chrome with remote debugging if not already running."""
        if self._is_chrome_running():
            return
        import platform
        import subprocess
        import os
        system = platform.system()
        if system == "Darwin":
            chrome_candidates = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
            ]
        elif system == "Linux":
            chrome_candidates = ["google-chrome", "chromium-browser", "chromium"]
        else:
            raise RuntimeError(
                f"Auto-launch not supported on {system}. "
                f"Start Chrome manually with --remote-debugging-port={self._port}"
            )

        import shutil
        chrome_path = None
        for c in chrome_candidates:
            if c.startswith("/"):
                if os.path.isfile(c):
                    chrome_path = c
                    break
            else:
                found = shutil.which(c)
                if found:
                    chrome_path = found
                    break

        if not chrome_path:
            raise RuntimeError(
                "Chrome not found. Install Google Chrome or start it manually with "
                f"--remote-debugging-port={self._port}"
            )

        # Use a dedicated user-data-dir so Chrome doesn't reuse an existing instance
        user_data_dir = os.path.expanduser("~/.nanobot/chrome-profile")
        os.makedirs(user_data_dir, exist_ok=True)

        subprocess.Popen(
            [
                chrome_path,
                f"--remote-debugging-port={self._port}",
                f"--user-data-dir={user_data_dir}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait up to 10s for Chrome to be ready
        deadline = time.time() + 10
        while time.time() < deadline:
            if self._is_chrome_running():
                return
            time.sleep(0.5)

        raise RuntimeError(f"Chrome started but port {self._port} not responding after 10s")

    def _connect_tab(self) -> Any:
        import requests
        import websockets.sync.client as ws_client

        resp = requests.get(f"http://{self._host}:{self._port}/json", timeout=5)
        resp.raise_for_status()
        targets = resp.json()
        pages = [t for t in targets if t.get("type") == "page"]

        ws_url = None
        if pages:
            ws_url = pages[0]["webSocketDebuggerUrl"]
        else:
            # Create a new tab
            r = requests.put(f"http://{self._host}:{self._port}/json/new", timeout=5)
            if r.ok:
                ws_url = r.json().get("webSocketDebuggerUrl")

        if not ws_url:
            raise RuntimeError("No Chrome tabs available")

        return ws_client.connect(ws_url)

    def _send(self, ws: Any, method: str, params: dict | None = None, msg_id: list | None = None) -> dict:
        if msg_id is None:
            msg_id = [0]
        msg_id[0] += 1
        msg: dict = {"id": msg_id[0], "method": method}
        if params:
            msg["params"] = params
        ws.send(json.dumps(msg))
        while True:
            raw = ws.recv()
            data = json.loads(raw)
            if data.get("id") == msg_id[0]:
                if "error" in data:
                    raise RuntimeError(f"CDP error: {data['error']}")
                return data.get("result", {})

    def _evaluate(self, ws: Any, expression: str, msg_id: list) -> Any:
        result = self._send(ws, "Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        }, msg_id)
        remote_obj = result.get("result", {})
        if remote_obj.get("subtype") == "error":
            raise RuntimeError(f"JS error: {remote_obj.get('description', remote_obj)}")
        return remote_obj.get("value")

    def _run(self, action: str, url: str, query: str, expression: str, wait: float) -> str:
        try:
            self._ensure_chrome()
        except Exception as e:
            return f"Error: {e}"

        msg_id = [0]
        ws = None
        try:
            ws = self._connect_tab()

            if action == "navigate":
                if not url:
                    return "Error: url required for navigate"
                self._send(ws, "Page.enable", msg_id=msg_id)
                self._send(ws, "Page.navigate", {"url": url}, msg_id)
                time.sleep(wait)
                current_url = self._evaluate(ws, "window.location.href", msg_id)
                title = self._evaluate(ws, "document.title", msg_id)
                return json.dumps({"url": current_url, "title": title})

            elif action == "get_content":
                text = self._evaluate(ws, """
                    (function() {
                        var clone = document.body ? document.body.cloneNode(true) : null;
                        if (!clone) return '';
                        clone.querySelectorAll('script,style,noscript,nav,footer,header').forEach(
                            function(el) { el.remove(); }
                        );
                        return (clone.innerText || clone.textContent || '').trim();
                    })()
                """, msg_id)
                title = self._evaluate(ws, "document.title", msg_id)
                current_url = self._evaluate(ws, "window.location.href", msg_id)
                text = re.sub(r'[ \t]+', ' ', text or '')
                text = re.sub(r'\n{3,}', '\n\n', text).strip()
                return json.dumps({
                    "url": current_url,
                    "title": title,
                    "content": text[:20000],
                    "truncated": len(text) > 20000,
                }, ensure_ascii=False)

            elif action == "search":
                if not query:
                    return "Error: query required for search"
                search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&hl=zh-CN"
                self._send(ws, "Page.enable", msg_id=msg_id)
                self._send(ws, "Page.navigate", {"url": search_url}, msg_id)
                time.sleep(wait)
                results = self._evaluate(ws, """
                    (function() {
                        var results = [];
                        var seen = new Set();
                        document.querySelectorAll('h3').forEach(function(h3) {
                            var link = h3.closest('a');
                            if (!link) {
                                var p = h3.parentElement;
                                while (p && p.tagName !== 'BODY') {
                                    var a = p.querySelector('a[href^="http"]');
                                    if (a) { link = a; break; }
                                    p = p.parentElement;
                                }
                            }
                            if (!link) return;
                            var href = link.href;
                            if (!href || href.includes('google.com') || seen.has(href)) return;
                            seen.add(href);
                            var container = h3.closest('[data-sokoban-container]') ||
                                            h3.closest('.g') || h3.parentElement;
                            var snippet = '';
                            if (container) {
                                var spans = container.querySelectorAll('span');
                                for (var i = 0; i < spans.length; i++) {
                                    var t = spans[i].textContent.trim();
                                    if (t.length > 40 && t !== h3.textContent.trim()) {
                                        snippet = t.substring(0, 250);
                                        break;
                                    }
                                }
                            }
                            results.push({
                                title: h3.textContent.trim(),
                                url: href,
                                snippet: snippet
                            });
                        });
                        return results.slice(0, 10);
                    })()
                """, msg_id)
                return json.dumps({"query": query, "results": results or []}, ensure_ascii=False)

            elif action == "evaluate":
                if not expression:
                    return "Error: expression required for evaluate"
                result = self._evaluate(ws, expression, msg_id)
                return json.dumps({"result": result}, ensure_ascii=False, default=str)

            elif action == "screenshot":
                result = self._send(ws, "Page.captureScreenshot", {"format": "png"}, msg_id)
                data = result.get("data", "")
                import base64
                import tempfile
                import os
                tmp = tempfile.NamedTemporaryFile(
                    suffix=".png", delete=False, prefix="chrome_screenshot_"
                )
                tmp.write(base64.b64decode(data))
                tmp.close()
                return json.dumps({
                    "screenshot_path": tmp.name,
                    "size_bytes": os.path.getsize(tmp.name),
                })

            else:
                return f"Error: unknown action '{action}'"

        except Exception as e:
            return f"Error: {e}"
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass
