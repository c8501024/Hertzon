"""Register the Hertzon MCP server with AI assistants (Claude Code, Claude desktop app, Codex, VS Code).

Used by Tools > AI Assistants and by ``python -m hertzon.mcp --install <client>``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

NAME = "hertzon"
ROOT = Path(__file__).resolve().parents[2]
CLIENTS = ("claude", "desktop", "codex", "vscode")
TITLES = {"claude": "Claude Code", "desktop": "Claude desktop app", "codex": "Codex",
          "vscode": "VS Code (Copilot agent mode)"}


def python_exe() -> str:
    """The interpreter of this environment with a console (pythonw.exe has no usable stdio)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        exe = exe.parent / "python.exe"
    return str(exe)


def server_spec() -> dict:
    return {"command": python_exe(), "args": ["-m", "hertzon.mcp"], "env": {"PYTHONPATH": str(ROOT)}}


# ---- Claude Code ---------------------------------------------------------------------------------
def _claude_cli() -> str | None:
    """The claude command; programs started from the start menu often lack the PATH of a terminal, so the
    usual install locations are tried as well (native installer, npm, local install, Homebrew)."""
    cli = shutil.which("claude")
    if cli:
        return cli
    home = Path.home()
    cands = [home / ".local" / "bin" / "claude", home / ".claude" / "local" / "claude",
             home / ".npm-global" / "bin" / "claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")]
    if os.name == "nt":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        cands = [home / ".local" / "bin" / "claude.exe", appdata / "npm" / "claude.cmd",
                 home / ".claude" / "local" / "claude.cmd", home / ".claude" / "local" / "claude.exe",
                 local / "Programs" / "claude" / "claude.exe", local / "AnthropicClaude" / "claude.exe"]
    for c in cands:
        if c.is_file():
            return str(c)
    return None


def _run(cmd: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}      # CREATE_NO_WINDOW
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def claude_command() -> list[str]:
    s = server_spec()
    return ["claude", "mcp", "add", "--scope", "user", NAME, "-e", f"PYTHONPATH={s['env']['PYTHONPATH']}",
            "--", s["command"], *s["args"]]


def claude_config() -> Path:
    """Claude Code keeps user-scope MCP servers in ~/.claude.json (CLAUDE_CONFIG_DIR moves it)."""
    d = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(d) / ".claude.json" if d else Path.home() / ".claude.json"


