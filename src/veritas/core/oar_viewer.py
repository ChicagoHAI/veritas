"""Render OpenAIReview's viewer as a single self-contained HTML artifact.

The vendored viewer (``review_engine/viz/index.html``) normally fetches its data
from ``/data/index.json`` + ``/data/<slug>.json`` (server or sibling files). For
a shareable, server-less artifact we embed the data in the page and install a
tiny ``fetch`` shim that serves those URLs from the embedded blob — no edits to
the viewer's own logic, so it stays a faithful vendored copy.

Input is the OpenAIReview-viz JSON dict (``review_bundle.to_oar_viz``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def _viewer_html_path() -> Path:
    return Path(__file__).resolve().parent.parent / "review_engine" / "viz" / "index.html"


def render_oar_viewer(viz_json: Dict[str, Any]) -> str:
    """Return a standalone HTML string with ``viz_json`` embedded."""
    html = _viewer_html_path().read_text(encoding="utf-8")
    slug = viz_json.get("slug", "paper")
    index = {"papers": [{"slug": slug, "title": viz_json.get("title", slug)}]}
    embed = {
        "/data/index.json": index,
        "data/index.json": index,
        f"/data/{slug}.json": viz_json,
        f"data/{slug}.json": viz_json,
    }
    shim = (
        "<script>window.__EMBED__=" + json.dumps(embed, ensure_ascii=False) + ";"
        "(function(){var f=window.fetch;window.fetch=function(u){"
        "var k=String(u);for(var key in window.__EMBED__){"
        "if(k===key||k.endsWith(key)){return Promise.resolve("
        "{ok:true,json:function(){return Promise.resolve(window.__EMBED__[key]);}});}}"
        "return f?f.apply(this,arguments):Promise.reject(new Error('no fetch'));};})();</script>"
    )
    # Inject before </head> so the shim is installed before the viewer's init runs.
    if "</head>" in html:
        return html.replace("</head>", shim + "\n</head>", 1)
    return shim + html
