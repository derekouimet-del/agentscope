"""Static, offline explanation of shell / Python commands.

No model and no network: the command text is parsed and matched against a built-in
knowledge base. This describes what the command *text* would do in general - it is not a
record of what actually happened (check the event's output/status for that).
"""
from __future__ import annotations

import re
import shlex

# exe -> (description, {flag: description})
KB: dict[str, tuple[str, dict[str, str]]] = {
    "ls": ("Lists files and folders.", {"-l": "long format (permissions, owner, size, date)", "-a": "include hidden files", "-h": "human-readable sizes", "-R": "recurse into subfolders", "-t": "sort by modified time", "-1": "one entry per line"}),
    "cd": ("Changes the current working directory (affects later commands in the same shell).", {}),
    "pwd": ("Prints the current working directory.", {}),
    "cat": ("Prints the contents of files to the output (or joins several files).", {"-n": "number the lines"}),
    "echo": ("Prints text. With > or >> it is commonly used to write text into a file.", {"-n": "no trailing newline", "-e": "interpret backslash escapes"}),
    "printf": ("Prints formatted text.", {}),
    "mkdir": ("Creates folders.", {"-p": "create parent folders as needed, no error if it exists"}),
    "rmdir": ("Removes empty folders.", {}),
    "rm": ("DELETES files or folders. Deleted files are not moved to a trash.", {"-r": "recursive: delete folders and everything inside", "-R": "recursive", "-f": "force: no prompts, ignore missing files", "-i": "ask before each deletion", "-v": "list what is deleted"}),
    "cp": ("Copies files or folders.", {"-r": "recursive (folders)", "-R": "recursive", "-a": "archive: preserve permissions, times and links", "-v": "verbose", "-f": "overwrite without asking"}),
    "mv": ("Moves or renames files and folders.", {"-f": "overwrite without asking", "-v": "verbose", "-n": "never overwrite"}),
    "touch": ("Creates an empty file, or updates a file's modified time.", {}),
    "ln": ("Creates a link (shortcut) to a file.", {"-s": "symbolic link"}),
    "chmod": ("Changes file permissions (who may read / write / execute).", {"-R": "recursive", "+x": "make executable"}),
    "chown": ("Changes the owner of files.", {"-R": "recursive"}),
    "grep": ("Searches text for lines matching a pattern.", {"-r": "search folders recursively", "-R": "recursive", "-i": "ignore case", "-n": "show line numbers", "-v": "show lines that do NOT match", "-l": "list only file names", "-E": "extended regular expressions", "-c": "count matches", "-o": "print only the matching part", "-w": "match whole words"}),
    "egrep": ("grep with extended regular expressions.", {}),
    "rg": ("ripgrep: fast recursive text search.", {"-i": "ignore case", "-n": "line numbers", "-l": "file names only"}),
    "find": ("Searches a folder tree for files by name, type, size or age. Can also run commands on matches (-exec) or delete them (-delete).", {"-name": "match file name pattern", "-type": "f = files, d = directories", "-delete": "DELETES every match", "-exec": "run a command on each match", "-mtime": "filter by age in days", "-maxdepth": "limit how deep to search", "-size": "filter by size"}),
    "sed": ("Edits text as a stream (search/replace, delete lines…).", {"-i": "edit the file in place (modifies it)", "-e": "script to apply", "-n": "print only selected lines", "-E": "extended regex"}),
    "awk": ("Processes text column by column; a small scripting language for reports and extraction.", {"-F": "field separator"}),
    "cut": ("Extracts columns/fields from each line.", {"-d": "delimiter", "-f": "fields to keep", "-c": "characters to keep"}),
    "tr": ("Translates or deletes characters.", {"-d": "delete characters"}),
    "head": ("Shows the first lines of a file or input.", {"-n": "number of lines"}),
    "tail": ("Shows the last lines of a file or input.", {"-n": "number of lines", "-f": "keep following new lines as they are added"}),
    "sort": ("Sorts lines.", {"-r": "reverse", "-n": "numeric", "-u": "unique", "-k": "sort by column"}),
    "uniq": ("Collapses repeated adjacent lines.", {"-c": "count occurrences"}),
    "wc": ("Counts lines, words and bytes.", {"-l": "lines", "-w": "words", "-c": "bytes"}),
    "xargs": ("Takes input lines and passes them as arguments to another command.", {"-n": "arguments per run", "-I": "placeholder for the argument"}),
    "tee": ("Copies input to both the screen and a file.", {"-a": "append instead of overwrite"}),
    "diff": ("Shows differences between two files.", {"-u": "unified format", "-r": "recurse into folders"}),
    "less": ("Pages through text.", {}), "more": ("Pages through text.", {}),
    "jq": ("Reads, filters and reshapes JSON.", {"-r": "raw output (no quotes)"}),
    "curl": ("Makes a network request (usually HTTP/HTTPS) and prints the response. Can download files or send data.", {"-s": "silent (no progress)", "-S": "show errors even when silent", "-L": "follow redirects", "-o": "save output to a file", "-O": "save using the remote file name", "-X": "HTTP method (GET, POST…)", "-H": "add a request header", "-d": "send data in the request body", "--data": "send data in the request body", "-I": "fetch headers only", "-k": "skip TLS certificate verification (insecure)", "--insecure": "skip TLS certificate verification (insecure)", "-f": "fail on HTTP errors", "-u": "basic-auth user:password", "-v": "verbose", "--max-time": "give up after N seconds", "-m": "give up after N seconds"}),
    "wget": ("Downloads files over HTTP/HTTPS/FTP.", {"-q": "quiet", "-O": "save to this file name (- = print)", "-c": "resume a partial download", "-r": "recursive download", "--no-check-certificate": "skip TLS certificate verification (insecure)", "-T": "timeout seconds"}),
    "tar": ("Creates or extracts archive files.", {"-x": "extract", "-c": "create", "-z": "gzip", "-j": "bzip2", "-v": "verbose", "-f": "archive file name", "-t": "list contents", "-C": "change to this folder first"}),
    "unzip": ("Extracts .zip archives.", {"-o": "overwrite without asking", "-d": "extract into this folder", "-l": "list contents"}),
    "zip": ("Creates .zip archives.", {"-r": "recurse into folders"}),
    "gzip": ("Compresses files.", {"-d": "decompress", "-k": "keep original"}), "gunzip": ("Decompresses .gz files.", {}),
    "pip": ("Python package manager: installs/removes Python libraries (downloads code from a package index).", {"install": "install packages", "uninstall": "remove packages", "list": "list installed packages", "show": "show package details", "freeze": "print installed versions", "-r": "read packages from a requirements file", "-U": "upgrade", "--upgrade": "upgrade", "-q": "quiet", "--break-system-packages": "override the OS protection on system Python", "--user": "install for this user only", "-e": "editable install from a folder"}),
    "pip3": ("Python package manager (Python 3).", {"install": "install packages", "uninstall": "remove packages", "list": "list installed packages"}),
    "python": ("Runs the Python interpreter (a script, a module with -m, or inline code with -c).", {"-c": "run the following code string", "-m": "run a module as a script", "-u": "unbuffered output", "-V": "print the version", "--version": "print the version"}),
    "python3": ("Runs the Python 3 interpreter (a script, a module with -m, or inline code with -c).", {"-c": "run the following code string", "-m": "run a module as a script", "-u": "unbuffered output", "--version": "print the version"}),
    "node": ("Runs JavaScript with Node.js.", {"-e": "run the following code string", "-v": "print the version"}),
    "npm": ("Node.js package manager: installs libraries and runs project scripts.", {"install": "install packages", "i": "install packages", "run": "run a script from package.json", "start": "run the start script", "test": "run tests", "-g": "install globally", "ci": "clean install from lockfile"}),
    "npx": ("Runs a Node.js package without installing it permanently (downloads it first).", {"-y": "assume yes"}),
    "yarn": ("Node.js package manager.", {}), "pnpm": ("Node.js package manager.", {}),
    "git": ("Version control: tracks and syncs code changes.", {"clone": "download a repository", "status": "show changed files", "add": "stage changes", "commit": "record staged changes", "push": "upload commits to a remote", "pull": "download and merge remote commits", "checkout": "switch branches / restore files", "diff": "show changes", "log": "show history", "reset": "move HEAD / discard changes (--hard discards work)", "--force": "overwrite remote history (dangerous)", "-f": "force", "--hard": "discard uncommitted changes", "stash": "shelve changes temporarily", "branch": "list / create branches", "init": "create a repository"}),
    "docker": ("Controls Docker containers and images.", {"run": "start a new container", "exec": "run a command inside a running container", "ps": "list containers", "logs": "show container output", "build": "build an image", "compose": "multi-container apps", "stop": "stop containers", "rm": "remove containers", "pull": "download an image", "--privileged": "gives the container nearly full host access (dangerous)", "-v": "mount a host folder/volume into the container", "-it": "interactive terminal"}),
    "apt": ("Debian/Ubuntu package manager: installs system software (needs root).", {"install": "install packages", "update": "refresh package lists", "remove": "uninstall", "upgrade": "upgrade installed packages", "-y": "assume yes to prompts"}),
    "apt-get": ("Debian/Ubuntu package manager: installs system software (needs root).", {"install": "install packages", "update": "refresh package lists", "remove": "uninstall", "upgrade": "upgrade installed packages", "-y": "assume yes to prompts"}),
    "sudo": ("Runs the following command with administrator (root) privileges.", {"-u": "run as this user", "-E": "preserve environment"}),
    "su": ("Switches to another user.", {}),
    "ps": ("Lists running processes.", {"aux": "all processes, detailed", "-ef": "all processes, full format", "-e": "all processes", "-f": "full format"}),
    "top": ("Live view of CPU/memory use by process.", {}), "htop": ("Interactive process viewer.", {}),
    "kill": ("Sends a signal to a process (by default asks it to stop).", {"-9": "force kill (cannot be caught)", "-15": "polite terminate", "-HUP": "reload/hang-up"}),
    "pkill": ("Kills processes by name.", {"-f": "match the full command line", "-9": "force kill"}), "killall": ("Kills all processes with a name.", {}),
    "df": ("Shows disk space usage per filesystem.", {"-h": "human-readable"}), "du": ("Shows disk space used by files/folders.", {"-h": "human-readable", "-s": "summary only"}),
    "free": ("Shows memory usage.", {"-h": "human-readable", "-m": "in megabytes"}),
    "which": ("Shows where an executable is located.", {}), "whoami": ("Prints the current user name.", {}), "uname": ("Prints system information.", {"-a": "everything"}),
    "env": ("Prints environment variables, or runs a command with modified ones. (Output can include secrets.)", {}),
    "printenv": ("Prints environment variables. (Output can include secrets.)", {}),
    "export": ("Sets an environment variable for later commands in this shell.", {}),
    "source": ("Runs a script in the current shell (so it can change variables and directory).", {}), ".": ("Runs a script in the current shell.", {}),
    "sleep": ("Waits for a number of seconds.", {}), "date": ("Prints the date and time.", {}), "true": ("Does nothing, succeeds.", {}), "false": ("Does nothing, fails.", {}),
    "test": ("Checks a condition (file exists, strings equal…) and succeeds or fails.", {"-f": "is a regular file", "-d": "is a directory", "-e": "exists"}),
    "[": ("Checks a condition (like `test`).", {}),
    "bash": ("Runs the Bash shell (a script, or a command string with -c).", {"-c": "run the following command string", "-s": "read commands from input"}),
    "sh": ("Runs the system shell (a script, or a command string with -c).", {"-c": "run the following command string", "-s": "read commands from input"}),
    "eval": ("Executes a string as shell code. Hard to audit - what runs depends on the string's content.", {}),
    "exec": ("Replaces the current shell with a command.", {}), "nohup": ("Runs a command that keeps running after the terminal closes.", {}), "time": ("Measures how long a command takes.", {}),
    "timeout": ("Stops a command if it runs longer than a time limit.", {}),
    "ssh": ("Opens a secure connection to another machine and can run commands there.", {"-i": "identity (private key) file", "-p": "port", "-L": "forward a local port", "-o": "set an option"}),
    "scp": ("Copies files to/from another machine over SSH.", {"-r": "recursive", "-i": "identity (private key) file", "-P": "port"}), "rsync": ("Synchronises folders (locally or over SSH).", {"-a": "archive mode", "-v": "verbose", "-z": "compress", "--delete": "DELETES files at the destination that are missing at the source"}),
    "ping": ("Tests whether a host answers network packets.", {"-c": "number of packets"}),
    "nc": ("Netcat: opens raw network connections or listeners.", {"-l": "listen for connections", "-z": "scan without sending data", "-v": "verbose"}), "ncat": ("Netcat: opens raw network connections or listeners.", {}),
    "nmap": ("Scans hosts for open network ports.", {"-p": "ports", "-sV": "detect service versions"}),
    "dig": ("Looks up DNS records.", {}), "nslookup": ("Looks up DNS records.", {}), "host": ("Looks up DNS records.", {}),
    "netstat": ("Shows network connections and listening ports.", {"-t": "TCP", "-l": "listening", "-n": "numeric", "-p": "show processes"}), "ss": ("Shows network sockets (modern netstat).", {"-t": "TCP", "-l": "listening", "-n": "numeric", "-p": "show processes"}),
    "systemctl": ("Controls system services.", {"start": "start a service", "stop": "stop a service", "restart": "restart a service", "status": "show status", "enable": "start at boot"}),
    "supervisorctl": ("Controls processes managed by supervisord.", {}), "service": ("Controls system services.", {}),
    "sqlite3": ("Opens/queries a SQLite database file.", {}), "psql": ("PostgreSQL command-line client.", {}), "mysql": ("MySQL command-line client.", {"-p": "password"}), "redis-cli": ("Redis command-line client.", {}),
    "make": ("Runs build tasks defined in a Makefile.", {"-j": "parallel jobs"}), "cargo": ("Rust build tool/package manager.", {}), "go": ("Go toolchain.", {}), "java": ("Runs Java programs.", {}),
    "dd": ("Copies raw data block by block. Writing to a device (of=/dev/…) can destroy disks.", {"if=": "input source", "of=": "output target"}),
    "mkfs": ("Formats a disk/partition - DESTROYS its data.", {}), "mount": ("Attaches a filesystem.", {}), "umount": ("Detaches a filesystem.", {}),
    "base64": ("Encodes/decodes Base64 text.", {"-d": "decode"}), "openssl": ("Cryptography toolkit (certificates, hashing, encryption).", {}),
    "crontab": ("Edits scheduled recurring jobs.", {"-e": "edit", "-l": "list", "-r": "REMOVE all jobs"}),
    "useradd": ("Creates a user account.", {}), "passwd": ("Changes a password.", {}),
    "chromium": ("Runs the Chromium browser.", {}), "playwright": ("Browser automation tool.", {}),
    "pytest": ("Runs Python tests.", {"-q": "quiet", "-x": "stop at first failure", "-k": "select tests by name"}),
    "uvicorn": ("Runs a Python ASGI web server (e.g. FastAPI).", {"--reload": "restart on code change", "--port": "listening port", "--host": "listening address"}),
}

