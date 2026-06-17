import subprocess
import httpx
from pathlib import Path
from datetime import datetime


class ToolDefinition:
    __slots__ = ("name", "description", "parameters")

    def __init__(self, name: str, description: str, parameters: dict):
        self.name = name
        self.description = description
        self.parameters = parameters

    def to_dict(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


WORKSPACE = Path.cwd()

def set_workspace(path: str):
    global WORKSPACE
    p = Path(path).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    WORKSPACE = p


def _log_tool(name: str, args: dict, result: str):
    """Log visuel compact pour l'utilisateur."""
    ts = datetime.now().strftime("%H:%M:%S")
    if name == "run_command":
        cmd = args.get("command", "")
        # Extrait l'action principale (premier mot)
        action = cmd.strip().split()[0] if cmd.strip() else "cmd"
        rest = cmd[len(action):].strip()
        print(f"\n+-[{ts}] Run : {action} {rest}")
        if result and result != "(aucune sortie)":
            for line in result.splitlines()[:10]:
                print(f"|  {line}")
            if len(result.splitlines()) > 10:
                print(f"|  ... ({len(result.splitlines())} lignes total)")
        print("+--")
    elif name == "edit_file":
        path = args.get("path", "")
        sl = args.get("start_line", "?")
        el = args.get("end_line", "?")
        content = args.get("content", "")
        preview = content[:50].replace("\n", "\\n")
        if len(content) > 50:
            preview += "..."
        print(f"\n+-[{ts}] Edit({sl}-{el}) : {preview} in {path}")
        print("+--")
    elif name == "write_file":
        path = args.get("path", "")
        content = args.get("content", "")
        preview = content[:50].replace("\n", "\\n")
        if len(content) > 50:
            preview += "..."
        print(f"\n+-[{ts}] Write : {preview} in {path}")
        print("+--")
    elif name == "read_file":
        path = args.get("path", "")
        lines = result.count("\n") + 1 if result else 0
        print(f"\n+-[{ts}] Read : {path} ({lines} lignes)")
        print("+--")
    elif name == "list_files":
        path = args.get("path", ".")
        count = len(result.splitlines()) if result else 0
        print(f"\n+-[{ts}] List : {path} ({count} items)")
        print("+--")
    elif name == "web_fetch":
        url = args.get("url", "")
        print(f"\n+-[{ts}] Fetch : {url[:80]}...")
        print("+--")


def _edit_file(path: str, start_line: int, end_line: int, content: str) -> str:
    full_path = WORKSPACE / path
    if not full_path.exists():
        return f"Erreur : fichier introuvable {full_path}"
    lines = full_path.read_text(encoding="utf-8").splitlines(keepends=True)
    total = len(lines)
    if start_line < 1 or start_line > total:
        return f"Erreur : start_line {start_line} hors limite (1-{total})"
    if end_line < start_line or end_line > total:
        return f"Erreur : end_line {end_line} hors limite ({start_line}-{total})"
    # Normalize new content line endings
    new_lines = content.splitlines(keepends=True) if content else []
    if not new_lines:
        new_lines = [""]
    # Ensure last line has newline
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"
    idx_s = start_line - 1
    idx_e = end_line
    old_preview = "".join(lines[idx_s:idx_e])[:100].replace("\n", "\\n")
    lines[idx_s:idx_e] = new_lines
    full_path.write_text("".join(lines), encoding="utf-8")
    result = f"Fichier édité : {full_path} (lignes {start_line}-{end_line} remplacées). Ancien: {old_preview}"
    _log_tool("edit_file", {"path": path, "start_line": start_line, "end_line": end_line, "content": content}, result)
    return result


def _write_file(path: str, content: str) -> str:
    full_path = WORKSPACE / path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    result = f"Fichier créé : {full_path}"
    _log_tool("write_file", {"path": path, "content": content}, result)
    return result


def _read_file(path: str | list) -> str:
    if isinstance(path, list):
        parts = []
        for p in path:
            full = WORKSPACE / p
            if not full.exists():
                parts.append(f"--- {p} ---\nErreur : fichier introuvable {full}")
            else:
                parts.append(f"--- {p} ---\n{full.read_text(encoding='utf-8')}")
        result = "\n\n".join(parts)
        _log_tool("read_file", {"path": path}, result)
        return result
    full_path = WORKSPACE / path
    if not full_path.exists():
        result = f"Erreur : fichier introuvable {full_path}"
        _log_tool("read_file", {"path": path}, result)
        return result
    result = full_path.read_text(encoding="utf-8")
    _log_tool("read_file", {"path": path}, result)
    return result


def _list_files(path: str = ".") -> str:
    full_path = WORKSPACE / path
    if not full_path.exists():
        result = f"Erreur : dossier introuvable {full_path}"
        _log_tool("list_files", {"path": path}, result)
        return result
    items = []
    for f in full_path.iterdir():
        items.append(f"+ {f.name}{'/' if f.is_dir() else ''}")
    result = "\n".join(items) if items else "(dossier vide)"
    _log_tool("list_files", {"path": path}, result)
    return result


def _run_command(command: str) -> str:
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=120
        )
        out = result.stdout or ""
        if result.stderr:
            out += f"\n[stderr]\n{result.stderr}"
        result_str = out or "(aucune sortie)"
    except subprocess.TimeoutExpired:
        result_str = "Erreur : commande a expiré (120s)"
    except Exception as e:
        result_str = f"Erreur : {e}"
    _log_tool("run_command", {"command": command}, result_str)
    return result_str


