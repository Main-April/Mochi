import subprocess
import shlex
import time
import threading
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


def _safe_path(path: str | list) -> Path | list | str:
    if isinstance(path, list):
        result = []
        for p in path:
            r = _safe_path(p)
            if isinstance(r, str):
                return r
            result.append(r)
        return result
    p = Path(path)
    if p.is_absolute():
        return "Erreur: chemin absolu interdit"
    resolved = (WORKSPACE / p).resolve()
    try:
        resolved.relative_to(WORKSPACE.resolve())
    except ValueError:
        return f"Erreur: accès en dehors du workspace interdit ({resolved})"
    return resolved


def set_workspace(path: str):
    global WORKSPACE
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return "Erreur: le dossier n'existe pas"
    WORKSPACE = p


def _log_tool(name: str, args: dict, summary: str, duration_ms: int):
    ts = datetime.now().strftime("%H:%M:%S")
    dur = f" ({duration_ms}ms)" if duration_ms > 0 else ""
    if name == "run_command":
        cmd = args.get("command", "")
        action = cmd.strip().split()[0] if cmd.strip() else "cmd"
        rest = cmd[len(action):].strip()
        print(f"\n+-[{ts}] Run : {action} {rest}{dur}")
        lines = summary.splitlines()
        for line in lines[:10]:
            print(f"|  {line}")
        if len(lines) > 10:
            print(f"|  ... ({len(lines)} lignes total)")
        print("+--")
    elif name == "edit_file":
        path = args.get("path", "")
        sl = args.get("start_line", "?")
        el = args.get("end_line", "?")
        print(f"\n+-[{ts}] Edit({sl}-{el}) in {path}{dur}")
        print("+--")
    elif name == "write_file":
        path = args.get("path", "")
        print(f"\n+-[{ts}] Write : {path}{dur}")
        print("+--")
    elif name == "read_file":
        path = args.get("path", "")
        lines = summary.count("\n") + 1 if summary else 0
        print(f"\n+-[{ts}] Read : {path} ({lines} lignes){dur}")
        print("+--")
    elif name == "list_files":
        path = args.get("path", ".")
        count = len(summary.splitlines()) if summary else 0
        print(f"\n+-[{ts}] List : {path} ({count} items){dur}")
        print("+--")
    elif name == "web_fetch":
        url = args.get("url", "")
        print(f"\n+-[{ts}] Fetch : {url[:80]}...{dur}")
        print("+--")


def _format_result(name: str, summary: str, data: dict) -> dict:
    return {"summary": summary, "data": data, "tool": name}


def _edit_file(path: str, start_line: int, end_line: int, content: str) -> dict:
    t0 = time.monotonic()
    fp = _safe_path(path)
    if isinstance(fp, str):
        return _format_result("edit_file", fp, {"path": path, "error": fp})
    full_path: Path = fp
    if not full_path.exists():
        msg = f"Erreur : fichier introuvable {full_path}"
        return _format_result("edit_file", msg, {"path": path, "error": msg})
    if not full_path.is_file():
        msg = f"Erreur : {full_path} n'est pas un fichier"
        return _format_result("edit_file", msg, {"path": path, "error": msg})
    old_lines = full_path.read_text(encoding="utf-8").splitlines(keepends=True)
    total = len(old_lines)
    if start_line < 1 or start_line > total:
        msg = f"Erreur : start_line {start_line} hors limite (1-{total})"
        return _format_result("edit_file", msg, {"path": path, "error": msg})
    if end_line < start_line or end_line > total:
        msg = f"Erreur : end_line {end_line} hors limite ({start_line}-{total})"
        return _format_result("edit_file", msg, {"path": path, "error": msg})
    new_lines = content.splitlines(keepends=True) if content else [""]
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"
    idx_s = start_line - 1
    idx_e = end_line
    old_content = "".join(old_lines[idx_s:idx_e])
    old_lines[idx_s:idx_e] = new_lines
    new_full = "".join(old_lines)
    full_path.write_text(new_full, encoding="utf-8")
    nr = len(new_lines)
    changes = _diff_lines(old_content, content)
    dur = int((time.monotonic() - t0) * 1000)
    summary = f"Fichier édité : {path} (lignes {start_line}-{end_line}, {nr} lignes insérées)"
    _log_tool("edit_file", {"path": path, "start_line": start_line, "end_line": end_line, "content": content}, summary, dur)
    return _format_result("edit_file", summary, {
        "path": str(path),
        "start_line": start_line,
        "end_line": end_line,
        "old_content": old_content,
        "new_content": content,
        "diff": changes,
        "duration_ms": dur,
    })