def _json_edit(path: Path, key: str, entry: dict | None) -> None:
    """Set (or with entry None remove) data[key][NAME] in a JSON settings file, keeping everything else."""
    data = _jsonc(path.read_text(encoding="utf-8")) if path.exists() else {}
    servers = data.setdefault(key, {})
    if entry is None:
        if NAME not in servers:
            return
        servers.pop(NAME, None)
    else:
        servers[NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".hertzon-tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _json_has(path: Path, key: str) -> bool:
    try:
        return NAME in (_jsonc(path.read_text(encoding="utf-8")).get(key) or {})
    except (OSError, ValueError):
        return False


def claude_status() -> str:
    if _json_has(claude_config(), "mcpServers"):
        return "registered"
    cli = _claude_cli()
    if cli:
        try:
            if _run([cli, "mcp", "get", NAME], timeout=30).returncode == 0:
                return "registered"
        except (OSError, subprocess.SubprocessError):
            pass
    return "not registered"


PLUGIN_REPO = "c8501024/hertzon"       # the marketplace on GitHub, for an installation without the plugin folder


def plugin_source() -> str:
    """Marketplace of the Claude plugin: the folder of this installation (it ships claude-plugin/ and
    .claude-plugin/marketplace.json, so the plugin always matches this version of Hertzon and loads in place,
    an update of Hertzon updates it), else the repository on GitHub."""
    if (ROOT / ".claude-plugin" / "marketplace.json").is_file() and (ROOT / "claude-plugin").is_dir():
        return str(ROOT)
    return PLUGIN_REPO


def plugin_command() -> list[str]:
    return ["claude", "plugin", "install", NAME, "--marketplace", plugin_source()]


def plugin_install(cli: str | None) -> str:
    """Install the Hertzon plugin for Claude Code (skills, commands, results pane); a message line."""
    hint = "Claude plugin: install it in a terminal with\n  " + " ".join(plugin_command())
    if not cli:
        return hint
    try:
        r = _run([cli, *plugin_command()[1:], "--json"], timeout=120)
        if r.returncode == 0:
            return "Claude plugin 'hertzon' installed (skills, commands and the results pane)."
    except (OSError, subprocess.SubprocessError):
        pass
    return "Claude plugin not installed automatically. " + hint


def plugin_remove(cli: str | None) -> None:
    if not cli:
        return
    for cmd in (["plugin", "uninstall", f"{NAME}@{NAME}"], ["plugin", "marketplace", "remove", NAME]):
        try:
            _run([cli, *cmd], timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass


def claude_install() -> str:
    """With the claude command: claude mcp add; without it (or if that fails) the entry is written to
    ~/.claude.json directly, which is where claude mcp add --scope user puts it. The Hertzon plugin is
    installed with it when the claude command is there."""
    msg = "Registered for Claude Code (all folders). Start a new Claude Code session to use it."
    cli = _claude_cli()
    if cli:
        try:
            _run([cli, "mcp", "remove", "--scope", "user", NAME], timeout=30)
            if _run([cli, *claude_command()[1:]]).returncode == 0:
                return msg + "\n" + plugin_install(cli)
        except (OSError, subprocess.SubprocessError):
            pass
    _json_edit(claude_config(), "mcpServers", {"type": "stdio", **server_spec()})
    return msg + f"\n(Written to {claude_config()}.)\n" + plugin_install(cli)


def claude_remove() -> str:
    cli = _claude_cli()
    if cli:
        try:
            _run([cli, "mcp", "remove", "--scope", "user", NAME], timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass
        plugin_remove(cli)
    _json_edit(claude_config(), "mcpServers", None)
    return "Removed from Claude Code (with the Hertzon plugin)."


# ---- Claude Desktop (claude_desktop_config.json) -------------------------------------------------
def desktop_config() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "Claude" / "claude_desktop_config.json"


def desktop_status() -> str:
    return "registered" if _json_has(desktop_config(), "mcpServers") else "not registered"


def desktop_install() -> str:
    _json_edit(desktop_config(), "mcpServers", server_spec())
    return "Registered for the Claude desktop app. Quit Claude completely (also in the tray) and start it again."


def desktop_remove() -> str:
    _json_edit(desktop_config(), "mcpServers", None)
    return "Removed from the Claude desktop app."


# ---- Codex (~/.codex/config.toml) ---------------------------------------------------------------
def codex_config() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "config.toml"


def _toml_str(s: str) -> str:
    return json.dumps(s)        # a JSON string is a valid TOML basic string


def codex_block() -> str:
    s = server_spec()
    env = ", ".join(f"{k} = {_toml_str(v)}" for k, v in s["env"].items())
    return (f"[mcp_servers.{NAME}]\n"
            f"command = {_toml_str(s['command'])}\n"
            f"args = [{', '.join(_toml_str(a) for a in s['args'])}]\n"
            f"env = {{ {env} }}\n"
            "startup_timeout_sec = 60\n"
            "tool_timeout_sec = 600\n")


def _strip_codex(text: str) -> str:
    """Remove the [mcp_servers.hertzon] table (and its sub-tables) from a config.toml text."""
    out, skip = [], False
    head = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")
    for line in text.splitlines(keepends=True):
        m = head.match(line)
        if m:
            key = m.group(1).replace('"', "").replace("'", "")
            skip = key == f"mcp_servers.{NAME}" or key.startswith(f"mcp_servers.{NAME}.")
        if not skip:
            out.append(line)
    return "".join(out)


def codex_status() -> str:
    p = codex_config()
    if not p.exists():
        return "not registered"
    return "registered" if _strip_codex(p.read_text(encoding="utf-8")) != p.read_text(encoding="utf-8") \
        else "not registered"


def codex_install() -> str:
    p = codex_config()
    p.parent.mkdir(parents=True, exist_ok=True)
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    text = _strip_codex(text).rstrip()
    text = (text + "\n\n" if text else "") + codex_block()
    p.write_text(text, encoding="utf-8")
    return f"Registered for Codex in {p}. Quit the Codex app completely and start it again (CLI / VS Code: start a new session) to use it."


def codex_remove() -> str:
    p = codex_config()
    if p.exists():
        p.write_text(_strip_codex(p.read_text(encoding="utf-8")).rstrip() + "\n", encoding="utf-8")
    return "Removed from Codex."


# ---- VS Code (user mcp.json) --------------------------------------------------------------------
def vscode_configs() -> list[Path]:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    found = [base / app / "User" / "mcp.json" for app in ("Code", "Code - Insiders") if (base / app).is_dir()]
    return found or [base / "Code" / "User" / "mcp.json"]


def _jsonc(text: str) -> dict:
    """mcp.json may hold comments and trailing commas (JSON with comments)."""
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    s = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', lambda m: m.group(1) or "", text, flags=re.S)
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    return json.loads(s)


def vscode_entry() -> dict:
    return {"type": "stdio", **server_spec()}


def vscode_status() -> str:
    for p in vscode_configs():
        try:
            if NAME in (_jsonc(p.read_text(encoding="utf-8")).get("servers") or {}):
                return "registered"
        except (OSError, ValueError):
            continue
    return "not registered"


def vscode_install() -> str:
    done = []
    for p in vscode_configs():
        data = _jsonc(p.read_text(encoding="utf-8")) if p.exists() else {}
        data.setdefault("servers", {})[NAME] = vscode_entry()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        done.append(str(p))
    return ("Registered for VS Code in " + ", ".join(done) +
            ". In the Copilot chat choose Agent mode; VS Code asks once to trust the server.")


def vscode_remove() -> str:
    for p in vscode_configs():
        if p.exists():
            data = _jsonc(p.read_text(encoding="utf-8"))
            servers = data.get("servers") or {}
            if NAME in servers:
                servers.pop(NAME)
                p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return "Removed from VS Code."


# ---- common --------------------------------------------------------------------------------------
def status(client: str) -> str:
    return {"claude": claude_status, "desktop": desktop_status, "codex": codex_status, "vscode": vscode_status}[client]()


def install(client: str) -> str:
    return {"claude": claude_install, "desktop": desktop_install, "codex": codex_install, "vscode": vscode_install}[client]()


def remove(client: str) -> str:
    return {"claude": claude_remove, "desktop": desktop_remove, "codex": codex_remove, "vscode": vscode_remove}[client]()


def installed(client: str) -> bool:
    """Whether the assistant seems to be installed for this user (its settings folder or command exists)."""
    if client == "claude":
        return claude_config().exists() or (Path.home() / ".claude").is_dir() or _claude_cli() is not None
    if client == "desktop":
        return desktop_config().parent.is_dir()
    if client == "codex":
        return codex_config().parent.is_dir()
    return any(p.parent.parent.is_dir() for p in vscode_configs())


AUTO = ("claude", "desktop", "codex")      # VS Code asks to trust each server, so it stays a manual choice
RESTART = {
    "claude": "Claude Code: end running sessions (/exit, or close the Claude Code window) and start a new one; "
              "/mcp then lists hertzon.",
    "desktop": "Claude desktop app: quit it completely (also the Claude icon in the taskbar tray: right click > "
               "Quit) and start it again.",
    "codex": "Codex: the Codex app reads its MCP servers only when it starts, so quit it completely (also the "
             "Codex icon in the taskbar tray: right click > Quit) and start it again; the Codex CLI and the VS Code "
             "extension need a new session. Then hertzon is listed in Codex's MCP settings. In the ChatGPT app, switch "
             "from ChatGPT to Codex (top left) and work in a local folder: ChatGPT chats do not load local MCP "
             "servers.",
    "vscode": "VS Code: reload the window (Ctrl+Shift+P > Developer: Reload Window), choose Agent mode in the chat "
              "and allow the hertzon server when asked.",
}


def auto_register(skip=()) -> list[str]:
    """Register with every installed assistant that does not list Hertzon yet (as Roblox Studio does when
    its MCP server is switched on). Clients in skip (removed by the user) are left alone. Returns the
    keys of the assistants that were registered now."""
    done = []
    for c in AUTO:
        if c in skip or not installed(c):
            continue
        try:
            if status(c) != "registered":
                install(c)
                done.append(c)
        except Exception:  # noqa: BLE001 - a broken settings file of one assistant must not stop the others
            continue
    return done


def _quote(a: str) -> str:
    return f'"{a}"' if (" " in a or "\\" in a) else a


def snippets() -> str:
    """Manual configuration for every client (and others that read the common mcpServers format)."""
    s = server_spec()
    generic = json.dumps({"mcpServers": {NAME: s}}, indent=2)
    vs = json.dumps({"servers": {NAME: vscode_entry()}}, indent=2)
    return ("Claude Code (terminal):\n  " + " ".join(_quote(a) for a in claude_command()) + "\n\n"
            f"Codex ({codex_config()}):\n" + codex_block() + "\n"
            "VS Code (mcp.json):\n" + vs + "\n\n"
            "Claude Desktop, Cursor and other clients (mcpServers):\n" + generic + "\n")
