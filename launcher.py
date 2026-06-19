"""Lance Mochi Agent dans une fenêtre native."""
import sys,threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import uvicorn,webview
from server import app

PORT = 8000
server = None


class JsApi:
    def select_folder(self) -> str:
        """Ouvre une boîte de dialogue de sélection de dossier et retourne le chemin."""
        try:
            folder = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            if folder and len(folder) > 0:
                return folder[0]
        except Exception:
            pass
        return ""


def start_server():
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


def main():
    global server
    t = threading.Thread(target=start_server, daemon=True)
    t.start()

    api = JsApi()
    window = webview.create_window(
        title="Mochi Agent",
        url=f"http://127.0.0.1:{PORT}",
        width=900,
        height=700,
        min_size=(600, 400),
        resizable=True,
        text_select=True,
        js_api=api,
    )
    window.events.closed += sys.exit
    webview.start(private_mode=False)


if __name__ == "__main__":
    main()