def _diff_lines(old: str, new: str) -> list[dict]:
    old_l = old.splitlines(keepends=True)
    new_l = new.splitlines(keepends=True)
    import difflib
    result = []
    for line in difflib.unified_diff(old_l, new_l, n=3):
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("@@"):
            result.append({"type": "hunk", "content": line.strip()})
        elif line.startswith("+"):
            result.append({"type": "add", "content": line[1:].rstrip("\n")})
        elif line.startswith("-"):
            result.append({"type": "del", "content": line[1:].rstrip("\n")})
        else:
            result.append({"type": "ctx", "content": line.rstrip("\n")})
    return result


def _write_file(path: str, content: str) -> dict:
    t0 = time.monotonic()
    fp = _safe_path(path)
    if isinstance(fp, str):
        return _format_result("write_file", fp, {"path": path, "error": fp})
    full_path: Path = fp
    old_content = None
    created = not full_path.exists()
    if not created:
        old_content = full_path.read_text(encoding="utf-8")
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    lines = content.count("\n") + 1 if content else 0
    dur = int((time.monotonic() - t0) * 1000)
    summary = f"Fichier {'créé' if created else 'écrit'} : {path} ({lines} lignes)"
    _log_tool("write_file", {"path": path, "content": content}, summary, dur)
    return _format_result("write_file", summary, {
        "path": str(path),
        "lines": lines,
        "content": content,
        "old_content": old_content,
        "created": created,
        "duration_ms": dur,
    })


def _read_file(path: str | list, offset: int = 0, limit: int = 0) -> dict:
    t0 = time.monotonic()
    if isinstance(path, list):
        parts_summary = []
        parts_data = []
        for p in path:
            res = _read_file(p, offset, limit)
            parts_summary.append(res["summary"])
            parts_data.append(res["data"])
        dur = int((time.monotonic() - t0) * 1000)
        summary = "; ".join(parts_summary)
        return _format_result("read_file", summary, {
            "files": parts_data,
            "duration_ms": dur,
        })
    fp = _safe_path(path)
    if isinstance(fp, str):
        return _format_result("read_file", fp, {"path": path, "error": fp})
    full_path: Path = fp
    if not full_path.exists():
        msg = f"Erreur : fichier introuvable {full_path}"
        return _format_result("read_file", msg, {"path": path, "error": msg})
    content = full_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    total_lines = len(lines)
    if offset > 0:
        lines = lines[offset:]
    if limit > 0:
        lines = lines[:limit]
    result = "".join(lines)
    dur = int((time.monotonic() - t0) * 1000)
    summary = f"Fichier lu : {path} ({total_lines} lignes)"
    _log_tool("read_file", {"path": path}, summary, dur)
    return _format_result("read_file", result, {
        "path": str(path),
        "content": result,
        "total_lines": total_lines,
        "offset": offset,
        "limit": limit,
        "duration_ms": dur,
    })


