"""MemPalace memory plugin — local-first MemoryProvider backed by the
``mempalace`` CLI (no API key, no MCP).

MemPalace stores conversations and notes as searchable "drawers" in a local
palace. This provider:

  - prefetch()        — ``mempalace search`` before each turn
  - sync_turn()       — writes a turn transcript into the corpus and mines it
  - on_memory_write() — mirrors durable built-in memory into the corpus
  - tools             — mempalace_search / mempalace_status / mempalace_remember

Everything shells out via ``subprocess.run`` with list args (no shell=True).
All operations are best-effort: errors and timeouts are logged and swallowed
so the agent never breaks if MemPalace is slow or missing.

Config in $HERMES_HOME/config.yaml (profile-scoped):
  plugins:
    mempalace:
      cli_path: mempalace                       # binary name or absolute path
      palace_path: ~/.hermes/mempalace/palace
      corpus_path: ~/.hermes/mempalace/hermes-corpus
      wing: hermes
      agent: hermes
      prefetch_limit: 5
      timeout_seconds: 45
      sync_enabled: true
      sync_min_chars: 80
      sync_max_chars: 12000
      sync_skip_platforms: [cron]              # never file these platforms
      sync_skip_session_prefixes: [cron_]      # never file these session ids
      sync_skip_low_value: true                # drop [SILENT]/maintenance reports
      sync_dedup_enabled: true                 # cross-session normalized dedup
      mine_lock_timeout_seconds: 5            # wait for cross-process mine lock
      auto_prefetch_enabled: true             # gate auto-recall on each turn
      prefetch_min_query_chars: 18            # skip shorter queries
      prefetch_min_query_words: 3             # skip queries with fewer words
      prefetch_min_cosine: 0.05               # drop results below this cosine
      prefetch_require_positive_bm25: false   # also require bm25>0 (non-recall)
      prefetch_skip_patterns: [ ... ]         # regex of low-value messages
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import fcntl  # POSIX-only; used for the cross-process mine lock.
    _HAS_FCNTL = True
except Exception:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore
    _HAS_FCNTL = False

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULTS: Dict[str, Any] = {
    "cli_path": "mempalace",
    "palace_path": "~/.hermes/mempalace/palace",
    "corpus_path": "~/.hermes/mempalace/hermes-corpus",
    "wing": "hermes",
    "agent": "hermes",
    "prefetch_limit": 5,
    "timeout_seconds": 45,
    "sync_enabled": True,
    "sync_min_chars": 80,
    "sync_max_chars": 12000,
    # --- Background/cron ingestion guard ------------------------------------
    # The Hermes core cron scheduler hardcodes agent_context="primary"
    # (NousResearch/hermes-agent#9763), so agent_context alone cannot be
    # trusted to keep cron/background sessions out of the corpus. Platform
    # and session-id prefix are honored as independent signals; primary
    # Telegram/CLI ingestion is unaffected.
    "sync_skip_platforms": ["cron"],
    "sync_skip_session_prefixes": ["cron_"],
    # Drop low-value maintenance turns ([SILENT], "Sem novos fatos
    # duráveis", dream/cleanup/doctor report wrappers) before filing.
    "sync_skip_low_value": True,
    # Cross-session dedup keyed by normalized content. O(1) marker lookups
    # under <corpus>/turns/.dedup-index/ — never a corpus scan per turn.
    "sync_dedup_enabled": True,
    # Seconds a mine will wait for the cross-process lock before giving up
    # (another process is mining and will pick up our files anyway). 0 = do
    # not wait, skip immediately if the lock is held.
    "mine_lock_timeout_seconds": 5,
    # --- Auto-recall (prefetch) noise gate ---------------------------------
    # When true, prefetch() runs MemPalace search before each turn. The
    # query is first screened locally (length, skip patterns, symbol-only)
    # and the results are post-filtered by parsed cosine/bm25 so low-value
    # or low-confidence noise never reaches the agent context.
    "auto_prefetch_enabled": True,
    "prefetch_min_query_chars": 18,
    "prefetch_min_query_words": 3,
    "prefetch_min_cosine": 0.05,
    "prefetch_require_positive_bm25": False,
    # Regexes (matched case-insensitively, anchored against the whole,
    # usually short, message) for low-value pt/en messages: greetings,
    # ok/yes/no, thanks, "só testando"/"teste", "continua", slash
    # commands, laughs/acks, and bare status pings. An explicit recall
    # intent (see _RECALL_INTENT) always overrides these.
    "prefetch_skip_patterns": [
        r"^\s*(oi+|ol[áa]|e?\s*a[íi]|eai|al[ôo]|hello+|hi+|hey+|yo+|sup)\b.{0,12}$",
        r"^\s*(bom\s+dia|boa\s+tarde|boa\s+noite|good\s+(morning|afternoon|evening|night))\b.{0,14}$",
        r"^\s*(ok(ay|ey)?|k+|blz|beleza|sim|n[ãa]o|no|yes|yep|nope|sure|"
        r"claro|certo|t[áa]|tudo\s+bem|pode\s+ser|isso\s*a[íi]?|aham|uhum)\s*[.!?…]*$",
        r"^\s*(obrigad[oa]|valeu|vlw|brigad[oa]|thanks?|thank\s+you|thx|ty|"
        r"agrade[çc]o)\s*[.!?…]*$",
        r"^\s*(s[óo]\s+)?(test(e|es|ando|ar|ing)?|testando|teste)\s*[.!?…]*$",
        r"^\s*(continu[ae]|segue|segu[ei]|vai|vamos|go\s*on|next|"
        r"prossi(ga|gue)|manda|bora|segue\s+o\s+jogo)\s*[.!?…]*$",
        r"^\s*/[\w-]+",
        r"^\s*(kk+|k\b|haha+|hehe+|rs+|lol|hmm+|ahn?|oh+|entendi|got\s+it|"
        r"nice|legal|massa|show|top|j[óo]ia|perfeito|[óo]timo|boa)\s*[.!?…]*$",
        r"^\s*(status|ping|pong|ready|pronto|feito|done|funcionou|"
        r"deu\s+certo|ok\s+pode|pode\s+(continuar|seguir|ir)|roda|rode|"
        r"executa|run\s+it|build|deploy)\s*[.!?…]*$",
    ],
}

# Query terms that signal an explicit memory/recall/decision/context ask.
# When any of these match, the prefetch gate is bypassed entirely (we never
# skip, and result filtering also accepts strong bm25 hits).
_RECALL_INTENT = re.compile(
    r"(mem[óo]ria|lembr|relembr|record(a|ar|e|ei)|recall|remember|"
    r"decid(i|iu|imos|ido|ir)|decis[ãa]o|hist[óo]ric|contexto|\bcontext\b|"
    r"[úu]ltima\s+vez|last\s+time|previous(ly)?|anteriormente|"
    r"o\s+que\s+(eu|a\s+gente|n[óo]s)\s+\w+|"
    r"what\s+did\s+(i|we)\s+(decide|say|discuss|do)|"
    r"j[áa]\s+(falamos|conversamos|discutimos|combinamos)|combinamos)",
    re.IGNORECASE | re.UNICODE,
)

# Output bound for prefetch / search tool results (keep context tight).
_MAX_PREFETCH_CHARS = 3500

# Conservative "obvious secret" patterns. Matching any of these skips the
# write entirely — we never want credentials filed into a searchable palace.
_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9]{16,}"),          # OpenAI/Stripe-style
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                      # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),              # GitHub tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),            # Slack tokens
    re.compile(
        r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|"
        r"client[_-]?secret|password)\b\s*[:=]\s*\S{6,}"
    ),
    re.compile(r"(?i)(^|[\s/\\])\.env(\.[a-z]+)?(\s|$|:)"),    # .env file refs
]


def _looks_like_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS) if text else False


# Agent contexts that must never file turns. The core documents
# "primary" / "subagent" / "cron" / "flush"; anything non-primary is
# background by definition.
_PRIMARY_AGENT_CONTEXTS = ("primary", "")

# The cron runner prepends this delivery wrapper to the prompt it hands the
# agent. Its presence in the user content marks a cron/background session
# even when platform/session_id/agent_context are all mis-reported
# (belt-and-suspenders for NousResearch/hermes-agent#9763).
_CRON_WRAPPER_MARKERS = (
    re.compile(r"you are running as a scheduled cron job", re.IGNORECASE),
    re.compile(r"respond with exactly \"?\[SILENT\]\"?", re.IGNORECASE),
)

# Low-value assistant replies: suppressed-delivery sentinels and the exact
# "nothing to report" phrasings used by the maintenance crons. Matched
# against the whole trimmed assistant content so a material turn that merely
# *mentions* [SILENT] is preserved.
_LOW_VALUE_ASSISTANT_PATTERNS = (
    re.compile(r"^\[SILENT\]$", re.IGNORECASE),
    re.compile(
        r"^sem\s+novos\s+fatos\s+dur[áa]veis\b[^\n]{0,60}$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^sem\s+limpeza\s+segura\s+na\s+mem[óo]ria\s+curta\b[^\n]{0,60}$",
        re.IGNORECASE,
    ),
    re.compile(r"^no\s+new\s+durable\s+facts\b[^\n]{0,60}$", re.IGNORECASE),
    # Deterministic dream-report rendering (engine.render_report).
    re.compile(r"^#\s*MemPalace Dream Report\b", re.IGNORECASE),
    # Cron dreaming report wrapper: starts with the fixed first bullet.
    re.compile(r"^-\s*mem[óo]rias\s+salvas\s*:", re.IGNORECASE),
)

# Cleanup-report bullets ("removi X / traduzi Y / compactei Z / eliminei
# duplicação W"). A reply made only of these bullets is a repeated
# maintenance report, not durable knowledge.
_CLEANUP_REPORT_LINE = re.compile(
    r"^-\s*(removi|traduzi|compactei|eliminei|mantive|deixei)\b",
    re.IGNORECASE,
)


def _is_low_value_reply(assistant_content: str) -> bool:
    """True when the assistant content is a maintenance/report wrapper
    with no durable value ([SILENT], "Sem novos fatos duráveis", dream or
    cleanup reports). Conservative: anything unrecognized is kept."""
    text = (assistant_content or "").strip()
    if not text:
        return False
    if any(p.search(text) for p in _LOW_VALUE_ASSISTANT_PATTERNS):
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and all(_CLEANUP_REPORT_LINE.match(ln) for ln in lines):
        return True
    return False


_DEDUP_WS_RE = re.compile(r"\s+")


def _normalize_turn_text(user_content: str, assistant_content: str) -> str:
    """Canonical form for cross-session dedup: casefold + collapse all
    whitespace. Materially different turns (different words) never
    collide; formatting/case variants of the same content do."""
    combined = f"{user_content}\n{assistant_content}"
    return _DEDUP_WS_RE.sub(" ", combined).casefold().strip()


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_plugin_config() -> dict:
    """Read plugins.mempalace from $HERMES_HOME/config.yaml, merged onto
    defaults. Never raises — falls back to defaults on any error."""
    cfg = dict(_DEFAULTS)
    try:
        from hermes_constants import get_hermes_home
        from hermes_cli.config import cfg_get
        config_path = get_hermes_home() / "config.yaml"
        if config_path.exists():
            import yaml
            with open(config_path, encoding="utf-8-sig") as f:
                all_config = yaml.safe_load(f) or {}
            user = cfg_get(all_config, "plugins", "mempalace", default={}) or {}
            if isinstance(user, dict):
                cfg.update(user)
    except Exception as e:
        logger.debug("MemPalace config load failed, using defaults: %s", e)
    return cfg


def _expand(path: str, hermes_home: str) -> str:
    """Expand ~, $HERMES_HOME and env vars in a configured path."""
    if not isinstance(path, str):
        return path
    path = path.replace("$HERMES_HOME", hermes_home).replace("${HERMES_HOME}", hermes_home)
    return str(Path(os.path.expandvars(os.path.expanduser(path))))


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

SEARCH_SCHEMA = {
    "name": "mempalace_search",
    "description": (
        "Search the local MemPalace long-term memory (past conversations, "
        "notes, and durable memories). Use for recall across sessions: "
        "preferences, decisions, prior work, people, projects."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for (exact words work best)."},
            "results": {"type": "integer", "description": "Max results (default: configured prefetch_limit)."},
            "wing": {"type": "string", "description": "Optional wing/project to scope the search."},
        },
        "required": ["query"],
    },
}

STATUS_SCHEMA = {
    "name": "mempalace_status",
    "description": "Show what is filed in the MemPalace palace (wings, rooms, drawer counts).",
    "parameters": {"type": "object", "properties": {}},
}

REMEMBER_SCHEMA = {
    "name": "mempalace_remember",
    "description": (
        "Explicitly store an important fact or note into MemPalace long-term "
        "memory. Use for durable information the user expects you to recall "
        "in future sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The information to remember."},
            "title": {"type": "string", "description": "Optional short title for the note."},
        },
        "required": ["content"],
    },
}


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class MemPalaceMemoryProvider(MemoryProvider):
    """Local memory backed by the mempalace CLI."""

    def __init__(self, config: Optional[dict] = None):
        self._config = config or _load_plugin_config()
        self._session_id = ""
        self._hermes_home = ""
        self._platform = ""
        self._agent_context = "primary"
        self._cli = ""
        self._palace = ""
        self._corpus = ""
        self._wing = str(self._config.get("wing", "hermes"))
        self._agent = str(self._config.get("agent", "hermes"))
        self._timeout = int(self._config.get("timeout_seconds", 45))
        self._prefetch_limit = int(self._config.get("prefetch_limit", 5))
        self._sync_enabled = bool(self._config.get("sync_enabled", True))
        self._sync_min = int(self._config.get("sync_min_chars", 80))
        self._sync_max = int(self._config.get("sync_max_chars", 12000))
        self._sync_skip_platforms = self._config_str_list(
            "sync_skip_platforms", lowercase=True)
        self._sync_skip_session_prefixes = self._config_str_list(
            "sync_skip_session_prefixes")
        self._sync_skip_low_value = bool(
            self._config.get("sync_skip_low_value", True))
        self._sync_dedup = bool(self._config.get("sync_dedup_enabled", True))
        try:
            self._mine_lock_timeout = max(
                0.0, float(self._config.get("mine_lock_timeout_seconds", 5))
            )
        except (TypeError, ValueError):
            self._mine_lock_timeout = 5.0
        # --- Auto-recall noise gate ---
        self._auto_prefetch = bool(self._config.get("auto_prefetch_enabled", True))
        try:
            self._prefetch_min_chars = max(
                0, int(self._config.get("prefetch_min_query_chars", 18)))
        except (TypeError, ValueError):
            self._prefetch_min_chars = 18
        try:
            self._prefetch_min_words = max(
                0, int(self._config.get("prefetch_min_query_words", 3)))
        except (TypeError, ValueError):
            self._prefetch_min_words = 3
        try:
            self._prefetch_min_cosine = float(
                self._config.get("prefetch_min_cosine", 0.05))
        except (TypeError, ValueError):
            self._prefetch_min_cosine = 0.05
        self._prefetch_require_bm25 = bool(
            self._config.get("prefetch_require_positive_bm25", False))
        raw_patterns = self._config.get(
            "prefetch_skip_patterns", _DEFAULTS["prefetch_skip_patterns"])
        if not isinstance(raw_patterns, (list, tuple)):
            raw_patterns = _DEFAULTS["prefetch_skip_patterns"]
        self._skip_patterns = []
        for pat in raw_patterns:
            try:
                self._skip_patterns.append(
                    re.compile(str(pat), re.IGNORECASE | re.UNICODE))
            except re.error as e:
                logger.debug("MemPalace: bad prefetch_skip_pattern %r: %s", pat, e)

        # Process-local guard: prevents this instance from piling up threads.
        self._mine_lock = threading.Lock()
        # Cross-process lock file path, resolved in initialize().
        self._mine_lock_path = ""
        self._threads: List[threading.Thread] = []

    def _config_str_list(self, key: str, *, lowercase: bool = False) -> tuple:
        """Read a list-of-strings config key, falling back to the default
        on any malformed value. Never raises."""
        raw = self._config.get(key, _DEFAULTS.get(key, []))
        if not isinstance(raw, (list, tuple)):
            raw = _DEFAULTS.get(key, [])
        out = []
        for item in raw:
            text = str(item).strip()
            if text:
                out.append(text.lower() if lowercase else text)
        return tuple(out)

    @property
    def name(self) -> str:
        return "mempalace"

    # -- Availability --------------------------------------------------------

    def _resolve_cli(self) -> str:
        """Resolve the mempalace binary: configured path, PATH, or the
        common ~/.local/bin install location."""
        configured = str(self._config.get("cli_path", "mempalace"))
        if os.path.isabs(configured):
            return configured if os.path.exists(configured) else ""
        found = shutil.which(configured)
        if found:
            return found
        fallback = Path.home() / ".local" / "bin" / "mempalace"
        return str(fallback) if fallback.exists() else ""

    def is_available(self) -> bool:
        """True only when the mempalace CLI exists and ``--version`` works."""
        cli = self._resolve_cli()
        if not cli:
            return False
        try:
            proc = subprocess.run(
                [cli, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            return proc.returncode == 0 and "mempalace" in (
                (proc.stdout or "") + (proc.stderr or "")
            ).lower()
        except Exception as e:
            logger.debug("MemPalace --version check failed: %s", e)
            return False

    # -- Lifecycle -----------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id or ""
        self._platform = kwargs.get("platform", "")
        self._agent_context = kwargs.get("agent_context", "primary")
        try:
            from hermes_constants import get_hermes_home
            self._hermes_home = str(get_hermes_home())
        except Exception:
            self._hermes_home = kwargs.get("hermes_home") or str(Path.home() / ".hermes")
        self._hermes_home = kwargs.get("hermes_home") or self._hermes_home

        self._cli = self._resolve_cli()
        self._palace = _expand(str(self._config.get("palace_path")), self._hermes_home)
        self._corpus = _expand(str(self._config.get("corpus_path")), self._hermes_home)

        # Create the corpus subdirectories we write into.
        for sub in ("turns", "manual"):
            try:
                Path(self._corpus, sub).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning("MemPalace could not create %s/%s: %s", self._corpus, sub, e)

        # Resolve the cross-process mine lock file. Prefer the corpus dir
        # (we just ensured it exists); fall back to the palace dir.
        self._mine_lock_path = self._resolve_mine_lock_path()

    def _resolve_mine_lock_path(self) -> str:
        """Pick a stable path for the inter-process mine lock file. Tries
        the corpus dir first, then the palace dir, then a temp fallback."""
        candidates = []
        if self._corpus:
            candidates.append(Path(self._corpus, ".mempalace-mine.lock"))
        if self._palace:
            candidates.append(Path(self._palace, ".hermes-mine.lock"))
        for cand in candidates:
            try:
                cand.parent.mkdir(parents=True, exist_ok=True)
                return str(cand)
            except Exception as e:
                logger.debug("MemPalace lock dir %s unusable: %s", cand.parent, e)
        # Last resort: a per-palace temp file so we still serialize on one host.
        tag = hashlib.sha256(
            (self._palace or self._corpus or "mempalace").encode("utf-8", "replace")
        ).hexdigest()[:12]
        import tempfile
        return str(Path(tempfile.gettempdir(), f"mempalace-mine-{tag}.lock"))

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "",
                          reset: bool = False, **kwargs) -> None:
        self._session_id = new_session_id or self._session_id

    def system_prompt_block(self) -> str:
        if not self._cli:
            return ""
        return (
            "# MemPalace Memory\n"
            "Local long-term memory is active. Past conversations and notes "
            "are auto-filed and searchable.\n"
            "Use mempalace_search to recall prior context, mempalace_remember "
            "to store durable facts, mempalace_status to inspect what is filed."
        )

    # -- CLI helper ----------------------------------------------------------

    def _run_cli(self, args: List[str], timeout: Optional[int] = None) -> Optional[subprocess.CompletedProcess]:
        """Run ``mempalace --palace <palace> <args...>``. Returns the
        CompletedProcess, or None on timeout/spawn error (logged, not raised)."""
        if not self._cli:
            return None
        cmd = [self._cli, "--palace", self._palace, *args]
        env = dict(os.environ)
        if self._hermes_home:
            env["HERMES_HOME"] = self._hermes_home
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout or self._timeout, env=env,
            )
        except subprocess.TimeoutExpired:
            logger.warning("MemPalace command timed out: %s", " ".join(args[:2]))
            return None
        except Exception as e:
            logger.debug("MemPalace command failed (%s): %s", " ".join(args[:2]), e)
            return None

    def _acquire_mine_filelock(self):
        """Acquire the inter-process mine lock. Returns an open file object
        holding the lock (caller must close it to release) or None if the
        lock could not be acquired / fcntl is unavailable.

        With fcntl available, waits up to ``mine_lock_timeout_seconds`` for
        the lock; if it cannot be taken in that window we return None and the
        caller skips mining — another process holds the lock and its mine
        scans the whole corpus, so our freshly written files are still picked
        up. Without fcntl we cannot serialize across processes; we log once
        and rely on the process-local thread lock only."""
        if not _HAS_FCNTL:
            logger.warning(
                "MemPalace: fcntl unavailable; mine serialized within this "
                "process only (cross-process mines may still collide)"
            )
            return None
        if not self._mine_lock_path:
            return None
        try:
            fh = open(self._mine_lock_path, "a+")
        except Exception as e:
            logger.debug("MemPalace could not open mine lock %s: %s",
                         self._mine_lock_path, e)
            return None
        deadline = time.monotonic() + self._mine_lock_timeout
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fh
            except OSError:
                if time.monotonic() >= deadline:
                    try:
                        fh.close()
                    except Exception:
                        pass
                    logger.debug(
                        "MemPalace mine lock busy; skipping (another mine is "
                        "running and will pick up new files)"
                    )
                    return None
                time.sleep(0.2)

    def _mine_corpus_async(self) -> None:
        """Mine the corpus into the palace in a background thread (convos
        mode). Serialized within this process by a thread lock and across
        processes by an fcntl file lock, so concurrent mines never collide
        and corrupt the HNSW segment."""
        if not self._cli:
            return

        # Drop references to finished threads so the list cannot grow
        # without bound across a long-running session.
        self._threads = [t for t in self._threads if t.is_alive()]
        if any(t.name == "mempalace-mine" for t in self._threads):
            return  # a mine thread is already in flight for this instance

        def _run():
            # Process-local guard first: cheap, and prevents this instance
            # from stacking threads if many turns arrive quickly.
            if not self._mine_lock.acquire(blocking=False):
                return  # a mine is already running here; it covers new files
            lock_fh = None
            try:
                lock_fh = self._acquire_mine_filelock()
                if lock_fh is None and _HAS_FCNTL:
                    return  # another process is mining; it picks up our files
                self._run_cli([
                    "mine", self._corpus,
                    "--mode", "convos",
                    "--wing", self._wing,
                    "--agent", self._agent,
                    "--limit", "0",
                ], timeout=max(self._timeout, 120))
            except Exception as e:
                logger.debug("MemPalace mine failed: %s", e)
            finally:
                if lock_fh is not None:
                    try:
                        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                    try:
                        lock_fh.close()
                    except Exception:
                        pass
                self._mine_lock.release()

        t = threading.Thread(target=_run, daemon=True, name="mempalace-mine")
        t.start()
        self._threads.append(t)

    # -- Prefetch ------------------------------------------------------------

    def _has_recall_intent(self, query: str) -> bool:
        """True when the user explicitly asks for memory/history/recall/
        decision/context. Such queries bypass the skip gate entirely."""
        try:
            return bool(_RECALL_INTENT.search(query))
        except Exception:
            return False

    def _should_skip_prefetch(self, query: str) -> bool:
        """Decide whether a query is low-value enough to skip MemPalace.
        Callers must check _has_recall_intent() first — this never sees an
        explicit recall ask. Never raises."""
        q = (query or "").strip()
        if not q:
            return True
        # Symbol/emoji/punctuation-only (no unicode letters at all).
        if not re.sub(r"[\W\d_]+", "", q, flags=re.UNICODE):
            return True
        if len(q) < self._prefetch_min_chars:
            return True
        if len(q.split()) < self._prefetch_min_words:
            return True
        for pat in self._skip_patterns:
            try:
                if pat.search(q):
                    return True
            except Exception:
                continue
        return False

    def _keep_block(self, cosine: Optional[float], bm25: Optional[float],
                    recall: bool) -> bool:
        """Keep a parsed result block if it clears the confidence bar."""
        c = cosine if cosine is not None else -1.0
        b = bm25 if bm25 is not None else 0.0
        keep = c >= self._prefetch_min_cosine
        if recall and b > 0:
            keep = True
        if self._prefetch_require_bm25 and not recall:
            keep = keep and (b > 0)
        return keep

    def _filter_search_output(self, out: str, recall: bool) -> str:
        """Drop low-confidence result blocks from raw search output. On any
        unexpected/unparseable format, returns the input unchanged so the
        agent is never broken by a CLI output change."""
        try:
            marks = list(re.finditer(r"(?m)^[ \t]*\[\d+\]", out))
            if not marks:
                return out  # unknown format — pass through unchanged
            header = out[: marks[0].start()].rstrip()
            kept: List[str] = []
            parsed_any = False
            for i, m in enumerate(marks):
                end = marks[i + 1].start() if i + 1 < len(marks) else len(out)
                block = out[m.start():end].rstrip()
                # Drop the CLI's trailing rule; we re-insert our own between
                # kept blocks so dropped ones don't leave orphan separators.
                block = re.sub(r"(?m)^[ \t]*[─-]{6,}[ \t]*$", "", block).rstrip()
                mc = re.search(r"cosine\s*=\s*(-?\d+(?:\.\d+)?)", block)
                mb = re.search(r"bm25\s*=\s*(-?\d+(?:\.\d+)?)", block)
                cosine = float(mc.group(1)) if mc else None
                bm25 = float(mb.group(1)) if mb else None
                if cosine is not None or bm25 is not None:
                    parsed_any = True
                if self._keep_block(cosine, bm25, recall):
                    kept.append(block)
            if not parsed_any:
                return out  # scores absent — don't silently drop everything
            if not kept:
                return ""
            sep = "\n\n  " + ("─" * 56) + "\n\n"
            body = sep.join(kept)
            return (f"{header}\n\n{body}" if header else body).strip()
        except Exception as e:
            logger.debug("MemPalace prefetch filter failed, passing raw: %s", e)
            return out

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._cli or not query or not query.strip():
            return ""
        q = query.strip()
        if not self._auto_prefetch:
            return ""
        recall = self._has_recall_intent(q)
        if not recall and self._should_skip_prefetch(q):
            return ""
        proc = self._run_cli(
            ["search", q,
             "--wing", self._wing,
             "--results", str(self._prefetch_limit)],
            timeout=min(self._timeout, 30),
        )
        if proc is None or proc.returncode != 0:
            if proc is not None:
                logger.debug("MemPalace search rc=%s: %s", proc.returncode,
                             (proc.stderr or "")[:200])
            return ""
        out = (proc.stdout or "").strip()
        if not out or "No results" in out or "0 results" in out.lower():
            return ""
        out = self._filter_search_output(out, recall).strip()
        if not out:
            return ""
        if len(out) > _MAX_PREFETCH_CHARS:
            out = out[:_MAX_PREFETCH_CHARS].rstrip() + "\n[... truncated]"
        return f"## MemPalace Memory\n```\n{out}\n```"

    # -- Sync ----------------------------------------------------------------

    def _dedup_index_dir(self) -> Path:
        return Path(self._corpus, "turns", ".dedup-index")

    def _normalized_turn_digest(self, user_content: str, assistant_content: str) -> str:
        """Content-only digest over the normalized turn text. Independent
        of session id so exact/near-exact repeats across sessions collide."""
        normalized = _normalize_turn_text(
            user_content[: self._sync_max], assistant_content[: self._sync_max]
        )
        return hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()[:24]

    def _claim_content_marker(self, digest: str) -> Optional[bool]:
        """Atomically claim the dedup marker for ``digest``.

        Returns True when this process claimed it first (proceed to write),
        False when it already exists (duplicate content — skip), or None
        when the index is unusable (fail open: file the turn). ``open(x)``
        is atomic on POSIX, so two concurrent processes can never both
        claim the same marker; no directory scan is involved."""
        try:
            index_dir = self._dedup_index_dir()
            index_dir.mkdir(parents=True, exist_ok=True)
            with open(index_dir / digest, "x", encoding="utf-8") as fh:
                fh.write(
                    f"{datetime.now(timezone.utc).isoformat()} "
                    f"{self._session_id or 'nosession'}\n"
                )
            return True
        except FileExistsError:
            return False
        except Exception as e:
            logger.debug("MemPalace dedup index unavailable: %s", e)
            return None

    def _release_content_marker(self, digest: str) -> None:
        """Best-effort removal of a claimed marker (transcript write failed,
        so the content is not actually filed and must stay claimable)."""
        try:
            (self._dedup_index_dir() / digest).unlink()
        except Exception:
            pass

    def _write_transcript(self, user_content: str, assistant_content: str) -> Optional[Path]:
        """Write a turn transcript into <corpus>/turns/. Returns the path,
        or None if skipped (too short, looks like a secret, duplicate
        content, or write error). Dedup is content-keyed across sessions
        via an O(1) atomic marker index — never a corpus scan."""
        combined = f"{user_content}\n{assistant_content}".strip()
        if len(combined) < self._sync_min:
            return None
        if _looks_like_secret(combined):
            logger.info("MemPalace: skipping turn that looks like it contains secrets")
            return None

        u = user_content[: self._sync_max]
        a = assistant_content[: self._sync_max]

        sid = self._session_id or "nosession"
        digest = self._normalized_turn_digest(user_content, assistant_content)

        claimed: Optional[bool] = None
        if self._sync_dedup:
            claimed = self._claim_content_marker(digest)
            if claimed is False:
                logger.debug(
                    "MemPalace: skipping duplicate turn content (%s)", digest
                )
                return None

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        fname = f"turn-{ts}-{sid[:8]}-{digest[:12]}.md"
        dest = Path(self._corpus, "turns", fname)

        iso = datetime.now(timezone.utc).isoformat()
        body = (
            f"# Hermes Turn\n\n"
            f"- session_id: {sid}\n"
            f"- timestamp: {iso}\n"
            f"- platform: {self._platform or 'cli'}\n\n"
            f"## User\n\n{u}\n\n"
            f"## Assistant\n\n{a}\n"
        )
        try:
            dest.write_text(body, encoding="utf-8")
            return dest
        except Exception as e:
            logger.warning("MemPalace could not write transcript: %s", e)
            if claimed is True:
                self._release_content_marker(digest)
            return None

    def _is_background_session(self, session_id: str, user_content: str) -> bool:
        """True for cron/subagent/background sessions that must never be
        filed. agent_context alone is not trustworthy — the core cron
        scheduler hardcodes agent_context="primary" (upstream
        NousResearch/hermes-agent#9763) — so platform, session-id prefix
        and the cron delivery wrapper are honored as independent signals.
        Primary Telegram/CLI turns match none of them."""
        if self._agent_context not in _PRIMARY_AGENT_CONTEXTS:
            return True
        if (self._platform or "").strip().lower() in self._sync_skip_platforms:
            return True
        sid = session_id or self._session_id or ""
        if any(sid.startswith(prefix) for prefix in self._sync_skip_session_prefixes):
            return True
        if any(p.search(user_content) for p in _CRON_WRAPPER_MARKERS):
            return True
        return False

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self._sync_enabled or not self._cli:
            return
        user_content = user_content or ""
        assistant_content = assistant_content or ""
        if self._is_background_session(session_id, user_content):
            return  # never file cron/subagent/flush/background turns
        if self._sync_skip_low_value and _is_low_value_reply(assistant_content):
            return  # maintenance/report wrappers carry no durable value
        path = self._write_transcript(user_content, assistant_content)
        if path is not None:
            self._mine_corpus_async()

    # -- Memory mirroring ----------------------------------------------------

    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: Optional[Dict[str, Any]] = None) -> None:
        """Mirror durable built-in memory writes into <corpus>/manual/.
        'remove' is a no-op (we do not delete from the palace here)."""
        if not self._cli or action == "remove" or not content:
            return
        if _looks_like_secret(content):
            logger.info("MemPalace: skipping memory mirror that looks like a secret")
            return
        self._write_manual_note(content, title=f"memory:{target}:{action}")
        self._mine_corpus_async()

    def _write_manual_note(self, content: str, title: str = "") -> Optional[Path]:
        content = (content or "")[: self._sync_max]
        digest = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()[:12]
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        fname = f"note-{ts}-{digest}.md"
        dest = Path(self._corpus, "manual", fname)
        try:
            existing = list(Path(self._corpus, "manual").glob(f"note-*-{digest}.md"))
            if existing:
                return existing[0]
        except Exception:
            pass
        iso = datetime.now(timezone.utc).isoformat()
        heading = title.strip() or "Hermes Note"
        body = (
            f"# {heading}\n\n"
            f"- session_id: {self._session_id or 'nosession'}\n"
            f"- timestamp: {iso}\n\n"
            f"{content}\n"
        )
        try:
            dest.write_text(body, encoding="utf-8")
            return dest
        except Exception as e:
            logger.warning("MemPalace could not write manual note: %s", e)
            return None

    # -- Tools ---------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH_SCHEMA, STATUS_SCHEMA, REMEMBER_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if not self._cli:
            return tool_error("MemPalace CLI not available")
        try:
            if tool_name == "mempalace_search":
                return self._tool_search(args)
            if tool_name == "mempalace_status":
                return self._tool_status()
            if tool_name == "mempalace_remember":
                return self._tool_remember(args)
            return tool_error(f"Unknown tool: {tool_name}")
        except Exception as e:
            return tool_error(str(e))

    def _tool_search(self, args: Dict[str, Any]) -> str:
        query = (args.get("query") or "").strip()
        if not query:
            return tool_error("query is required")
        results = int(args.get("results") or self._prefetch_limit)
        wing = (args.get("wing") or self._wing).strip()
        proc = self._run_cli(
            ["search", query, "--wing", wing, "--results", str(results)],
            timeout=min(self._timeout, 30),
        )
        if proc is None:
            return tool_error("MemPalace search timed out")
        if proc.returncode != 0:
            return tool_error(f"MemPalace search failed: {(proc.stderr or '').strip()[:300]}")
        out = (proc.stdout or "").strip()
        if len(out) > _MAX_PREFETCH_CHARS:
            out = out[:_MAX_PREFETCH_CHARS].rstrip() + "\n[... truncated]"
        return json.dumps({"query": query, "wing": wing, "results": out}, ensure_ascii=False)

    def _tool_status(self) -> str:
        proc = self._run_cli(["status"], timeout=min(self._timeout, 30))
        if proc is None:
            return tool_error("MemPalace status timed out")
        if proc.returncode != 0:
            return tool_error(f"MemPalace status failed: {(proc.stderr or '').strip()[:300]}")
        return json.dumps({
            "palace": self._palace,
            "wing": self._wing,
            "status": (proc.stdout or "").strip()[:_MAX_PREFETCH_CHARS],
        }, ensure_ascii=False)

    def _tool_remember(self, args: Dict[str, Any]) -> str:
        content = (args.get("content") or "").strip()
        if not content:
            return tool_error("content is required")
        if _looks_like_secret(content):
            return tool_error("Refusing to store content that appears to contain secrets")
        path = self._write_manual_note(content, title=(args.get("title") or "").strip())
        if path is None:
            return tool_error("Could not write the note")
        self._mine_corpus_async()
        return json.dumps({
            "status": "stored",
            "file": str(path),
            "message": "Note filed into MemPalace corpus; mining in background.",
        }, ensure_ascii=False)

    # -- Shutdown ------------------------------------------------------------

    def shutdown(self) -> None:
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=10.0)

    # -- Setup (hermes memory setup) -----------------------------------------

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {"key": "cli_path", "description": "mempalace binary (name on PATH or absolute path)",
             "default": _DEFAULTS["cli_path"]},
            {"key": "palace_path", "description": "Where the MemPalace palace lives",
             "default": _DEFAULTS["palace_path"]},
            {"key": "corpus_path", "description": "Corpus directory Hermes writes transcripts/notes into",
             "default": _DEFAULTS["corpus_path"]},
            {"key": "wing", "description": "Wing (project) name", "default": _DEFAULTS["wing"]},
            {"key": "agent", "description": "Agent name recorded on drawers", "default": _DEFAULTS["agent"]},
            {"key": "prefetch_limit", "description": "Max search results prefetched per turn",
             "default": str(_DEFAULTS["prefetch_limit"])},
            {"key": "timeout_seconds", "description": "CLI timeout in seconds",
             "default": str(_DEFAULTS["timeout_seconds"])},
            {"key": "sync_enabled", "description": "Auto-file conversation turns",
             "default": "true", "choices": ["true", "false"]},
            {"key": "sync_skip_low_value",
             "description": "Skip low-value maintenance turns ([SILENT], 'Sem novos fatos duráveis', cleanup/dream reports)",
             "default": "true", "choices": ["true", "false"]},
            {"key": "sync_dedup_enabled",
             "description": "Deduplicate turns by normalized content across sessions",
             "default": "true", "choices": ["true", "false"]},
            {"key": "mine_lock_timeout_seconds",
             "description": "Seconds to wait for the cross-process mine lock before skipping",
             "default": str(_DEFAULTS["mine_lock_timeout_seconds"])},
            {"key": "auto_prefetch_enabled",
             "description": "Auto-recall from MemPalace before each turn",
             "default": "true", "choices": ["true", "false"]},
            {"key": "prefetch_min_query_chars",
             "description": "Skip auto-recall for queries shorter than this many chars",
             "default": str(_DEFAULTS["prefetch_min_query_chars"])},
            {"key": "prefetch_min_query_words",
             "description": "Skip auto-recall for queries with fewer words than this",
             "default": str(_DEFAULTS["prefetch_min_query_words"])},
            {"key": "prefetch_min_cosine",
             "description": "Drop prefetched results with cosine below this",
             "default": str(_DEFAULTS["prefetch_min_cosine"])},
            {"key": "prefetch_require_positive_bm25",
             "description": "Also require bm25>0 for non-recall queries",
             "default": "false", "choices": ["true", "false"]},
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        config_path = Path(hermes_home) / "config.yaml"
        try:
            import yaml
            existing = {}
            if config_path.exists():
                with open(config_path, encoding="utf-8-sig") as f:
                    existing = yaml.safe_load(f) or {}
            existing.setdefault("plugins", {})
            existing["plugins"]["mempalace"] = values
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(existing, f, default_flow_style=False)
        except Exception as e:
            logger.warning("MemPalace save_config failed: %s", e)


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register MemPalace as a memory provider plugin."""
    ctx.register_memory_provider(MemPalaceMemoryProvider())