OPS = {"|": "pipes the output of the previous step into the next one", "&&": "runs the next step only if the previous one succeeded", "||": "runs the next step only if the previous one failed",
       ";": "then runs the next step regardless of the previous result", "&": "runs the previous step in the background"}
REDIRECTS = {">": "writes (overwrites) the output into", ">>": "appends the output to", "<": "reads input from", "2>": "writes error messages to", "2>>": "appends error messages to", "&>": "writes all output to", "2>&1": "merges error output into normal output"}

_SPLIT = re.compile(r"(\|\||&&|;|\||(?<![>&0-9])&(?![>&]))")


def _tokens(seg: str) -> list[str]:
    try:
        return shlex.split(seg, posix=True)
    except ValueError:
        return seg.split()


def _flag_desc(exe: str, tok: str) -> tuple[str, str | None]:
    flags = KB.get(exe, ("", {}))[1]
    if tok in flags:
        return tok, flags[tok]
    if tok.startswith("--") and "=" in tok and tok.split("=")[0] in flags:
        return tok, flags[tok.split("=")[0]]
    if tok.startswith("-") and not tok.startswith("--") and len(tok) > 2 and tok not in flags:
        parts, ok = [], True
        for ch in tok[1:]:
            d = flags.get("-" + ch)
            if d is None:
                ok = False
                break
            parts.append(f"-{ch}: {d}")
        if ok:
            return tok, "; ".join(parts)
    return tok, None