def _list_files(path: str = ".") -> dict:
    t0 = time.monotonic()
    fp = _safe_path(path)
    if isinstance(fp, str):
        return _format_result("list_files", fp, {"path": path, "error": fp})
    full_path: Path = fp
    if not full_path.exists():
        msg = f"Erreur : dossier introuvable {full_path}"
        return _format_result("list_files", msg, {"path": path, "error": msg})
    items = []
    for f in sorted(full_path.iterdir()):
        items.append({
            "name": f.name,
            "type": "dir" if f.is_dir() else "file",
            "size": f.stat().st_size if f.is_file() else 0,
        })
    summary = "\n".join(f"+ {i['name']}{'/' if i['type'] == 'dir' else ''}" for i in items) if items else "(dossier vide)"
    dur = int((time.monotonic() - t0) * 1000)
    _log_tool("list_files", {"path": path}, summary, dur)
    return _format_result("list_files", summary, {
        "path": str(path),
        "items": items,
        "count": len(items),
        "duration_ms": dur,
    })


_BLOCKED_CMDS = {"rm", "del", "rd", "format", "shutdown", "reboot", "reg", "regedit",
                  "del /f", "rmdir", "cipher", "diskpart", "wevtutil", "bcdedit",
                  "del /s", "rd /s", "rm -rf", "rm -r", "rm -f"}


def _run_command(command: str) -> dict:
    t0 = time.monotonic()
    cmd_lower = command.strip().lower()
    for bad in _BLOCKED_CMDS:
        if cmd_lower.startswith(bad):
            msg = f"Erreur : commande interdite ({bad})"
            return _format_result("run_command", msg, {"command": command, "error": msg})
    try:
        args = shlex.split(command)
        if not args:
            msg = "Erreur : commande vide"
            return _format_result("run_command", msg, {"command": command, "error": msg})
        result = subprocess.run(
            args, shell=False, capture_output=True, text=True, timeout=120
        )
        dur = int((time.monotonic() - t0) * 1000)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        out = stdout
        if stderr:
            out += f"\n[stderr]\n{stderr}"
        summary = out or "(aucune sortie)"
        if result.returncode != 0:
            summary = f"[code {result.returncode}] {summary}"
        return _format_result("run_command", summary, {
            "command": command,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
            "duration_ms": dur,
        })
    except subprocess.TimeoutExpired:
        dur = int((time.monotonic() - t0) * 1000)
        msg = "Erreur : commande a expiré (120s)"
        return _format_result("run_command", msg, {"command": command, "error": msg, "duration_ms": dur})
    except Exception as e:
        dur = int((time.monotonic() - t0) * 1000)
        msg = f"Erreur : {e}"
        return _format_result("run_command", msg, {"command": command, "error": msg, "duration_ms": dur})


_SSRF_BLOCKED = {"127.0.0.1", "0.0.0.0", "localhost",
                 "169.254.169.254", "::1", "metadata.google.internal",
                 "100.100.100.200", "192.168.", "10.", "172.16.", "172.17.",
                 "172.18.", "172.19.", "172.20.", "172.21.", "172.22.",
                 "172.23.", "172.24.", "172.25.", "172.26.", "172.27.",
                 "172.28.", "172.29.", "172.30.", "172.31."}


def _is_private_url(url: str) -> bool:
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        host_lower = host.lower()
        if host_lower in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
            return True
        for prefix in _SSRF_BLOCKED:
            if host_lower.startswith(prefix):
                return True
        if parsed.scheme in ("file", "dict", "gopher", "ftp"):
            return True
    except Exception:
        pass
    return False


def _web_fetch(url: str) -> dict:
    t0 = time.monotonic()
    if _is_private_url(url):
        msg = "Erreur : accès aux ressources internes/réseau local interdit"
        return _format_result("web_fetch", msg, {"url": url, "error": msg})
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr,fr-FR;q=0.9,en;q=0.8",
        }
        resp = httpx.get(url, headers=headers, timeout=30.0, follow_redirects=True, max_redirects=5)
        resp.raise_for_status()
        content = resp.text[:10000]
        dur = int((time.monotonic() - t0) * 1000)
        summary = f"Contenu de {url} : {len(content)} caractères"
        return _format_result("web_fetch", summary, {
            "url": url,
            "content": content,
            "content_length": len(resp.text),
            "truncated": len(resp.text) > 10000,
            "status_code": resp.status_code,
            "duration_ms": dur,
        })
    except Exception as e:
        dur = int((time.monotonic() - t0) * 1000)
        msg = f"Erreur de fetch : {e}"
        return _format_result("web_fetch", msg, {"url": url, "error": msg, "duration_ms": dur})


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
                "offset": {"type": "integer", "description": "Ignorer les N premières lignes"},
                "limit": {"type": "integer", "description": "Lire au maximum N lignes"},
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