def _web_fetch(url: str) -> str:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr,fr-FR;q=0.9,en;q=0.8",
        }
        resp = httpx.get(url, headers=headers, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        result = resp.text[:10000]
    except Exception as e:
        result = f"Erreur de fetch : {e}"
    _log_tool("web_fetch", {"url": url}, result)
    return result


TOOLS = [
    ToolDefinition(
        name="edit_file",
        description="Remplacer un bloc de lignes dans un fichier existant (ex: lignes 4-10). Plus rapide que write_file pour des modifications ciblées.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Chemin relatif du fichier"},
                "start_line": {"type": "integer", "description": "Numéro de ligne de début (1-indexed)"},
                "end_line": {"type": "integer", "description": "Numéro de ligne de fin (inclusif)"},
                "content": {"type": "string", "description": "Nouveau contenu qui remplace les lignes start_line à end_line"},
            },
            "required": ["path", "start_line", "end_line", "content"],
        },
    ),
    ToolDefinition(
        name="write_file",
        description="Ecrire ou creer un fichier sur le disque.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Chemin relatif du fichier (ex: site/index.html)"},
                "content": {"type": "string", "description": "Contenu du fichier"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolDefinition(
        name="read_file",
        description="Lire le contenu d'un ou plusieurs fichiers. Si plusieurs, passer une liste de chemins.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "oneOf": [
                        {"type": "string", "description": "Chemin relatif du fichier"},
                        {"type": "array", "items": {"type": "string"}, "description": "Liste de chemins relatifs"},
                    ],
                    "description": "Chemin relatif du fichier ou tableau de chemins",
                },
            },
            "required": ["path"],
        },
    ),
    ToolDefinition(
        name="list_files",
        description="Lister les fichiers d'un dossier.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Chemin relatif du dossier (defaut: .)"},
            },
            "required": [],
        },
    ),
    ToolDefinition(
        name="run_command",
        description="Executer une commande shell (bash, powershell, etc.).",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Commande a executer"},
            },
            "required": ["command"],
        },
    ),
    ToolDefinition(
        name="web_fetch",
        description="Recuperer le contenu d'une URL.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL a fetch"},
            },
            "required": ["url"],
        },
    ),
]

_TOOL_MAP = {
    "edit_file": _edit_file,
    "write_file": _write_file,
    "read_file": _read_file,
    "list_files": _list_files,
    "run_command": _run_command,
    "web_fetch": _web_fetch,
}


def execute_tool(name: str, arguments: dict) -> str:
    fn = _TOOL_MAP.get(name)
    if not fn:
        return f"Erreur : outil '{name}' inconnu"
    try:
        return str(fn(**arguments))
    except Exception as e:
        return f"Erreur d'execution de {name}: {e}"