def _explain_segment(seg: str) -> dict:
    text = seg.strip()
    toks = _tokens(text)
    out: dict = {"text": text, "exe": None, "description": "", "known": False, "flags": [], "args": [], "redirects": [], "env": []}
    # redirections (also when glued to the command)
    clean: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        m = re.match(r"^(\d?>>?|&>|<|2>&1)(.*)$", t)
        if m and (m.group(1) in REDIRECTS):
            tgt = m.group(2) or (toks[i + 1] if i + 1 < len(toks) and m.group(1) != "2>&1" else "")
            out["redirects"].append(f"{REDIRECTS[m.group(1)]} {tgt}".strip())
            i += 1 if m.group(2) or m.group(1) == "2>&1" else 2
            continue
        if t.startswith("<<"):
            out["redirects"].append("feeds the following inline text (here-document) as input")
            i += 1
            continue
        clean.append(t)
        i += 1
    toks = clean
    while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]) and not toks[0].startswith(("/", ".")):
        out["env"].append(toks.pop(0).split("=")[0])
    wrappers = []
    while toks and toks[0] in ("sudo", "nohup", "time", "exec", "timeout", "env", "nice"):
        w = toks.pop(0)
        wrappers.append(w)
        if w == "timeout" and toks:
            toks.pop(0)
        while w in ("sudo", "env") and toks and toks[0].startswith("-"):
            toks.pop(0)
    if not toks:
        out["description"] = "Sets variables / no command."
        return out
    exe = toks[0].rsplit("/", 1)[-1]
    out["exe"] = exe
    args = toks[1:]
    if exe in KB:
        out["known"] = True
        out["description"] = KB[exe][0]
    else:
        out["description"] = f"Runs `{toks[0]}` - not in the built-in reference."
    if wrappers:
        out["description"] = " ".join(KB[w][0].rstrip(".") + "." for w in wrappers if w in KB) + " " + out["description"]
    for a in args:
        if a.startswith("-") or (exe in KB and a in KB[exe][1]):
            tok, d = _flag_desc(exe, a)
            out["flags"].append({"flag": tok, "description": d})
        else:
            out["args"].append(a)
    if exe == "cd" and args:
        out["description"] = f"Changes the working directory to {args[0]}."
    if exe in ("python", "python3") and "-c" in args:
        out["description"] += " Here it runs the inline code string given after -c."
    for r in out["redirects"]:
        out["description"] += f" It {r}."
    return out


