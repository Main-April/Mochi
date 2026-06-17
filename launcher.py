"""Lance Mochi Agent dans une fenêtre native."""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from server import app
import webview

PORT = 8000
server = None


def start_server():
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


def main():
    global server
    t = threading.Thread(target=start_server, daemon=True)
    t.start()

    window = webview.create_window(
        title="Mochi Agent",
        url=f"http://127.0.0.1:{PORT}",
        width=900,
        height=700,
        min_size=(600, 400),
        resizable=True,
        text_select=True,
    )
    window.events.closed += sys.exit
    webview.start(private_mode=False)


if __name__ == "__main__":
    main()
