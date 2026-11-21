#!/usr/bin/env python3
"""Review, extend, order, and cite a Scholarime reference candidate set."""

from __future__ import annotations

import argparse
import html
import json
import secrets
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from reference_import import ReferenceImportError, parse_import
from scholar_common import (
    Reference,
    citation_asset_status,
    clean_text,
    deduplicate_references,
    format_citations,
    utc_now,
)


VERSION = "1.0.0"
MAX_REQUEST_BYTES = 4 * 1024 * 1024


class ReviewError(ValueError):
    """A user-correctable review error."""


@dataclass
class ReviewState:
    references: list[Reference]
    output_path: Path
    skill_root: Path
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    done_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    final_payload: dict[str, Any] | None = None
    error_message: str = ""

    def state_payload(self) -> dict[str, Any]:
        return {
            "references": [reference.to_dict() for reference in self.references],
            "citation_styles": [
                {
                    "id": "gb-t-7714-2025",
                    "label": "GB/T 7714-2025（顺序编码）",
                },
                {"id": "apa", "label": "APA 第 7 版"},
            ],
            "citation_assets": citation_asset_status(self.skill_root),
            "output_path": str(self.output_path),
        }

    def replace(self, references: Sequence[Reference]) -> None:
        with self.lock:
            self.references = deduplicate_references(references)

    def add(self, references: Sequence[Reference]) -> None:
        with self.lock:
            self.references = deduplicate_references([*self.references, *references])

    def preview(self, style: str) -> dict[str, Any]:
        with self.lock:
            current = list(self.references)
        return format_citations(style, current)

    def confirm(self, references: Sequence[Reference], style: str) -> dict[str, Any]:
        unique = deduplicate_references(references)
        if not unique:
            raise ReviewError("At least one reference is required.")
        for reference in unique:
            if not reference.title:
                raise ReviewError("Every reference needs a title.")
        formatted = format_citations(style, unique)
        payload = {
            "version": VERSION,
            "generated_at": utc_now(),
            "citation_style": formatted["style"],
            "citation_assets": citation_asset_status(self.skill_root),
            "references": [reference.to_dict() for reference in unique],
            "citations": formatted["references"],
            "in_text": formatted["in_text"],
        }
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.output_path)
        with self.lock:
            self.references = unique
            self.final_payload = payload
        self.done_event.set()
        return payload