def _warnings(cmd: str) -> list[dict]:
    w: list[dict] = []
    add = lambda level, msg: w.append({"level": level, "message": msg})
    c = cmd
    if re.search(r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*\s+)*-?[a-zA-Z]*f|\brm\s+-[a-zA-Z]*[rR]", c):
        add("high", "Recursive/forced delete: removes files and folders permanently.")
    if re.search(r"\brm\b[^|;&]*\s(/|~|\$HOME|\*)(\s|$)", c):
        add("critical", "Deletes from the root, home folder or with a bare wildcard.")
    if re.search(r"(curl|wget)[^|;]*\|\s*(sudo\s+)?(ba|z)?sh\b", c):
        add("critical", "Downloads a script and runs it immediately without review.")
    if re.search(r"base64\s+(-d|--decode)[^|;]*\|\s*(ba|z)?sh\b", c) or re.search(r"\beval\b", c):
        add("high", "Executes dynamically-built or encoded code (hard to audit).")
    if re.search(r"\bsudo\b", c):
        add("medium", "Runs with administrator privileges.")
    if re.search(r"chmod\s+(-R\s+)?[0-7]*777|chmod\s+-R", c):
        add("medium", "Broad permission change (777 / recursive).")
    if re.search(r"\b(dd|mkfs[.\w]*)\b.*(/dev/|of=)", c):
        add("critical", "Writes directly to a device / formats a disk.")
    if re.search(r"git\s+(push\s+.*(--force|-f)\b|reset\s+--hard|clean\s+-[a-z]*f)", c):
        add("high", "Can overwrite remote history or discard uncommitted work.")
    if re.search(r"docker\s+run\b.*--privileged|-v\s+/var/run/docker\.sock", c):
        add("high", "Gives a container broad access to the host.")
    if re.search(r"\b(pip3?|npm|npx|apt(-get)?|yarn|pnpm|cargo|go)\s+(install|i|get|add)\b|npx\s", c):
        add("low", "Installs third-party software (downloads and may run code).")
    if re.search(r"(curl|wget)\b", c):
        add("low", "Makes network requests to external servers.")
    if re.search(r"-k\b|--insecure|--no-check-certificate", c) and re.search(r"curl|wget", c):
        add("medium", "Disables TLS certificate verification.")
    if re.search(r"/etc/(shadow|passwd|sudoers)|\.ssh/|id_rsa|\.aws/|\.env\b|secrets\.env|docker\.sock", c):
        add("high", "Touches credentials or other sensitive files.")
    if re.search(r"\b(env|printenv)\b\s*($|[|;&])", c):
        add("medium", "Prints environment variables, which may contain secrets.")
    if "[REDACTED]" in c:
        add("info", "Contains a value AgentScope redacted - fill in your own secret before re-running.")
    if re.search(r":\(\)\s*\{", c):
        add("critical", "Looks like a fork bomb.")
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    return sorted(w, key=lambda x: order[x["level"]])


