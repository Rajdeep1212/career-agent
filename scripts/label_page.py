"""Serve the local labelling page for one batch (docs/ROADMAP_QUEUE.md Q3).

Usage (from the repository root, in the app's Python environment):
    python scripts/label_page.py --batch data/eval/batches/<batch id>

Then open the address it prints. Keys 0-3 label the job on screen and move on; Left arrow or B goes
back one job; a refresh or a restart continues where you stopped. Labels are appended to
data/eval/labels/<batch id>.jsonl. Stop the server with Ctrl+C.

    python scripts/label_page.py --batch data/eval/batches/<batch id> --check

--check prints how many jobs were labelled in under 8 seconds and whether the file counts as complete, and exits
with 1 when it does not. The same lines are printed when the server stops.

It listens on this machine only and loads nothing from the network. See app/eval/label_page.py.
"""
import argparse
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.eval.label_page import LabelSession, handle  # noqa: E402

ADDRESS = "127.0.0.1"       # this machine only
MAX_BODY = 10_000


def serve(session: LabelSession, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, method: str) -> None:
            length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
            response = handle(session, method, self.path, dict(self.headers.items()), self.rfile.read(length) if length else b"",
                              port=port)
            self.send_response(response.status)
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def do_GET(self) -> None:
            self._reply("GET")

        def do_POST(self) -> None:
            self._reply("POST")

        def log_message(self, *arguments) -> None:
            pass        # listing text and labels stay out of the terminal

    server = HTTPServer((ADDRESS, port), Handler)
    state = session.state()
    print(f"Batch {session.batch_id}: {state['labelled']} of {state['total']} labelled. Labels: {session.path}")
    print(f"Open http://{ADDRESS}:{port}/ in your browser. Ctrl+C stops the server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the local labelling page for one batch.")
    parser.add_argument("--batch", required=True, type=Path, help="a folder made by scripts/build_label_batch.py")
    parser.add_argument("--labels", type=Path, default=Path(settings.data_dir) / "eval" / "labels")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--check", action="store_true", help="print the quality check of the label file and exit")
    args = parser.parse_args(argv)
    session = LabelSession(args.batch, args.labels)
    if not args.check:
        serve(session, args.port)
    quality = session.quality()
    print(f"{session.batch_id}: {quality['labelled']} of {quality['total']} labelled; {quality['summary']}.")
    print(quality["message"])
    return 0 if quality["accepted"] or not args.check else 1


if __name__ == "__main__":
    raise SystemExit(main())