def load_candidates(path: Path | str) -> list[Reference]:
    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReviewError(f"Could not read candidates file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReviewError(f"Candidates file is invalid JSON: {exc}") from exc
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("results") or payload.get("references") or []
    else:
        items = []
    references = [
        Reference.from_dict(item, default_source="Scholarime candidate")
        for item in items
        if isinstance(item, dict)
    ]
    references = [reference for reference in references if reference.title]
    if not references:
        raise ReviewError("The candidates file contains no usable references.")
    return deduplicate_references(references)


def _manual_reference(payload: Mapping[str, Any]) -> Reference:
    title = clean_text(payload.get("title"))
    url = clean_text(payload.get("url"))
    if not title:
        raise ReviewError("A title is required for a manually added source.")
    authors = payload.get("authors") or []
    if isinstance(authors, str):
        authors = [part.strip() for part in authors.replace("；", ";").split(";")]
    return Reference(
        title=title,
        source="Manual entry",
        authors=[clean_text(author) for author in authors if clean_text(author)],
        year=payload.get("year"),
        container=clean_text(payload.get("container")),
        url=url,
        doi=clean_text(payload.get("doi")),
        item_type=clean_text(payload.get("item_type")) or "webpage",
        publisher=clean_text(payload.get("publisher")),
    )


def _html_page(state: ReviewState) -> str:
    token_json = json.dumps(state.token).replace("<", "\\u003c")
    title = html.escape(state.references[0].title if state.references else "Scholarime")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Scholarime Reference Review</title>
  <style>
    :root {{
      --ink: #1b2430;
      --muted: #65717d;
      --paper: #f5f3ee;
      --panel: #fffdf9;
      --line: #d8d2c7;
      --accent: #244f73;
      --danger: #8d3b32;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--paper);
      font-family: "Noto Serif SC", "Source Han Serif SC", "Times New Roman", serif;
    }}
    header {{
      padding: 28px clamp(20px, 5vw, 64px) 20px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    h1 {{ margin: 0 0 8px; font-size: clamp(24px, 3vw, 34px); font-weight: 600; }}
    .sub {{ color: var(--muted); margin: 0; line-height: 1.6; }}
    main {{
      display: grid;
      grid-template-columns: minmax(0, 1.6fr) minmax(300px, .8fr);
      gap: 24px;
      padding: 24px clamp(20px, 5vw, 64px) 80px;
      max-width: 1500px;
      margin: 0 auto;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 18px;
    }}
    h2 {{ margin: 0 0 14px; font-size: 20px; font-weight: 600; }}
    .toolbar {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }}
    button, select, input, textarea {{
      font: inherit;
      color: var(--ink);
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
    }}
    button {{ cursor: pointer; }}
    button.primary {{ color: #fff; background: var(--accent); border-color: var(--accent); }}
    button.danger {{ color: var(--danger); }}
    button:focus-visible, select:focus-visible, input:focus-visible, textarea:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}
    #list {{ list-style: none; padding: 0; margin: 18px 0 0; display: grid; gap: 12px; }}
    .card {{
      background: #fff;
      border: 1px solid var(--line);
      border-left: 5px solid var(--accent);
      border-radius: 8px;
      padding: 14px 16px;
      cursor: grab;
    }}
    .card.dragging {{ opacity: .45; }}
    .rank {{ color: var(--muted); font-size: 13px; margin-right: 8px; }}
    .ref-title {{ font-weight: 600; line-height: 1.5; }}
    .meta {{ color: var(--muted); font-size: 14px; line-height: 1.55; margin-top: 6px; }}
    .abstract {{ margin-top: 8px; color: #38434f; line-height: 1.65; font-size: 14px; }}
    details summary {{ cursor: pointer; color: var(--accent); margin-top: 8px; }}
    .card-actions {{ display: flex; gap: 8px; margin-top: 10px; }}
    .stack {{ display: grid; gap: 18px; align-content: start; }}
    .field {{ display: grid; gap: 6px; margin-bottom: 10px; }}
    .field label {{ font-size: 14px; color: var(--muted); }}
    .hint {{ color: var(--muted); font-size: 13px; line-height: 1.55; }}
    #preview {{ white-space: pre-wrap; line-height: 1.7; font-size: 14px; overflow-wrap: anywhere; }}
    .status {{ min-height: 22px; color: var(--muted); font-size: 14px; }}
    .warning {{ color: var(--danger); }}
    @media (max-width: 900px) {{
      main {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Scholarime Reference Review</h1>
    <p class="sub">拖动卡片调整顺序，删除无关来源，添加自己的资料或导入数据库文件。确认后窗口会关闭，最终顺序写入本地 JSON。</p>
  </header>
  <main>
    <section>
      <div class="toolbar">
        <strong>候选文献</strong>
        <span class="hint" id="count"></span>
        <span id="status" class="status"></span>
      </div>
      <ol id="list"></ol>
    </section>
    <div class="stack">
      <section>
        <h2>添加来源</h2>
        <form id="add-form">
          <div class="field"><label for="add-title">题名</label><input id="add-title" required></div>
          <div class="field"><label for="add-authors">作者，以分号分隔</label><input id="add-authors"></div>
          <div class="field"><label for="add-year">年份</label><input id="add-year" inputmode="numeric"></div>
          <div class="field"><label for="add-container">来源、机构或期刊</label><input id="add-container"></div>
          <div class="field"><label for="add-type">类型</label>
            <select id="add-type">
              <option value="webpage">网页</option>
              <option value="manual">技术手册</option>
              <option value="report">报告</option>
              <option value="book">图书</option>
              <option value="article">论文</option>
            </select>
          </div>
          <div class="field"><label for="add-url">URL</label><input id="add-url" type="url"></div>
          <div class="field"><label for="add-doi">DOI</label><input id="add-doi"></div>
          <button type="submit">添加</button>
        </form>
      </section>
      <section>
        <h2>导入数据库导出文件</h2>
        <p class="hint">支持 RIS、BibTeX、EndNote（.enw）和 RefWorks 文本；单个文件不超过 4 MiB。</p>
        <input id="import-files" type="file" multiple accept=".ris,.bib,.bibtex,.enw,.txt">
      </section>
      <section>
        <h2>引用格式</h2>
        <div class="field">
          <label for="style">输出样式</label>
          <select id="style">
            <option value="gb-t-7714-2025">GB/T 7714-2025（顺序编码）</option>
            <option value="apa">APA 第 7 版</option>
          </select>
        </div>
        <p id="asset-warning" class="hint warning"></p>
        <div class="toolbar">
          <button id="preview-button" type="button">预览</button>
          <button id="confirm-button" class="primary" type="button">确认并关闭</button>
        </div>
        <pre id="preview" class="hint"></pre>
      </section>
    </div>
  </main>
  <script>
    const TOKEN = {token_json};
    let references = [];
    let draggedId = null;

    async function api(path, options = {{}}) {{
      const headers = Object.assign(
        {{"Content-Type": "application/json", "X-Scholarime-Token": TOKEN}},
        options.headers || {{}}
      );
      const response = await fetch(path, {{...options, headers}});
      const payload = await response.json().catch(() => ({{error: "Invalid server response"}}));
      if (!response.ok) throw new Error(payload.error || response.statusText);
      return payload;
    }}

    function text(tag, value, className = "") {{
      const node = document.createElement(tag);
      node.textContent = value || "";
      if (className) node.className = className;
      return node;
    }}

    function render() {{
      const list = document.getElementById("list");
      list.replaceChildren();
      references.forEach((ref, index) => {{
        const item = document.createElement("li");
        item.className = "card";
        item.draggable = true;
        item.dataset.id = ref.record_id;

        const titleLine = document.createElement("div");
        titleLine.appendChild(text("span", String(index + 1), "rank"));
        titleLine.appendChild(text("span", ref.title, "ref-title"));
        item.appendChild(titleLine);

        const authors = Array.isArray(ref.authors) ? ref.authors.join("; ") : "";
        const meta = [authors, ref.year, ref.container, ref.source, ref.doi ? "DOI " + ref.doi : ""]
          .filter(Boolean).join(" · ");
        item.appendChild(text("div", meta, "meta"));

        if (ref.abstract) {{
          const details = document.createElement("details");
          details.appendChild(text("summary", "摘要"));
          details.appendChild(text("div", ref.abstract, "abstract"));
          item.appendChild(details);
        }}

        const actions = document.createElement("div");
        actions.className = "card-actions";
        const up = text("button", "上移");
        up.type = "button";
        up.addEventListener("click", () => move(index, index - 1));
        const down = text("button", "下移");
        down.type = "button";
        down.addEventListener("click", () => move(index, index + 1));
        const remove = text("button", "删除", "danger");
        remove.type = "button";
        remove.addEventListener("click", () => {{
          references.splice(index, 1);
          render();
        }});
        actions.append(up, down, remove);
        item.appendChild(actions);

        item.addEventListener("dragstart", () => {{
          draggedId = ref.record_id;
          item.classList.add("dragging");
        }});
        item.addEventListener("dragend", () => item.classList.remove("dragging"));
        item.addEventListener("dragover", event => event.preventDefault());
        item.addEventListener("drop", event => {{
          event.preventDefault();
          if (!draggedId || draggedId === ref.record_id) return;
          const from = references.findIndex(entry => entry.record_id === draggedId);
          const to = references.findIndex(entry => entry.record_id === ref.record_id);
          if (from < 0 || to < 0) return;
          const [moved] = references.splice(from, 1);
          references.splice(to, 0, moved);
          render();
        }});
        list.appendChild(item);
      }});
      document.getElementById("count").textContent = `${{references.length}} 条`;
    }}

    function move(from, to) {{
      if (to < 0 || to >= references.length) return;
      const [moved] = references.splice(from, 1);
      references.splice(to, 0, moved);
      render();
    }}

    async function refreshState() {{
      const payload = await api("/api/state");
      references = payload.references;
      const assets = payload.citation_assets || {{}};
      const missing = Object.entries(assets)
        .filter(([, info]) => !info.available)
        .map(([name]) => name);
      document.getElementById("asset-warning").textContent = missing.length
        ? "CSL 样式文件尚未安装；当前使用内置格式化器。"
        : "";
      render();
    }}

    document.getElementById("add-form").addEventListener("submit", async event => {{
      event.preventDefault();
      const payload = {{
        title: document.getElementById("add-title").value,
        authors: document.getElementById("add-authors").value,
        year: document.getElementById("add-year").value,
        container: document.getElementById("add-container").value,
        item_type: document.getElementById("add-type").value,
        url: document.getElementById("add-url").value,
        doi: document.getElementById("add-doi").value
      }};
      try {{
        await api("/api/add", {{method: "POST", body: JSON.stringify(payload)}});
        event.target.reset();
        await refreshState();
      }} catch (error) {{
        document.getElementById("status").textContent = error.message;
      }}
    }});

    document.getElementById("import-files").addEventListener("change", async event => {{
      const files = Array.from(event.target.files || []);
      for (const file of files) {{
        try {{
          const content = await file.text();
          await api("/api/import", {{
            method: "POST",
            body: JSON.stringify({{filename: file.name, content}})
          }});
        }} catch (error) {{
          document.getElementById("status").textContent = `${{file.name}}: ${{error.message}}`;
        }}
      }}
      event.target.value = "";
      await refreshState();
    }});

    document.getElementById("preview-button").addEventListener("click", async () => {{
      const style = document.getElementById("style").value;
      try {{
        await api("/api/order", {{
          method: "POST",
          body: JSON.stringify({{references}})
        }});
        const payload = await api("/api/preview", {{
          method: "POST",
          body: JSON.stringify({{style}})
        }});
        document.getElementById("preview").textContent =
          payload.references.join("\\n\\n") + "\\n\\n正文引用：\\n" + payload.in_text.join("  ");
      }} catch (error) {{
        document.getElementById("status").textContent = error.message;
      }}
    }});

    document.getElementById("confirm-button").addEventListener("click", async () => {{
      const style = document.getElementById("style").value;
      try {{
        document.getElementById("status").textContent = "正在保存…";
        await api("/api/confirm", {{
          method: "POST",
          body: JSON.stringify({{references, citation_style: style}})
        }});
        document.body.innerHTML =
          '<main style="grid-template-columns:1fr;max-width:700px;margin:80px auto">' +
          '<section><h2>引用资料已保存</h2><p>窗口即将关闭。</p></section></main>';
        setTimeout(() => window.close(), 300);
      }} catch (error) {{
        document.getElementById("status").textContent = error.message;
      }}
    }});

    refreshState().catch(error => {{
      document.getElementById("status").textContent = error.message;
    }});
  </script>
</body>
</html>
"""


class ReviewRequestHandler(BaseHTTPRequestHandler):
    server: "ReviewServer"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-Scholarime-Token", ""),
            self.server.state.token,
        )

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ReviewError("Request body is empty or exceeds 4 MiB.")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewError("Request body must be UTF-8 JSON.") from exc
        if not isinstance(payload, dict):
            raise ReviewError("Request body must be a JSON object.")
        return payload

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/":
            body = _html_page(self.server.state).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/api/state":
            if not self._authorised():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Invalid session token"})
            else:
                self._send_json(HTTPStatus.OK, self.server.state.state_payload())
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        if not self._authorised():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Invalid session token"})
            return
        path = urlsplit(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/state":
                self._send_json(HTTPStatus.OK, self.server.state.state_payload())
            elif path == "/api/add":
                self.server.state.add([_manual_reference(payload)])
                self._send_json(HTTPStatus.OK, {"status": "ok"})
            elif path == "/api/import":
                imported = parse_import(
                    clean_text(payload.get("filename")),
                    str(payload.get("content") or ""),
                )
                self.server.state.add(imported)
                self._send_json(HTTPStatus.OK, {"added": len(imported)})
            elif path == "/api/order":
                items = payload.get("references") or []
                if not isinstance(items, list):
                    raise ReviewError("references must be a list.")
                references = [
                    Reference.from_dict(item)
                    for item in items
                    if isinstance(item, dict)
                ]
                self.server.state.replace(references)
                self._send_json(HTTPStatus.OK, {"count": len(self.server.state.references)})
            elif path == "/api/preview":
                style = clean_text(payload.get("citation_style") or payload.get("style"))
                self._send_json(HTTPStatus.OK, self.server.state.preview(style))
            elif path == "/api/confirm":
                items = payload.get("references") or []
                if not isinstance(items, list):
                    raise ReviewError("references must be a list.")
                references = [
                    Reference.from_dict(item)
                    for item in items
                    if isinstance(item, dict)
                ]
                final = self.server.state.confirm(
                    references,
                    clean_text(payload.get("citation_style")),
                )
                self._send_json(HTTPStatus.OK, final)
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except (ReviewError, ReferenceImportError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self.server.state.error_message = str(exc)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal review error"})


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: ReviewState) -> None:
        super().__init__(address, ReviewRequestHandler)
        self.state = state


def serve_review(
    state: ReviewState,
    host: str = "127.0.0.1",
    port: int = 0,
    open_window: bool = True,
) -> dict[str, Any] | None:
    server = ReviewServer((host, port), state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://{host}:{server.server_port}/"

    window = None
    if open_window:
        try:
            import webview

            window = webview.create_window(
                "Scholarime Reference Review",
                url,
                width=1180,
                height=820,
                min_size=(760, 560),
            )
            threading.Thread(
                target=lambda: (
                    state.done_event.wait(),
                    window.destroy(),
                ),
                daemon=True,
            ).start()
            webview.start()
        except ImportError:
            print(
                "warning: pywebview is unavailable; opening the system browser. "
                "The tab may require manual closing.",
                file=sys.stderr,
            )
            webbrowser.open(url)
            state.done_event.wait()
    else:
        state.done_event.wait()

    server.shutdown()
    server.server_close()
    return state.final_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reference_review.py",
        description="Open a local WebUI to curate and cite a Scholarime candidate set.",
    )
    parser.add_argument("--input", type=Path, required=True, help="Candidate JSON file.")
    parser.add_argument("--output", type=Path, help="Final JSON path.")
    parser.add_argument("--skill-root", type=Path, help="Scholarime skill root.")
    parser.add_argument("--port", type=int, default=0, help="Loopback port; 0 chooses one.")
    parser.add_argument("--no-window", action="store_true", help="Start the server without a window.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        references = load_candidates(args.input)
        output = (
            args.output.resolve()
            if args.output
            else Path(args.input).expanduser().resolve().with_name("final_references.json")
        )
        skill_root = (
            args.skill_root.resolve()
            if args.skill_root
            else Path(__file__).resolve().parents[1]
        )
        state = ReviewState(
            references=references,
            output_path=output,
            skill_root=skill_root,
        )
        final = serve_review(state, port=args.port, open_window=not args.no_window)
    except (ReviewError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if final is None:
        print("Review ended without a confirmed result.", file=sys.stderr)
        return 1
    print(f"Final references: {output}")
    print(f"Citation style: {final['citation_style']}")
    print(f"Reference count: {len(final['references'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