def explain_shell(cmd: str) -> dict:
    cmd = (cmd or "").strip()
    # split off simple line continuations and multiple lines
    lines = [l for l in re.sub(r"\\\n", " ", cmd).splitlines() if l.strip() and not l.strip().startswith("#")]
    steps: list[dict] = []
    ops: list[str] = []
    for line in lines:
        parts = _SPLIT.split(line)
        for p in parts:
            if p in OPS:
                ops.append(p)
            elif p.strip():
                if steps and len(ops) < len(steps):
                    ops.append("\n")
                steps.append(_explain_segment(p))
    segs = []
    for i, s in enumerate(steps):
        s["then"] = OPS.get(ops[i], "then") if i < len(ops) and ops[i] != "\n" else ("then" if i < len(ops) else None)
        s["operator"] = ops[i] if i < len(ops) and ops[i] != "\n" else None
        segs.append(s)
    known = sum(1 for s in segs if s["known"])
    if not segs:
        summary = "Empty command."
    elif len(segs) == 1:
        summary = segs[0]["description"]
    else:
        names = [s["exe"] or "(assignment)" for s in segs]
        summary = f"A sequence of {len(segs)} steps ({', '.join(names)}). " + " ".join(
            f"{i + 1}) {s['description'].split('. ')[0].rstrip('.')}." for i, s in enumerate(segs[:6])) + (" …" if len(segs) > 6 else "")
    return {"kind": "shell", "summary": summary, "steps": segs, "warnings": _warnings(cmd),
            "coverage": f"{known} of {len(segs)} program(s) recognised" if segs else "",
            "disclaimer": "General explanation of the command text from a built-in reference. It does not show what actually happened - check the status and output."}


