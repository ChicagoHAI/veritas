"""Local HTTP server for the review visualization."""

import json
import sys
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from . import __version__


VIZ_DIR = Path(__file__).parent / "viz"


class ReviewHandler(SimpleHTTPRequestHandler):
    """Custom handler that serves the viz UI and result data."""

    def __init__(self, *args, results_dir: str = "./review_results", **kwargs):
        self.results_dir = Path(results_dir)
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_index()
        elif self.path == "/data/index.json":
            self._serve_data_index()
        elif self.path.startswith("/data/") and self.path.endswith(".json"):
            slug = self.path[len("/data/"):-len(".json")]
            self._serve_paper_data(slug)
        else:
            self.send_error(404, "Not Found")

    def _serve_index(self):
        html_path = VIZ_DIR / "index.html"
        if not html_path.exists():
            self.send_error(500, "index.html not found in package")
            return
        html = html_path.read_text(encoding="utf-8")
        html = html.replace("<!-- __VERSION__ -->", f"v{__version__}")
        content = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_data_index(self):
        """Dynamically build index.json from result files in results_dir."""
        papers = []
        if self.results_dir.is_dir():
            for f in sorted(self.results_dir.glob("*.json")):
                try:
                    data = json.loads(f.read_text())
                    # Only include files that look like paper results
                    if "paragraphs" not in data or "methods" not in data:
                        continue
                    papers.append({
                        "slug": data.get("slug", f.stem),
                        "title": data.get("title", f.stem),
                    })
                except (json.JSONDecodeError, KeyError):
                    continue
        index = {"papers": papers}
        self._send_json(index)

    def _serve_paper_data(self, slug: str):
        """Serve a paper's result JSON."""
        json_path = self.results_dir / f"{slug}.json"
        if not json_path.exists():
            self.send_error(404, f"No results for: {slug}")
            return
        try:
            data = json.loads(json_path.read_text())
            self._send_json(data)
        except json.JSONDecodeError:
            self.send_error(500, f"Invalid JSON: {slug}.json")

    def _send_json(self, data: dict):
        content = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        # Quieter logging
        sys.stderr.write(f"[serve] {args[0]}\n")


def run_server(results_dir: str = "./review_results", port: int = 8080) -> None:
    """Start the visualization server."""
    results_path = Path(results_dir)
    if not results_path.is_dir():
        print(f"Warning: results directory does not exist: {results_path}")
        print("  Run 'openaireview review <file>' first to generate results.")

    handler = partial(ReviewHandler, results_dir=results_dir)
    server = HTTPServer(("0.0.0.0", port), handler)
    print(f"Serving review visualization at http://localhost:{port}")
    print(f"Results directory: {results_path.resolve()}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()