def execute_tool(name: str, arguments: dict) -> dict:
    fn = _TOOL_MAP.get(name)
    if not fn:
        return _format_result(name, f"Erreur : outil '{name}' inconnu", {"error": f"outil '{name}' inconnu"})
    try:
        result = fn(**arguments)
        if name in ("edit_file", "write_file"):
            with _HISTORY_LOCK:
                _TOOL_HISTORY.append({"tool": name, "args": dict(arguments), "result": result})
                _REDO_STACK.clear()
        return result
    except Exception as e:
        return _format_result(name, f"Erreur d'execution de {name}: {e}", {"error": str(e)})


# ---------------------------------------------------------------------------
# Undo / Redo history
# ---------------------------------------------------------------------------

_TOOL_HISTORY: list[dict] = []
_REDO_STACK: list[dict] = []
_HISTORY_LOCK = threading.Lock()


def _undo_tool() -> dict:
    with _HISTORY_LOCK:
        if not _TOOL_HISTORY:
            return _format_result("undo", "Rien à annuler.", {"message": "Rien à annuler."})
        entry = _TOOL_HISTORY.pop()
    name = entry["tool"]
    args = entry["args"]
    result = entry["result"]
    data = result.get("data", {})

    if name == "edit_file":
        old_content = data.get("old_content")
        if old_content is None:
            return _format_result("undo", "Impossible d'annuler : old_content manquant",
                                  {"message": "old_content manquant"})
        reverse = _edit_file(args["path"], args["start_line"], args["end_line"], old_content)
        with _HISTORY_LOCK:
            _REDO_STACK.append({"tool": name, "args": dict(args), "result": result})
        return _format_result("undo",
                              f"Annulé : édition de {args['path']} (lignes {args['start_line']}-{args['end_line']})",
                              {"message": f"Édition de {args['path']} annulée", "undo_result": reverse})

    elif name == "write_file":
        old_content = data.get("old_content")
        path = args["path"]
        if old_content is not None:
            reverse = _write_file(path, old_content)
            with _HISTORY_LOCK:
                _REDO_STACK.append({"tool": name, "args": dict(args), "result": result})
            return _format_result("undo",
                                  f"Annulé : écriture dans {path} (restauration de l'ancien contenu)",
                                  {"message": f"Écriture dans {path} annulée", "undo_result": reverse})
        else:
            fp = _safe_path(path)
            if isinstance(fp, str):
                return _format_result("undo", f"Erreur : {fp}", {"message": fp})
            if fp.exists():
                fp.unlink()
                with _HISTORY_LOCK:
                    _REDO_STACK.append({"tool": name, "args": dict(args), "result": result})
                return _format_result("undo",
                                      f"Annulé : fichier créé {path} supprimé",
                                      {"message": f"Fichier {path} supprimé"})
            return _format_result("undo", f"Fichier {path} déjà supprimé", {"message": f"{path} déjà supprimé"})

    return _format_result("undo", f"Annulation non supportée pour {name}",
                          {"message": f"Pas d'annulation pour {name}"})


def _redo_tool() -> dict:
    with _HISTORY_LOCK:
        if not _REDO_STACK:
            return _format_result("redo", "Rien à refaire.", {"message": "Rien à refaire."})
        entry = _REDO_STACK.pop()
    name = entry["tool"]
    args = entry["args"]
    result = execute_tool(name, args)
    with _HISTORY_LOCK:
        _TOOL_HISTORY.append({"tool": name, "args": dict(args), "result": result})
    return _format_result("redo",
                          f"Refait : {name} sur {args.get('path', '')}",
                          {"message": f"Action {name} refaite", "redo_result": result})