_PY_RISKS = [
    (r"\bos\.(remove|unlink|rmdir)|shutil\.rmtree|\.unlink\(", "high", "Deletes files or folders."),
    (r"\b(subprocess|os\.system|os\.popen)\b", "medium", "Runs other programs / shell commands."),
    (r"\b(eval|exec)\(", "high", "Executes dynamically-built code."),
    (r"\b(requests|httpx|urllib|aiohttp|http\.client|socket)\b", "low", "Makes network connections."),
    (r"open\([^)]*['\"][wa]b?\+?['\"]|\.write_text\(|\.write_bytes\(|json\.dump\(|to_csv\(", "low", "Writes to files."),
    (r"\.ssh|/etc/(shadow|passwd)|id_rsa|\.env\b|os\.environ", "medium", "Reads credentials or environment variables."),
    (r"\bpickle\.loads?\b", "medium", "Unpickling can run arbitrary code."),
]


def explain_python(code: str) -> dict:
    code = code or ""
    imports = sorted(set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][\w]*)", code, re.M)))
    funcs = re.findall(r"^\s*(?:async\s+)?def\s+(\w+)", code, re.M)
    notes = []
    if imports:
        notes.append(f"Imports: {', '.join(imports)}.")
    if funcs:
        notes.append(f"Defines function(s): {', '.join(funcs[:8])}.")
    if re.search(r"\bprint\(", code):
        notes.append("Prints results to the output.")
    if re.search(r"open\([^)]*\)", code):
        notes.append("Opens files.")
    warnings = [{"level": lvl, "message": msg} for pat, lvl, msg in _PY_RISKS if re.search(pat, code)]
    if "[REDACTED]" in code:
        warnings.append({"level": "info", "message": "Contains a value AgentScope redacted - fill in your own secret before re-running."})
    summary = " ".join(notes) or "Runs Python code."
    return {"kind": "python", "summary": summary, "steps": [], "warnings": warnings, "coverage": "",
            "disclaimer": "Heuristic reading of the code text (imports, calls). It does not execute or fully analyse the code and does not show what actually happened."}


def explain(command: str, runtime: str = "terminal") -> dict:
    return explain_python(command) if runtime in ("python", "nodejs") else explain_shell(command)
