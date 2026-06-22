import json
import os
import re
import asyncio
import time
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from collections import deque

import httpx

from .tools import TOOLS, execute_tool, _question_store
from .parser import clean as _clean_response, compress as _compress

_JSON_ENSURE = {"ensure_ascii": False}

# Project root (2 levels up from core/agent.py → Agent/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent



def _build_tools_dict() -> list:
    """Construit la liste des outils au moment de l'appel (non au module load)."""
    return [t.to_dict() for t in TOOLS]


class OpenRouterError(Exception):
    pass


class OpenRouter:
    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: str = "", max_retries: int = 3, rpm: int = 20):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise OpenRouterError("OPENROUTER_API_KEY non définie.")
        self.max_retries = max_retries
        self._sem = asyncio.Semaphore(6)
        self._rpm = rpm
        self._times_per_key: dict[str, deque] = {}
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0, pool=10.0),
            limits=httpx.Limits(
                max_keepalive_connections=10,
                max_connections=20,
                keepalive_expiry=30.0,
            ),
        )

    async def close(self):
        await self._client.aclose()

    def _headers(self, api_key: str | None = None) -> dict:
        return {
            "Authorization": f"Bearer {api_key or self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "Mochi Agent",
        }

    async def _limit(self, api_key: str):
        now = time.monotonic()
        cutoff = now - 60
        dq = self._times_per_key.setdefault(api_key, deque())
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= self._rpm:
            sleep_for = 60 - (now - dq[0])
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
        dq.append(time.monotonic())

    async def _req(self, payload: dict, api_key: str | None = None) -> dict:
        ak = api_key or self.api_key
        async with self._sem:
            await self._limit(ak)
            last_err: Exception | None = None
            for attempt in range(self.max_retries):
                try:
                    r = await self._client.post(
                        f"{self.BASE_URL}/chat/completions",
                        headers=self._headers(api_key=ak),
                        json=payload,
                        timeout=30.0,
                    )
                    if r.status_code == 429:
                        retry_after = r.headers.get("Retry-After")
                        delay = int(retry_after) if retry_after and retry_after.isdigit() else (4 ** attempt)
                        await asyncio.sleep(min(delay, 30))
                        last_err = OpenRouterError("429 Too Many Requests")
                        continue
                    r.raise_for_status()
                    return r.json()
                except httpx.HTTPStatusError as e:
                    c = e.response.status_code
                    if c == 402:
                        raise OpenRouterError("Crédits insuffisants (402). Recharge ton compte OpenRouter.")
                    if c == 401:
                        # 401 peut signifier : clé invalide OU modèle non accessible sur ce tier
                        body = e.response.text[:300]
                        if "not found" in body.lower() or "model" in body.lower():
                            raise OpenRouterError(f"Modèle non accessible (401): {body}")
                        raise OpenRouterError(f"Clé API invalide (401): {body}")
                    if c >= 500 and attempt < self.max_retries - 1:
                        last_err = e
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    raise OpenRouterError(f"HTTP {c}: {e.response.text[:200]}")
                except httpx.RequestError as e:
                    last_err = e
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
            raise OpenRouterError(
                f"429: {last_err or 'trop de requêtes'}"
            )

    async def chat(
        self,
        messages: list,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list | None = None,
        api_key: str | None = None,
    ) -> tuple[str, dict | None, list | None]:
        p: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            p["tools"] = tools
        d = await self._req(p, api_key)
        m = d["choices"][0]["message"]
        content = m.get("content") or ""
        return content, d.get("usage"), m.get("tool_calls")

    async def chat_with_tools(
        self,
        messages: list,
        model: str,
        max_tokens: int = 4096,
        tools: list | None = None,
        api_key: str | None = None,
        max_rounds: int = 10,
    ) -> tuple[str, dict | None]:
        usage_acc: dict = {}
        for _ in range(max_rounds):
            content, usage, tool_calls = await self.chat(
                messages, model, max_tokens=max_tokens, tools=tools, api_key=api_key
            )
            _merge_usage(usage_acc, usage)
            if not tool_calls:
                return content, usage_acc or None
            for tc in tool_calls:
                result = execute_tool(
                    tc["function"]["name"],
                    json.loads(tc["function"]["arguments"]),
                )
                messages.append({
                    "role": "assistant",
                    "content": content or "",
                    "tool_calls": [tc],
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result["summary"][:2000],
                })
        content, usage, _ = await self.chat(
            messages, model, max_tokens=max_tokens, api_key=api_key
        )
        _merge_usage(usage_acc, usage)
        return content, usage_acc or None

    async def chat_stream(
        self,
        messages: list,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list | None = None,
        api_key: str | None = None,
    ):
        p: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            p["tools"] = tools

        ak = api_key or self.api_key
        async with self._sem:
            await self._limit(ak)
            last_err: Exception | None = None
            for attempt in range(self.max_retries):
                try:
                    async with self._client.stream(
                        "POST",
                        f"{self.BASE_URL}/chat/completions",
                        headers=self._headers(api_key=ak),
                        json=p,
                        timeout=30.0,
                    ) as r:
                        if r.status_code == 429:
                            retry_after = r.headers.get("Retry-After")
                            delay = int(retry_after) if retry_after and retry_after.isdigit() else (4 ** attempt)
                            await asyncio.sleep(min(delay, 30))
                            last_err = OpenRouterError("429 Too Many Requests")
                            continue
                        if r.status_code != 200:
                            body = (await r.aread()).decode(errors="replace")[:200]
                            raise OpenRouterError(f"HTTP {r.status_code}: {body}")

                        tool_acc: dict[int, dict] = {}
                        full_content = ""
                        finish_reason: str | None = None

                        async for line in r.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            raw = line[6:].strip()
                            if raw == "[DONE]":
                                break
                            try:
                                chunk = json.loads(raw)
                            except json.JSONDecodeError:
                                continue

                            if chunk.get("usage"):
                                yield ("usage", chunk["usage"])

                            choices = chunk.get("choices", [])
                            if not choices:
                                continue
                            choice = choices[0]
                            finish_reason = choice.get("finish_reason") or finish_reason
                            delta = choice.get("delta", {})

                            if delta.get("content"):
                                full_content += delta["content"]
                                yield ("content", delta["content"])

                            if delta.get("tool_calls"):
                                for tc in delta["tool_calls"]:
                                    idx = tc.get("index", 0)
                                    if idx not in tool_acc:
                                        tool_acc[idx] = {
                                            "id": "",
                                            "type": "function",
                                            "function": {"name": "", "arguments": ""},
                                        }
                                    if tc.get("id"):
                                        tool_acc[idx]["id"] = tc["id"]
                                    fn = tc.get("function", {})
                                    if fn.get("name"):
                                        tool_acc[idx]["function"]["name"] = fn["name"]
                                    if fn.get("arguments"):
                                        tool_acc[idx]["function"]["arguments"] += fn["arguments"]

                        if finish_reason == "tool_calls" and tool_acc:
                            yield ("tool_calls", list(tool_acc.values()))
                        else:
                            yield ("done", full_content)
                        return

                except httpx.RequestError as e:
                    last_err = e
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue

            yield ("error", f"429: {last_err or 'trop de requêtes'}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _merge_usage(acc: dict, u: dict | None):
    if not u:
        return
    acc["prompt_tokens"] = acc.get("prompt_tokens", 0) + (u.get("prompt_tokens") or 0)
    acc["completion_tokens"] = acc.get("completion_tokens", 0) + (u.get("completion_tokens") or 0)
    acc["total_tokens"] = acc.get("total_tokens", 0) + (u.get("total_tokens") or 0)


def _load_env(env_path: str | Path = ".env"):
    p = Path(env_path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ[k.strip()] = v.strip().strip("\"'")


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

class Memory:
    def __init__(self, filepath: str | Path = "memory.json", max_chars: int = 120_000):
        self.filepath = Path(filepath)
        self.max_chars = max_chars
        self.messages: list[dict] = self._load()
        self._cc: int = sum(len(m.get("content") or "") for m in self.messages)

    def _load(self) -> list:
        if self.filepath.exists():
            try:
                return json.loads(
                    self.filepath.read_text(encoding="utf-8")
                ).get("messages", [])
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def _save(self):
        data = json.dumps(
            {"updated_at": datetime.now().isoformat(), "messages": self.messages},
            **_JSON_ENSURE,
        )
        self.filepath.write_text(data, encoding="utf-8")

    async def flush(self):
        self._save()

    def add(self, role: str, content: str, meta: dict | None = None):
        entry = {"role": role, "content": content, "ts": datetime.now().isoformat()}
        if meta:
            entry["meta"] = meta
        self.messages.append(entry)
        self._cc += len(content)
        self._prune()
        self._save()

    def add_system(self, content: str):
        if self.messages and self.messages[0]["role"] == "system":
            old_len = len(self.messages[0].get("content") or "")
            self._cc -= old_len
            self.messages[0]["content"] = content
        else:
            self.messages.insert(0, {"role": "system", "content": content})
        self._cc += len(content)
        self._save()

    def get(self, limit: int | None = None) -> list:
        if limit:
            return self.messages[-limit:]
        return self.messages

    def clear(self):
        self.messages = []
        self._cc = 0
        self._save()

    def _prune(self):
        i = 0
        while self._cc > self.max_chars and i < len(self.messages):
            if self.messages[i].get("role") == "system":
                i += 1
                continue
            removed = self.messages.pop(i)
            self._cc -= len(removed.get("content") or "")

    def count(self) -> int:
        return len(self.messages)


# ---------------------------------------------------------------------------
# Constantes de mode
# ---------------------------------------------------------------------------

MODE_META = {
    "work":    {"label": "Working",       "desc": "code, fichiers, commandes"},
    "docs":    {"label": "Documentation", "desc": "documentation technique"},
    "debug":   {"label": "Debug",         "desc": "analyse et correction de bugs"},
    "creative":{"label": "Creative",      "desc": "génération créative"},
    "focus":   {"label": "Focus",         "desc": "mode autonome multi-modèles pour infra complète"},
}

# Mapping des types de tâches vers les sous-modèles Focus
_FOCUS_TASK_TYPE_MAP = {
    "plan":    "planner",
    "code":    "coder",
    "debug":   "debugger",
    "style":   "stylist",
    "ui":      "stylist",
    "css":     "stylist",
    "review":  "reviewer",
    "test":    "debugger",
    "fix":     "debugger",
    "arch":    "planner",
    "infra":   "planner",
    "doc":     "reviewer",
}

def _detect_focus_specialist(step: str) -> str:
    """Détecte quel sous-modèle utiliser selon la nature de l'étape."""
    s = step.lower()
    if any(kw in s for kw in ("style", "css", "design", "ui", "ux", "couleur", "color", "interface", "layout", "html", "front")):
        return "stylist"
    if any(kw in s for kw in ("bug", "fix", "erreur", "error", "debug", "traceback", "exception", "test", "valide", "vérifie")):
        return "debugger"
    if any(kw in s for kw in ("plan", "architecture", "structure", "organise", "infra", "décompose", "stratégie")):
        return "planner"
    if any(kw in s for kw in ("review", "vérifie", "audit", "qualité", "sécurité", "check", "analyse")):
        return "reviewer"
    # Par défaut : coder
    return "coder"


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Agent:
    def __init__(self, config_path: str = "config.json"):
        config_file = (_PROJECT_ROOT / config_path).resolve()
        config = json.loads(config_file.read_text(encoding="utf-8"))
        _load_env(config_file.parent / ".env")
        self.config = config
        self.name: str = config["agent"]["name"]

        # Charger le workspace persistant
        ws = config.get("workspace")
        if ws:
            from .tools import set_workspace
            set_workspace(ws)

        ctx = config["context"]
        memory_file = (_PROJECT_ROOT / ctx.get("memory_file", "memory.json")).resolve()
        self.memory = Memory(
            filepath=str(memory_file),
            max_chars=ctx.get("max_context_tokens", 120_000),
        )

        rl = config.get("rate_limits", {})
        self.client = OpenRouter(
            max_retries=rl.get("max_retries", 3),
            rpm=rl.get("max_requests_per_minute", 60),
        )

        self.modes: dict = config.get("modes", {})
        self.current_mode: str = "work"
        self._mc_cache: dict | None = None
        self._fallback: str = "openai/gpt-oss-120b:free"

        self.token_usage: dict = {"prompt": 0, "completion": 0, "total": 0}
        self._usage_lock = asyncio.Lock()

        self._pool: ThreadPoolExecutor | None = None
        self.auto_detect: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self):
        await self.memory.flush()
        await self.client.close()
        if self._pool:
            self._pool.shutdown(wait=False)

    # ------------------------------------------------------------------
    # Mode helpers
    # ------------------------------------------------------------------

    def _mc(self) -> dict:
        if self._mc_cache is None:
            self._mc_cache = self.modes.get(self.current_mode, {})
        return self._mc_cache

    def _invalidate_mc(self):
        self._mc_cache = None

    def _api_key(self) -> str | None:
        mc = self._mc()
        k = mc.get("api_key")
        if k:
            return k
        env_key = {"work": "WORK_API_KEY", "docs": "DOCS_API_KEY"}.get(self.current_mode)
        return os.environ.get(env_key) if env_key else None

    def _model(self) -> str:
        return self._mc().get("model", "openai/gpt-4o-mini")

    def _mt(self) -> int:
        return (
            self._mc().get("max_tokens")
            or self.config["rate_limits"].get("max_tokens_per_request", 4096)
        )

    def _tools(self) -> list | None:
        if self._mc().get("tools", False):
            all_tools = _build_tools_dict()
            allowed = self._mc().get("allowed_tools")
            if allowed:
                return [t for t in all_tools if t["function"]["name"] in allowed]
            return all_tools
        return None

    def _get_pool(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=8)
        return self._pool

    # ------------------------------------------------------------------
    # Multi-key fallback
    # ------------------------------------------------------------------

    def _all_api_keys(self) -> list[str]:
        keys: list[str] = []
        seen = set()
        for k in (self._api_key(), self.client.api_key):
            if k and k not in seen:
                keys.append(k)
                seen.add(k)
        for key_name in ("OPENROUTER_API_KEY", "WORK_API_KEY", "DOCS_API_KEY", "FALLBACK_KEY"):
            v = os.environ.get(key_name)
            if v and v not in seen:
                keys.append(v)
                seen.add(v)
        return keys

    # ------------------------------------------------------------------
    # Token accounting (thread-safe)
    # ------------------------------------------------------------------

    async def _tu(self, u: dict | None):
        if not u:
            return
        async with self._usage_lock:
            self.token_usage["prompt"]     += u.get("prompt_tokens", 0) or 0
            self.token_usage["completion"] += u.get("completion_tokens", 0) or 0
            self.token_usage["total"]      += u.get("total_tokens", 0) or 0

    # ------------------------------------------------------------------
    # Core call (avec fallback loggé)
    # ------------------------------------------------------------------

    async def _call(
        self,
        msgs: list,
        model: str | None = None,
        mt: int | None = None,
        tools: list | None = None,
        api_key: str | None = None,
        temp: float = 0.7,
    ) -> tuple[str, dict | None, list | None]:
        model   = model   or self._model()
        mt      = mt      or self._mt()
        api_key = api_key or self._api_key()

        keys = self._all_api_keys() if not api_key else [api_key]
        models = [model]
        if self._fallback and self._fallback != model:
            models.append(self._fallback)

        last_err: Exception | None = None
        for mk in models:
            for k in keys:
                try:
                    content, usage, tcs = await self.client.chat(
                        msgs, mk, temperature=temp, max_tokens=mt,
                        tools=tools, api_key=k,
                    )
                    cleaned = _clean_response(content)
                    return cleaned, usage, tcs
                except OpenRouterError as e:
                    last_err = e
                    if "429" in str(e):
                        await asyncio.sleep(2)
                    continue
        raise OpenRouterError(
            f"Tous les modèles/clés échoués: {last_err or 'inconnu'}"
        )

    async def _call_with_tools(
        self,
        msgs: list,
        model: str | None = None,
        mt: int | None = None,
        tools: list | None = None,
        api_key: str | None = None,
        temp: float = 0.7,
    ) -> tuple[str, dict | None]:
        model   = model   or self._model()
        mt      = mt      or self._mt()
        api_key = api_key or self._api_key()

        keys = self._all_api_keys() if not api_key else [api_key]
        models = [model]
        if self._fallback and self._fallback != model:
            models.append(self._fallback)

        last_err: Exception | None = None
        for mk in models:
            for k in keys:
                try:
                    content, usage = await self.client.chat_with_tools(
                        msgs, mk, max_tokens=mt, tools=tools, api_key=k,
                    )
                    cleaned = _clean_response(content)
                    return cleaned, usage
                except OpenRouterError as e:
                    last_err = e
                    if "429" in str(e):
                        await asyncio.sleep(2)
                    continue
        raise OpenRouterError(
            f"Tous les modèles/clés échoués: {last_err or 'inconnu'}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _load_system_prompt_base(self) -> str:
        path = Path.cwd() / ".mochi" / "prompts" / "system_prompt.md"
        try:
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
                try:
                    print(f"\n[{self.name}][SYSTEM PROMPT] charge depuis {path} ({len(content)} chars)")
                except Exception:
                    pass
                return content
        except OSError:
            pass
        return ""

    def _system_prompt(self) -> str:
        # Cache le résultat pour éviter de relire le disque à chaque appel d'étape
        if not hasattr(self, "_sp_cache") or self._sp_cache_mode != self.current_mode:
            base = self._load_system_prompt_base()
            mc = self._mc()
            mode_label = self.current_mode.capitalize()
            tools_status = "activés: edit_file, write_file, read_file, list_files, run_command, web_fetch" if mc.get("tools") else "désactivés"
            self._sp_cache = base.replace("{name}", self.name).replace("{mode}", mode_label).replace("{tools}", tools_status)
            self._sp_cache_mode = self.current_mode
        return self._sp_cache

    def _invalidate_sp_cache(self):
        """Invalide le cache du system prompt (à appeler lors d'un changement de mode)."""
        if hasattr(self, "_sp_cache"):
            del self._sp_cache
        if hasattr(self, "_sp_cache_mode"):
            del self._sp_cache_mode

    def _plan_prompt(self) -> str:
        if self.current_mode == "docs":
            return "Aucune planification. Réponds directement."
        base = (
            "Tu es un planificateur expert. Décompose en étapes [STEP] groupées par [GROUP N] (N identique = parallèle).\n"
            "Règles : 1) Une action concrète par step. 2) Ordre logique. 3) Max 8 steps. 4) Inclus tests/vérifs.\n"
            "Exemple:\n[GROUP 1]\n[STEP] Explorer la codebase (grep/glob)\n[GROUP 2]\n[STEP] Créer module principal\n[STEP] Créer tests unitaires\n[GROUP 3]\n[STEP] Lancer tests et corriger"
        )
        
        mode_specific = {
            "work": base + "\n\nFocus: implémentation code, fichiers, commandes, tests.",
            "debug": base + "\n\nFocus: investigation bug, lecture logs/code, identification cause racine, fix.",
            "creative": base + "\n\nFocus: génération idées, concepts, contenu créatif (pas d'outils).",
        }
        return mode_specific.get(self.current_mode, base)

    async def chat_async(self, message: str) -> str:
        self.memory.add("user", message)
        msgs = self.memory.get()
        content, usage, tool_calls = await self._call(
            msgs, temp=0.5, tools=self._tools()
        )
        if tool_calls:
            content, usage = await self._call_with_tools(
                msgs, temp=0.5, tools=self._tools()
            )
        await self._tu(usage)
        cleaned = _clean_response(content)
        self.memory.add("assistant", cleaned)
        return cleaned

    async def gen(self, task: str) -> str:
        if self.current_mode == "docs":
            return await self._gen_docs(task)
        elif self.current_mode == "debug":
            return await self._gen_debug(task)
        else:
            return await self._gen_work(task)

    async def _exec_step(
        self,
        step: str,
        tools: list | None,
        model: str,
        mt: int,
        ak: str | None,
    ) -> str:
        step_prompt = (
            f"{self._system_prompt()}\n\n"
            "MÉTHODE: Analyse → Plan → Exécute → Valide\n"
            "RÈGLE: Utilise les outils, ne devine pas.\n"
            "IMPORTANT: Termine avec un résumé de ce qui a été fait."
        )
        msgs = [
            {"role": "system", "content": step_prompt},
            {"role": "user",   "content": step},
        ]
        try:
            if tools:
                content, usage = await self._call_with_tools(
                    msgs, model=model, mt=mt, tools=tools, api_key=ak
                )
            else:
                content, usage, _ = await self._call(
                    msgs, model=model, mt=mt, api_key=ak
                )
        except OpenRouterError as e:
            return f"[Erreur] {e}"
        await self._tu(usage)
        return content

    async def _gen_work(self, task: str) -> str:
        model = self._model()
        ak    = self._api_key()
        tools = self._tools()
        mt    = self._mt()

        self.memory.add("user", task)
        msgs = self.memory.get()
        content, usage = await self._call_with_tools(
            msgs, model=model, mt=mt, tools=tools, api_key=ak, temp=0.5
        )
        await self._tu(usage)
        cleaned = _clean_response(content)
        self.memory.add("assistant", cleaned)
        return cleaned

    async def _gen_docs(self, task: str) -> str:
        model = self._model()
        ak    = self._api_key()

        content, usage, _ = await self._call(
            [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user",   "content": task},
            ],
            model=model, mt=self._mt(), api_key=ak, temp=0.3,
        )
        await self._tu(usage)
        return _clean_response(content)

    async def _gen_debug(self, task: str) -> str:
        model = self._model()
        ak    = self._api_key()
        tools = self._tools()
        mt    = self._mt()

        msgs = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user",   "content": task},
        ]
        content, usage = await self._call_with_tools(
            msgs, model=model, mt=mt, tools=tools, api_key=ak, temp=0.2
        )
        await self._tu(usage)
        return _clean_response(content)

    async def generate_stream(self, task: str):
        if self.current_mode == "docs":
            async for event, data in self._gen_docs_stream(task):
                yield event, data
        elif self.current_mode == "debug":
            async for event, data in self._gen_debug_stream(task):
                yield event, data
        elif self.current_mode == "focus":
            async for event, data in self._gen_focus_stream(task):
                yield event, data
        else:
            async for event, data in self._gen_work_stream(task):
                yield event, data

    async def _gen_focus_stream(self, task: str):
        """
        Mode Focus — Pipeline multi-modèles autonome :
        1. Planner décompose la tâche en étapes JSON (sans _clean_response)
        2. Chaque étape est exécutée par le sous-modèle spécialisé approprié
        3. Un contexte compressé des étapes précédentes est injecté (pas le texte streamed brut)
        4. Les appels d'outils sont streamés en temps réel

        Corrections v2 :
        - _focus_plan utilise raw=True → pas de corruption du JSON par le parser
        - system_prompt mis en cache → pas de relecture disque à chaque étape
        - contexte inter-étapes = résumés compressés, pas le texte streamed (évite la répétition)
        - memory ne reçoit la tâche user qu'une seule fois, en fin de pipeline
        """
        mc    = self._mc()
        tools = self._tools()
        pool  = self._get_pool()
        loop  = asyncio.get_event_loop()
        keys  = self._all_api_keys()
        # On cache le system prompt une seule fois pour tout le pipeline
        base_sp = self._system_prompt()

        full_reply_parts: list[str] = []

        # ── Phase 1 : Planification ───────────────────────────────────────
        yield ("focus_phase", {"phase": "planning", "message": "Analyse de la tâche et planification..."})

        try:
            steps = await self._focus_plan(task)
        except Exception as e:
            yield ("error", f"[Focus/Planner] Erreur de planification: {e}")
            return

        yield ("focus_plan", {"steps": steps, "total": len(steps)})

        # Sauvegarder le plan dans l'historique Focus
        try:
            self._save_focus_run(task, steps)
        except Exception:
            pass  # non bloquant

        # Contexte inter-étapes : liste de résumés courts (pas le texte streamed complet)
        step_summaries: dict[int, str] = {}   # step_id → résumé court
        completed_steps: list[int] = []

        # ── Phase 2 : Exécution des étapes ───────────────────────────────
        # On trie les étapes par id pour garantir l'ordre séquentiel correct
        steps_sorted = sorted(steps, key=lambda s: s.get("id", 0))

        for step in steps_sorted:
            step_id    = step.get("id", "?")
            title      = step.get("title", f"Étape {step_id}")
            specialist = step.get("specialist", "coder")
            depends    = [d for d in step.get("depends_on", []) if isinstance(d, int)]

            # Vérifier les dépendances — on signale mais on n'arrête jamais l'exécution
            missing = [d for d in depends if d not in completed_steps]
            msg_suffix = f" (dépendances en cours: {missing})" if missing else ""
            yield ("focus_phase", {
                "phase": "executing",
                "step_id": step_id,
                "specialist": specialist,
                "title": title,
                "message": f"[{specialist.upper()}] {title}{msg_suffix}",
            })

            # Construire le contexte : résumés compressés des étapes déjà complètes
            # On utilise un index {id: step} pour accéder aux titres sans risque d'IndexError
            steps_by_id = {s.get("id"): s for s in steps_sorted}
            ctx_lines: list[str] = []
            for dep_id in depends:
                if dep_id in step_summaries:
                    dep_title = steps_by_id.get(dep_id, {}).get("title", str(dep_id))
                    ctx_lines.append(f"Étape {dep_id} ({dep_title}) : {step_summaries[dep_id]}")
            for sid in completed_steps[-2:]:
                if sid not in depends and sid in step_summaries:
                    sid_title = steps_by_id.get(sid, {}).get("title", str(sid))
                    ctx_lines.append(f"Étape {sid} ({sid_title}) : {step_summaries[sid]}")
            context_str = "\n".join(ctx_lines) if ctx_lines else ""

            sub_model    = self._focus_model(specialist)
            sub_fallback = self._focus_fallback(specialist)
            sub_temp     = self._focus_temp(specialist)
            sub_mt       = self._focus_mt(specialist)
            sub_role     = self._focus_sub(specialist).get("role", "Expert")

            # System prompt de l'étape : base + rôle spécialiste, PAS la tâche complète
            # (la tâche est dans le message user uniquement → pas de duplication)
            step_system = (
                f"{base_sp}\n\n"
                f"RÔLE ACTUEL : {sub_role}.\n"
                "Tu fais partie d'un pipeline multi-agents. "
                "Exécute UNIQUEMENT ta partie, ne récapitule pas le projet entier."
            )
            if context_str:
                step_system += f"\n\nCONTEXTE DES ÉTAPES PRÉCÉDENTES :\n{context_str}"

            step_user = (
                f"TÂCHE GLOBALE : {task}\n\n"
                f"TON TRAVAIL (étape {step_id}/{len(steps)}) : {title}\n"
                f"{step.get('description', title)}\n\n"
                "Instructions : utilise les outils disponibles. Termine par un résumé court (2-3 lignes max) de ce que tu as accompli."
            )

            msgs = [
                {"role": "system", "content": step_system},
                {"role": "user",   "content": step_user},
            ]

            sub_models = [sub_model]
            if sub_fallback and sub_fallback != sub_model:
                sub_models.append(sub_fallback)

            step_parts: list[str] = []
            asked_question = None

            for _tool_round in range(mc.get("max_tool_rounds", 20)):
                had_tool_call = False
                round_ok = False
                for mk in sub_models:
                    if round_ok:
                        break
                    for k in keys:
                        yielded = False
                        try:
                            async for event, data in self.client.chat_stream(
                                msgs, mk,
                                temperature=sub_temp,
                                max_tokens=sub_mt,
                                tools=tools,
                                api_key=k,
                            ):
                                if event == "content":
                                    yielded = True
                                    step_parts.append(data)
                                    full_reply_parts.append(data)
                                    yield ("content", data)
                                elif event == "tool_calls":
                                    had_tool_call = True
                                    for tc in data:
                                        name = tc["function"]["name"]
                                        args = json.loads(tc["function"]["arguments"])
                                        yield ("tool_call", (name, args))
                                        result = await loop.run_in_executor(pool, execute_tool, name, args)
                                        yield ("tool_result", (name, result))
                                        if name == "ask_user" and result.get("data", {}).get("awaiting_answer"):
                                            qid = result["data"]["question_id"]
                                            yield ("question", result["data"])
                                            _question_store[qid] = {
                                                "messages": list(msgs),
                                                "tool_call": tc,
                                                "full_reply_parts": list(full_reply_parts),
                                                "mode": self.current_mode,
                                                "model": sub_model,
                                                "max_tokens": sub_mt,
                                                "tools": tools,
                                                "api_key": self._api_key(),
                                            }
                                            asked_question = qid
                                        else:
                                            msgs.append({"role": "assistant", "content": "", "tool_calls": [tc]})
                                            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": result["summary"][:2000]})
                                    if asked_question:
                                        break
                                elif event == "done":
                                    round_ok = True
                                elif event == "usage":
                                    await self._tu(data)
                                elif event == "error":
                                    if yielded:
                                        yield ("content", "\n\n_[Interruption]_")
                                    yield ("error", data)
                                    round_ok = True
                                    break
                        except Exception as e:
                            if yielded:
                                yield ("content", "\n\n_[Interruption]_")
                            yield ("error", str(e))
                            round_ok = True
                            break
                        if not yielded:
                            await asyncio.sleep(2)
                    if asked_question:
                        break
                if round_ok or asked_question:
                    break
                if not had_tool_call:
                    break

            if asked_question:
                full_reply = "".join(full_reply_parts).strip()
                if full_reply:
                    self.memory.add("user", task)
                    self.memory.add("assistant", full_reply)
                return

            # Résumé compressé de l'étape pour le contexte des étapes suivantes
            # On prend seulement les 400 derniers caractères streamed (la conclusion)
            step_text = "".join(step_parts).strip()
            if step_text:
                # Extraire la fin du texte (là où le résumé se trouve)
                summary_raw = step_text[-600:] if len(step_text) > 600 else step_text
                step_summaries[step_id] = _compress(summary_raw, max_chars=300)
            completed_steps.append(step_id)

            yield ("focus_step_done", {"step_id": step_id, "specialist": specialist, "title": title})

        # ── Phase 3 : Fin ───────────────────────────────────────────────
        yield ("focus_phase", {"phase": "done", "message": "Toutes les étapes terminées."})

        full_reply = "".join(full_reply_parts).strip()
        if full_reply:
            # Sauvegarder une seule fois dans la mémoire (pas à chaque étape)
            self.memory.add("user", task)
            self.memory.add("assistant", full_reply)

    async def resume_from_answer(self, qid: str, answer: str):
        data = _question_store.get(qid)
        if not data:
            yield ("error", "Question introuvable ou expirée")
            return

        msgs = data["messages"]
        tc = data["tool_call"]
        model = data["model"]
        mt = data["max_tokens"]
        tools = data["tools"]
        ak = data["api_key"]
        full_reply_parts = list(data.get("full_reply_parts", []))
        mode = data.get("mode", self.current_mode)

        msgs.append({"role": "assistant", "content": "", "tool_calls": [tc]})
        msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": answer[:2000]})

        del _question_store[qid]

        pool = self._get_pool()
        loop = asyncio.get_event_loop()
        keys = self._all_api_keys() if not ak else [ak]
        models = [model]
        if self._fallback and self._fallback != model:
            models.append(self._fallback)

        asked_question = None

        for tool_round in range(6):
            had_tool_call = False
            ok = False
            for mk in models:
                if ok:
                    break
                for k in keys:
                    yielded = False
                    try:
                        async for event, chunk in self.client.chat_stream(
                            msgs, mk, max_tokens=mt, tools=tools, api_key=k
                        ):
                            if event == "content":
                                yielded = True
                                full_reply_parts.append(chunk)
                                yield ("content", chunk)
                            elif event == "tool_calls":
                                had_tool_call = True
                                for tcc in chunk:
                                    name = tcc["function"]["name"]
                                    args = json.loads(tcc["function"]["arguments"])
                                    yield ("tool_call", (name, args))
                                    result = await loop.run_in_executor(
                                        pool, execute_tool, name, args
                                    )
                                    yield ("tool_result", (name, result))
                                    if name == "ask_user" and result.get("data", {}).get("awaiting_answer"):
                                        nqid = result["data"]["question_id"]
                                        yield ("question", result["data"])
                                        _question_store[nqid] = {
                                            "messages": list(msgs),
                                            "tool_call": tcc,
                                            "full_reply_parts": list(full_reply_parts),
                                            "mode": mode,
                                            "model": model,
                                            "max_tokens": mt,
                                            "tools": tools,
                                            "api_key": ak,
                                        }
                                        asked_question = nqid
                                    else:
                                        msgs.append({
                                            "role": "assistant",
                                            "content": "",
                                            "tool_calls": [tcc],
                                        })
                                        msgs.append({
                                            "role": "tool",
                                            "tool_call_id": tcc["id"],
                                            "content": result["summary"][:2000],
                                        })
                                if asked_question:
                                    break
                            elif event == "done":
                                ok = True
                            elif event == "usage":
                                await self._tu(chunk)
                            elif event == "error":
                                if yielded:
                                    yield ("content", "\n\n_[L'assistant a été interrompu]_")
                                yield ("error", chunk)
                                ok = True
                                break
                    except Exception as e:
                        if yielded:
                            yield ("content", "\n\n_[L'assistant a été interrompu]_")
                        yield ("error", str(e))
                        ok = True
                        break
                    if not yielded:
                        await asyncio.sleep(2)
                if asked_question:
                    break
            if ok or asked_question:
                break
            if not had_tool_call:
                break

        if asked_question:
            return

        full_reply = "".join(full_reply_parts).strip()
        if full_reply:
            self.memory.add("assistant", full_reply)

    async def _gen_work_stream(self, task: str):
        mc    = self._mc()
        model = mc.get("model", "openai/gpt-4o-mini")
        mt    = mc.get("max_tokens") or self.config["rate_limits"].get("max_tokens_per_request", 4096)
        ak    = self._api_key()
        tools = self._tools()
        system_prompt = (
            f"{self._system_prompt()}\n\n"
            "Exécute directement les outils commande par commande.\n"
            "Ne planifie pas. Sois rapide et concis.\n"
            "IMPORTANT: Une fois les outils exécutés, ajoute un résumé de ce que tu viens de faire.\n"
            "Le résumé ne remplace pas ton travail, il le complète à la fin."
        )
        pool = self._get_pool()
        loop = asyncio.get_event_loop()

        # Mettre à jour le système + charger l'historique
        self.memory.add_system(system_prompt)
        self.memory.add("user", task)
        msgs = self.memory.get()

        keys = self._all_api_keys() if not ak else [ak]
        models = [model]
        if self._fallback and self._fallback != model:
            models.append(self._fallback)

        full_reply_parts = []
        asked_question = None

        for tool_round in range(6):
            had_tool_call = False
            ok = False
            for mk in models:
                if ok:
                    break
                for k in keys:
                    yielded = False
                    try:
                        async for event, data in self.client.chat_stream(
                            msgs, mk, max_tokens=mt, tools=tools, api_key=k
                        ):
                            if event == "content":
                                yielded = True
                                full_reply_parts.append(data)
                                yield ("content", data)
                            elif event == "tool_calls":
                                had_tool_call = True
                                for tc in data:
                                    name = tc["function"]["name"]
                                    args = json.loads(tc["function"]["arguments"])
                                    yield ("tool_call", (name, args))
                                    result = await loop.run_in_executor(
                                        pool, execute_tool, name, args
                                    )
                                    yield ("tool_result", (name, result))
                                    if name == "ask_user" and result.get("data", {}).get("awaiting_answer"):
                                        qid = result["data"]["question_id"]
                                        yield ("question", result["data"])
                                        _question_store[qid] = {
                                            "messages": list(msgs),
                                            "tool_call": tc,
                                            "full_reply_parts": list(full_reply_parts),
                                            "mode": self.current_mode,
                                            "model": model,
                                            "max_tokens": mt,
                                            "tools": tools,
                                            "api_key": ak,
                                        }
                                        asked_question = qid
                                    else:
                                        msgs.append({
                                            "role": "assistant",
                                            "content": "",
                                            "tool_calls": [tc],
                                        })
                                        msgs.append({
                                            "role": "tool",
                                            "tool_call_id": tc["id"],
                                            "content": result["summary"][:2000],
                                        })
                                if asked_question:
                                    break
                            elif event == "done":
                                ok = True
                            elif event == "usage":
                                await self._tu(data)
                            elif event == "error":
                                if yielded:
                                    yield ("content", "\n\n_[L\'assistant a été interrompu]_")
                                yield ("error", data)
                                ok = True
                                break
                    except OpenRouterError as e:
                        if yielded:
                            yield ("content", "\n\n_[L\'assistant a été interrompu]_")
                        yield ("error", str(e))
                        ok = True
                        break
                    if not yielded:
                        await asyncio.sleep(2)
                if asked_question:
                    break
            if ok or asked_question:
                break
            if not had_tool_call:
                break

        if asked_question:
            return

        # Sauvegarder en mémoire
        full_reply = "".join(full_reply_parts).strip()
        if full_reply:
            self.memory.add("assistant", full_reply)

    async def _gen_docs_stream(self, task: str):
        mc    = self._mc()
        model = mc.get("model", "openai/gpt-4o-mini")
        ak    = self._api_key()
        keys = self._all_api_keys() if not ak else [ak]
        models = [model]
        if self._fallback and self._fallback != model:
            models.append(self._fallback)

        # Mettre à jour le système + charger l'historique
        self.memory.add_system(self._system_prompt())
        self.memory.add("user", task)
        msgs = self.memory.get()

        full_reply_parts = []

        for mk in models:
            for k in keys:
                yielded = False
                try:
                    async for event, data in self.client.chat_stream(
                        msgs, mk, max_tokens=2048, api_key=k,
                    ):
                        if event == "content":
                            yielded = True
                            full_reply_parts.append(data)
                            yield ("content", data)
                        elif event == "done":
                            pass
                        elif event == "usage":
                            await self._tu(data)
                        elif event == "error":
                            if yielded:
                                yield ("content", "\n\n_[L\'assistant a été interrompu]_")
                            yield ("error", data)
                            break
                except OpenRouterError as e:
                    if yielded:
                        yield ("content", "\n\n_[L\'assistant a été interrompu]_")
                    yield ("error", str(e))
                    break
                if not yielded:
                    await asyncio.sleep(2)

        full_reply = "".join(full_reply_parts).strip()
        if full_reply:
            self.memory.add("assistant", full_reply)

    async def _gen_debug_stream(self, task: str):
        mc    = self._mc()
        model = mc.get("model", "openai/gpt-4o-mini")
        mt    = mc.get("max_tokens") or self.config["rate_limits"].get("max_tokens_per_request", 4096)
        ak    = self._api_key()
        tools = self._tools()
        pool = self._get_pool()
        loop = asyncio.get_event_loop()

        # Mettre à jour le système + charger l'historique
        self.memory.add_system(self._system_prompt())
        self.memory.add("user", task)
        msgs = self.memory.get()

        keys = self._all_api_keys() if not ak else [ak]
        models = [model]
        if self._fallback and self._fallback != model:
            models.append(self._fallback)

        full_reply_parts = []
        asked_question = None

        for tool_round in range(6):
            had_tool_call = False
            ok = False
            for mk in models:
                if ok:
                    break
                for k in keys:
                    yielded = False
                    try:
                        async for event, data in self.client.chat_stream(
                            msgs, mk, max_tokens=mt, tools=tools, api_key=k
                        ):
                            if event == "content":
                                yielded = True
                                full_reply_parts.append(data)
                                yield ("content", data)
                            elif event == "tool_calls":
                                had_tool_call = True
                                for tc in data:
                                    name = tc["function"]["name"]
                                    args = json.loads(tc["function"]["arguments"])
                                    yield ("tool_call", (name, args))
                                    result = await loop.run_in_executor(
                                        pool, execute_tool, name, args
                                    )
                                    yield ("tool_result", (name, result))
                                    if name == "ask_user" and result.get("data", {}).get("awaiting_answer"):
                                        qid = result["data"]["question_id"]
                                        yield ("question", result["data"])
                                        _question_store[qid] = {
                                            "messages": list(msgs),
                                            "tool_call": tc,
                                            "full_reply_parts": list(full_reply_parts),
                                            "mode": self.current_mode,
                                            "model": model,
                                            "max_tokens": mt,
                                            "tools": tools,
                                            "api_key": ak,
                                        }
                                        asked_question = qid
                                    else:
                                        msgs.append({
                                            "role": "assistant",
                                            "content": "",
                                            "tool_calls": [tc],
                                        })
                                        msgs.append({
                                            "role": "tool",
                                            "tool_call_id": tc["id"],
                                            "content": result["summary"][:2000],
                                        })
                                if asked_question:
                                    break
                            elif event == "done":
                                ok = True
                            elif event == "usage":
                                await self._tu(data)
                            elif event == "error":
                                if yielded:
                                    yield ("content", "\n\n_[L\'assistant a été interrompu]_")
                                yield ("error", data)
                                ok = True
                                break
                    except OpenRouterError as e:
                        if yielded:
                            yield ("content", "\n\n_[L\'assistant a été interrompu]_")
                        yield ("error", str(e))
                        ok = True
                        break
                    if not yielded:
                        await asyncio.sleep(2)
                if asked_question:
                    break
            if ok or asked_question:
                break
            if not had_tool_call:
                break

        if asked_question:
            return

        full_reply = "".join(full_reply_parts).strip()
        if full_reply:
            self.memory.add("assistant", full_reply)

    # ------------------------------------------------------------------
    # Mode management
    # ------------------------------------------------------------------

    def auto_detect_mode(self, text: str) -> str | None:
        if not self.auto_detect:
            return None
        t = text.lower()
        debug_kw = ("bug", "erreur", "error", "exception", "crash", "panique", "plante", "fail", "traceback")
        doc_kw   = ("documentation", "docs", "api", "comment", "explique", "définition")
        if any(kw in t for kw in debug_kw):
            return "debug"
        if any(kw in t for kw in doc_kw):
            return "docs"
        return None

    def set_mode(self, mode: str) -> str:
        if mode not in self.modes:
            return f"Mode inconnu. Modes disponibles: {', '.join(self.modes.keys())}"
        self.current_mode = mode
        self._invalidate_mc()
        self._invalidate_sp_cache()
        fallbacks = self._mc().get("fallback_models", [])
        self._fallback = fallbacks[0] if fallbacks else self._mc().get("fallback", "openai/gpt-oss-120b:free")
        self._rebuild()
        return f"Mode: {MODE_META.get(mode, {}).get('label', mode)}"

    # ------------------------------------------------------------------
    # Focus mode helpers
    # ------------------------------------------------------------------

    def _focus_sub(self, specialist: str) -> dict:
        """Retourne la config du sous-modèle Focus demandé."""
        mc = self._mc()
        subs = mc.get("sub_models", {})
        return subs.get(specialist, subs.get("coder", {}))

    def _focus_model(self, specialist: str) -> str:
        sub = self._focus_sub(specialist)
        return sub.get("model", self._model())

    def _focus_fallback(self, specialist: str) -> str:
        sub = self._focus_sub(specialist)
        return sub.get("fallback", self._fallback)

    def _focus_temp(self, specialist: str) -> float:
        sub = self._focus_sub(specialist)
        return sub.get("temperature", 0.3)

    def _focus_mt(self, specialist: str) -> int:
        sub = self._focus_sub(specialist)
        return sub.get("max_tokens", self._mt())

    async def _focus_call(
        self,
        msgs: list,
        specialist: str,
        tools: list | None = None,
        raw: bool = False,
    ) -> tuple[str, dict | None]:
        """Appelle un sous-modèle Focus avec fallback automatique.

        Args:
            raw: si True, retourne le contenu brut sans _clean_response
                 (nécessaire pour le JSON du plan, le parser sinon corrompt les listes)
        """
        model    = self._focus_model(specialist)
        fallback = self._focus_fallback(specialist)
        temp     = self._focus_temp(specialist)
        mt       = self._focus_mt(specialist)
        keys     = self._all_api_keys()
        models   = [model]
        if fallback and fallback != model:
            models.append(fallback)

        last_err: Exception | None = None
        for mk in models:
            model_failed = False
            for k in keys:
                if model_failed:
                    break
                try:
                    if tools:
                        content, usage = await self.client.chat_with_tools(
                            msgs, mk, max_tokens=mt, tools=tools, api_key=k
                        )
                    else:
                        content, usage, _ = await self.client.chat(
                            msgs, mk, temperature=temp, max_tokens=mt, api_key=k
                        )
                    return (content if raw else _clean_response(content)), usage
                except OpenRouterError as e:
                    last_err = e
                    err_str = str(e)
                    if "401" in err_str and ("not found" in err_str.lower() or "modèle" in err_str.lower()):
                        # Ce modèle n'est pas accessible → essayer le modèle suivant directement
                        model_failed = True
                        break
                    if "429" in err_str:
                        await asyncio.sleep(2)
                    continue
        raise OpenRouterError(f"[Focus/{specialist}] Tous modèles/clés échoués: {last_err}")

    async def _focus_plan(self, task: str) -> list[dict]:
        """Phase 1 : le Planner décompose la tâche en étapes d'IMPLÉMENTATION.

        Principe clé : le message utilisateur contient déjà les specs/analyse.
        Le planner NE RELIT PAS les specs — il génère des fichiers/modules à créer.
        raw=True : pas de _clean_response → JSON intact.
        """
        system = (
            "Tu es un architecte qui planifie l'IMPLÉMENTATION concrète d'un projet logiciel.\n\n"
            "CONTEXTE : L'utilisateur t'envoie les specs d'un projet. "
            "Ces specs sont déjà analysées et complètes. "
            "Tu n'as PAS à les résumer, les relister, ni faire d'étape d'analyse.\n\n"
            "TON SEUL RÔLE : produire un plan d'implémentation en JSON.\n"
            "Chaque étape = un fichier ou module à CRÉER/CODER, pas une description.\n\n"
            "INTERDITS ABSOLUS (ces étapes seront rejetées) :\n"
            '- "Définir les exigences", "Lister les fonctionnalités", "Analyser"\n'
            '- "Concevoir l\'architecture" sans code concret à produire\n'
            '- "Documenter", "Résumer", "Récapituler"\n'
            "- Toute étape dont description = répétition des specs de l'utilisateur\n\n"
            "AUTORISÉ :\n"
            '- "Créer Main.java et structure MVC", "Implémenter AuthService.java"\n'
            '- "Créer LoginPanel.java (Swing)", "Implémenter WebSocketClient.java"\n'
            '- "Écrire ContactDAO.java + test", "Intégrer Java Sound API dans AudioCall.java"\n\n'
            "RÈGLES JSON :\n"
            "1. Tableau JSON brut uniquement — pas de markdown, pas de texte autour\n"
            "2. 4 à 8 étapes maximum\n"
            "3. specialist ∈ {coder, stylist, debugger, reviewer}\n"
            "4. La dernière étape = reviewer\n"
            "5. depends_on = IDs des étapes dont celle-ci dépend ([] si indépendante)\n\n"
            "FORMAT EXACT :\n"
            '[{"id":1,"title":"Créer X","description":"Implémenter Y avec Z","specialist":"coder","depends_on":[]},...]'
        )
        msgs = [
            {"role": "system", "content": system},
            {"role": "user",   "content": f"Specs du projet :\n\n{task}"},
        ]
        content, usage = await self._focus_call(msgs, "planner", raw=True)
        await self._tu(usage)

        raw_text = content.strip()
        # Extraire le bloc JSON si entouré de markdown (certains modèles l'ajoutent quand même)
        m = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw_text)
        if m:
            raw_text = m.group(1).strip()
        # Trouver le premier [ et le dernier ] pour isoler le tableau
        start = raw_text.find("[")
        end   = raw_text.rfind("]")
        if start != -1 and end != -1 and end > start:
            raw_text = raw_text[start:end+1]
        try:
            steps = json.loads(raw_text)
            if isinstance(steps, list) and steps:
                # Sanity check : normaliser les champs manquants
                validated = []
                for i, s in enumerate(steps):
                    if not isinstance(s, dict):
                        continue
                    validated.append({
                        "id":          s.get("id", i + 1),
                        "title":       s.get("title", f"Étape {i+1}"),
                        "description": s.get("description", s.get("title", "")),
                        "specialist":  s.get("specialist", "coder") if s.get("specialist") in ("coder","stylist","debugger","reviewer","planner") else "coder",
                        "depends_on":  s.get("depends_on", []),
                    })
                if validated:
                    return validated
        except (json.JSONDecodeError, ValueError):
            pass
        # Fallback : plan minimal si le JSON est illisible
        return [
            {"id": 1, "title": "Analyse & implémentation", "description": task, "specialist": "coder", "depends_on": []},
            {"id": 2, "title": "Revue et validation",       "description": "Vérifier la qualité, la cohérence et corriger les problèmes.", "specialist": "reviewer", "depends_on": [1]},
        ]

    async def _focus_execute_step(
        self,
        step: dict,
        task: str,
        context: str,
        tools: list | None,
    ) -> str:
        """Phase 2 : exécute une étape avec le sous-modèle spécialisé approprié."""
        specialist = step.get("specialist", "coder")
        # Auto-détection si le spécialiste n'est pas défini clairement
        if specialist not in ("coder", "stylist", "debugger", "reviewer", "planner"):
            specialist = _detect_focus_specialist(step.get("description", ""))

        sub = self._focus_sub(specialist)
        role = sub.get("role", "Assistant expert")

        system = (
            f"{self._system_prompt()}\n\n"
            f"Tu es le {role.upper()} dans un pipeline de développement autonome.\n\n"
            f"PROJET GLOBAL : {task}\n\n"
            f"CONTEXTE DES ÉTAPES PRÉCÉDENTES :\n{context or 'Aucun contexte précédent.'}\n\n"
            "RÈGLES :\n"
            "- Exécute UNIQUEMENT ta partie, ne refais pas le travail des autres\n"
            "- Utilise les outils disponibles pour lire/écrire/exécuter\n"
            "- Sois précis et complet\n"
            "- Termine avec un résumé de ce qui a été fait"
        )
        msgs = [
            {"role": "system", "content": system},
            {"role": "user",   "content": f"ÉTAPE {step['id']}: {step['title']}\n\n{step['description']}"},
        ]
        content, usage = await self._focus_call(msgs, specialist, tools=tools)
        await self._tu(usage)
        return content

    def _rebuild(self):
        self.memory.clear()
        self.memory.add_system(self._system_prompt())

    def set_mode_max_tokens(self, value: int) -> str:
        if value < 64 or value > 32_768:
            return "max_tokens doit être entre 64 et 32768."
        self._mc()["max_tokens"] = value
        return f"max_tokens → {value}"

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Focus history
    # ------------------------------------------------------------------

    def _focus_history_path(self) -> Path:
        return _PROJECT_ROOT / "focus_history.json"

    def _load_focus_history(self) -> list[dict]:
        p = self._focus_history_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _save_focus_run(self, task: str, steps: list[dict]) -> str:
        """Sauvegarde un run Focus dans l'historique. Retourne l'ID du run."""
        history = self._load_focus_history()
        run_id = f"focus_{int(time.time())}_{len(history)}"
        history.insert(0, {
            "id":      run_id,
            "task":    task[:200],
            "steps":   steps,
            "ts":      datetime.now().isoformat(),
            "n_steps": len(steps),
        })
        # Garder max 50 runs
        history = history[:50]
        self._focus_history_path().write_text(
            json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return run_id

    def get_focus_history(self) -> list[dict]:
        return self._load_focus_history()

    async def plan_only(self, task: str) -> list[dict]:
        """Génère le plan Focus sans exécuter les étapes."""
        return await self._focus_plan(task)

    def save_session(self, name: str) -> str:
        p = _PROJECT_ROOT / "sessions" / f"{name}.json"
        p.parent.mkdir(exist_ok=True)
        json.dump(
            {
                "name": name,
                "messages": self.memory.messages,
                "mode": self.current_mode,
            },
            p.open("w", encoding="utf-8"),
            **_JSON_ENSURE,
            indent=2,
        )
        return f"Session '{name}' sauvegardée dans {p}."

    def load_session(self, name: str) -> str:
        p = _PROJECT_ROOT / "sessions" / f"{name}.json"
        if not p.exists():
            return f"Session '{name}' introuvable."
        d = json.loads(p.read_text(encoding="utf-8"))
        self.memory.messages = d["messages"]
        self.memory._cc = sum(len(m.get("content") or "") for m in self.memory.messages)
        self.current_mode = d.get("mode", "work")
        self._invalidate_mc()
        self._fallback = self._mc().get("fallback", "openai/gpt-oss-120b:free")
        return f"Session '{name}' chargée ({len(self.memory.messages)} messages)."

    # ------------------------------------------------------------------
    # Stats & misc
    # ------------------------------------------------------------------

    def show_stats(self) -> dict:
        mc = self._mc()
        label = MODE_META.get(self.current_mode, {}).get("label", self.current_mode)
        msgs = self.memory.messages
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        asst_msgs = [m for m in msgs if m.get("role") == "assistant"]
        total_chars = sum(len(m.get("content") or "") for m in msgs)

        first_ts = None
        last_ts = None
        for m in msgs:
            ts = m.get("ts")
            if ts:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts

        duration = ""
        if first_ts and last_ts and first_ts != last_ts:
            from datetime import datetime
            try:
                f = datetime.fromisoformat(first_ts)
                l = datetime.fromisoformat(last_ts)
                delta = l - f
                total_sec = int(delta.total_seconds())
                if total_sec >= 3600:
                    duration = f"{total_sec // 3600}h {(total_sec % 3600) // 60}m"
                elif total_sec >= 60:
                    duration = f"{total_sec // 60}m {total_sec % 60}s"
                else:
                    duration = f"{total_sec}s"
            except Exception:
                pass

        rl = self.config.get("rate_limits", {})
        ctx = self.config.get("context", {})
        return {
            "mode":         label,
            "model":        mc.get("model", "?"),
            "fallback":     self._fallback,
            "max_tokens":   self._mt(),
            "temperature":  mc.get("temperature", 0.7),
            "tools":        mc.get("tools", False),
            "messages": {
                "total":    len(msgs),
                "user":     len(user_msgs),
                "assistant": len(asst_msgs),
                "system":   len(msgs) - len(user_msgs) - len(asst_msgs),
                "total_chars": total_chars,
                "avg_chars_per_msg": round(total_chars / max(len(msgs), 1)),
            },
            "tokens": dict(self.token_usage),
            "rate_limits": {
                "max_rpm":          rl.get("max_requests_per_minute", 20),
                "max_tokens_req":   rl.get("max_tokens_per_request", 2048),
                "max_retries":      rl.get("max_retries", 3),
            },
            "context": {
                "max_chars": self.memory.max_chars,
                "usage_pct": round(total_chars / max(self.memory.max_chars, 1) * 100, 1),
            },
            "duration": duration,
            "first_msg": first_ts,
            "last_msg":  last_ts,
            "agent_name": self.name,
        }

    def clear_memory(self):
        self.memory.clear()
        self._rebuild()

    def undo(self) -> str:
        from .tools import _undo_tool
        result = _undo_tool()
        return result["summary"]

    def redo(self) -> str:
        from .tools import _redo_tool
        result = _redo_tool()
        return result["summary"]


# ---------------------------------------------------------------------------
# Cosmétique
# ---------------------------------------------------------------------------

def logo_to_ascii() -> str:
    return r"""
  __  __       _     _
 |  \/  | ___ | |__ (_)
 | |\/| |/ _ \| '_ \| |
 | |  | | (_) | | | | |
 |_|  |_|\___/|_| |_|_|
"""
