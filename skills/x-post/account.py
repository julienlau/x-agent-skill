#!/usr/bin/env python3
"""
account.py - which X account, which voice, and where their files live.

Every skill in the pack calls this first, so "post this as pd in my founder
voice" resolves the same way everywhere.

The workdir (home) holds one folder per account and a shared voice library:

    <home>/
      voices/<voice>.md         voice files, shared by every account
      x-<name>/account.md       short name, handle, default voice, timezone, poster
      x-<name>/twitter.env      optional, chmod 600, the account's X login
      x-<name>/cookie.header    written by `cookie`, chmod 600
      x-<name>/log.md plan.md swipe.md

Home:     --home > $X_AGENT_HOME > `home = ...` in the config file.
          No default. `init --home PATH` writes the config file once.
Account:  --account > $X_AGENT_ACCOUNT > `account = ...` in the config file
          (set by `use`) > the only account. An account is named by its full
          name, its folder name (x-<name>) or its short name.
Voice:    --voice > $X_AGENT_VOICE > `voice:` in account.md > the only voice.

twitter.env holds one of: TWITTER_COOKIE (the raw Cookie header from the
browser), TWITTER_AUTH_TOKEN + TWITTER_CT0, or TWITTER_BROWSER +
TWITTER_CHROME_PROFILE. `exec` drops every inherited TWITTER_* variable
first, so one account's login never leaks into another's command.

Exit 2 means a human has to choose or fix something: the message says what.
Exit 3 (verify) means the logged-in X account is not this account's handle.

Config file: ${XDG_CONFIG_HOME:-~/.config}/x-agent/config

Usage
  python3 account.py init pepitedata --short pd --home ~/agents --voice founder --handle @pepitedata
  python3 account.py list
  python3 account.py use pd
  python3 account.py paths [--account pd] [--voice founder] [--json]
  python3 account.py path swipe
  python3 account.py where
  python3 account.py verify [--account pd]              # twitter whoami == handle:
  python3 account.py exec [--account pd] -- twitter post "..."
  python3 account.py cookie [--account pd]              # browser publishing
"""

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "templates")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
FILES = ("log", "plan", "swipe")
PATH_KEYS = ("home", "dir", "account", "voice", "log", "plan", "swipe", "env")
POSTERS = ("auto", "twitter-cli", "browser", "none")


class Stop(Exception):
    """A choice only the user can make, or a setup step that is missing."""


# ---------------------------------------------------------------- config

def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "x-agent", "config")


def read_config():
    cfg = {}
    try:
        with open(config_path(), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return cfg


def write_config(cfg):
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# x-agent skill pack, written by account.py\n")
        for k, v in cfg.items():
            fh.write(f"{k} = {v}\n")


def norm_path(p):
    return os.path.abspath(os.path.expanduser(p))


# ---------------------------------------------------------------- home

def resolve_home(flag):
    if flag:
        return norm_path(flag), "--home"
    if os.environ.get("X_AGENT_HOME"):
        return norm_path(os.environ["X_AGENT_HOME"]), "$X_AGENT_HOME"
    cfg = read_config()
    if cfg.get("home"):
        return norm_path(cfg["home"]), "config"
    raise Stop("no workdir configured. Ask the user where the X account files should live "
               "(for example ~/agents), then run: account.py init <name> --home <path>")


# ---------------------------------------------------------------- accounts

def frontmatter(path):
    meta = {}
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return meta
    if not lines or lines[0].strip() != "---":
        return meta
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("\"'")
    return meta


def load_accounts(home):
    accounts = []
    if not os.path.isdir(home):
        return accounts
    for entry in sorted(os.listdir(home)):
        d = os.path.join(home, entry)
        if entry.startswith("x-") and len(entry) > 2 and os.path.isdir(d):
            name = entry[2:]
            meta = frontmatter(os.path.join(d, "account.md"))
            accounts.append({"name": name, "dir": d, "short": meta.get("short") or name, "meta": meta})
    return accounts


def id_map(accounts):
    """Every way to name each account -> account names it points at."""
    ids = {}
    for a in accounts:
        for i in {a["name"], "x-" + a["name"], a["short"]}:
            ids.setdefault(i, set()).add(a["name"])
    return ids


def check_conflicts(accounts):
    clashes = sorted(f"'{i}' -> {', '.join(sorted(n))}" for i, n in id_map(accounts).items() if len(n) > 1)
    if clashes:
        raise Stop("short names clash, fix `short:` in account.md: " + "; ".join(clashes))


def describe(accounts):
    if not accounts:
        return "none yet (run: account.py init <name> --short <s>)"
    return ", ".join(f"{a['short']} ({a['name']}{', ' + a['meta']['handle'] if a['meta'].get('handle') else ''})"
                     for a in accounts)


def find(accounts, ident):
    ident = (ident or "").strip().lstrip("@")
    names = id_map(accounts).get(ident)
    if not names:
        raise Stop(f"no account '{ident}'. Known: {describe(accounts)}")
    return next(a for a in accounts if a["name"] in names)


def resolve_account(home, flag):
    accounts = load_accounts(home)
    check_conflicts(accounts)
    for ident, source in ((flag, "--account"),
                          (os.environ.get("X_AGENT_ACCOUNT"), "$X_AGENT_ACCOUNT"),
                          (read_config().get("account"), "config (account.py use)")):
        if ident:
            return find(accounts, ident), source
    if len(accounts) == 1:
        return accounts[0], "only account"
    raise Stop(f"which account? Ask the user. Accounts: {describe(accounts)}")


# ---------------------------------------------------------------- voices

def voices_dir(home):
    return os.path.join(home, "voices")


def list_voices(home):
    d = voices_dir(home)
    if not os.path.isdir(d):
        return []
    return sorted(f[:-3] for f in os.listdir(d) if f.endswith(".md"))


def voice_name(v):
    v = v.strip()
    return v[:-3] if v.endswith(".md") else v


def resolve_voice(home, account, flag):
    for v, source in ((flag, "--voice"),
                      (os.environ.get("X_AGENT_VOICE"), "$X_AGENT_VOICE"),
                      (account["meta"].get("voice"), "account.md")):
        if v:
            return voice_name(v), source
    voices = list_voices(home)
    if len(voices) == 1:
        return voices[0], "only voice"
    return None, "ask the user which voice. Voices: " + (", ".join(voices) or "none yet")


# ---------------------------------------------------------------- resolve

def resolve(args):
    home, home_src = resolve_home(args.home)
    account, acc_src = resolve_account(home, args.account)
    voice, voice_src = resolve_voice(home, account, args.voice)
    d = account["dir"]
    poster, poster_src = effective_poster(account)
    paths = {
        "home": home,
        "dir": d,
        "account": os.path.join(d, "account.md"),
        "voice": os.path.join(voices_dir(home), voice + ".md") if voice else None,
        "env": os.path.join(d, "twitter.env"),
    }
    for f in FILES:
        paths[f] = os.path.join(d, f + ".md")
    return {
        "home": home, "home_source": home_src,
        "account": account["name"], "short": account["short"], "account_source": acc_src,
        "handle": account["meta"].get("handle", ""),
        "timezone": account["meta"].get("timezone", ""),
        "poster": poster, "poster_source": poster_src,
        "voice": voice, "voice_source": voice_src,
        "paths": paths,
        "exists": {k: bool(p) and os.path.exists(p) for k, p in paths.items()},
    }


# ---------------------------------------------------------------- commands

def cmd_where(args):
    home, src = resolve_home(args.home)
    print(f"home    {home}  ({src})")
    print(f"config  {config_path()}")


def cmd_list(args):
    home, _ = resolve_home(args.home)
    accounts = load_accounts(home)
    check_conflicts(accounts)
    try:
        active = resolve_account(home, args.account)[0]["name"]
    except Stop:
        active = None
    print(f"accounts in {home}")
    if not accounts:
        print("  none yet (run: account.py init <name> --short <s>)")
    w = max([len(a["short"]) for a in accounts] + [5])
    n = max([len(a["name"]) for a in accounts] + [4])
    for a in accounts:
        mark = "*" if a["name"] == active else " "
        m = a["meta"]
        print(f"{mark} {a['short']:<{w}}  {a['name']:<{n}}  {m.get('handle', '-') or '-':<16}  voice={m.get('voice', '-') or '-'}")
    print(f"voices in {voices_dir(home)}")
    for v in list_voices(home) or ["(none)"]:
        print(f"  {v}")


def cmd_use(args):
    cfg = read_config()
    if args.clear:
        cfg.pop("account", None)
        write_config(cfg)
        print("default account cleared")
        return
    if not args.id:
        raise Stop("use <account> or use --clear")
    home, _ = resolve_home(args.home)
    accounts = load_accounts(home)
    check_conflicts(accounts)
    a = find(accounts, args.id)
    cfg["account"] = a["name"]
    write_config(cfg)
    print(f"default account: {a['short']} ({a['name']})")
    if os.environ.get("X_AGENT_ACCOUNT"):
        print(f"note: $X_AGENT_ACCOUNT={os.environ['X_AGENT_ACCOUNT']} still overrides this", file=sys.stderr)


def cmd_paths(args):
    r = resolve(args)
    if args.json:
        print(json.dumps(r, indent=2))
        return
    ok = lambda k: "ok" if r["exists"][k] else "missing"
    print(f"home        {r['home']}  ({r['home_source']})")
    print(f"account     {r['short']} = {r['account']}  handle={r['handle'] or '-'}  ({r['account_source']})")
    print(f"timezone    {r['timezone'] or '-'}")
    print(f"poster      {r['poster']}  ({r['poster_source']})")
    print(f"account.md  {r['paths']['account']}  [{ok('account')}]")
    if r["voice"]:
        print(f"voice       {r['voice']}  {r['paths']['voice']}  [{ok('voice')}]  ({r['voice_source']})")
    else:
        print(f"voice       -  ({r['voice_source']})")
    for f in FILES:
        print(f"{f:<11} {r['paths'][f]}  [{ok(f)}]")
    print(f"twitter.env {r['paths']['env']}  [{ok('env')}]")


def cmd_path(args):
    r = resolve(args)
    p = r["paths"][args.key]
    if not p:
        raise Stop(r["voice_source"])
    print(p)


def render_template(name, values):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as fh:
        text = fh.read()
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def cmd_init(args):
    name = args.name.strip()
    if name.startswith("x-"):
        name = name[2:]
    if not NAME_RE.match(name):
        raise Stop(f"account name '{args.name}': use lowercase letters, digits, '-' or '_'")
    short = (args.short or name).strip()
    if not NAME_RE.match(short):
        raise Stop(f"short name '{short}': use lowercase letters, digits, '-' or '_'")

    cfg = read_config()
    if args.home:
        home = norm_path(args.home)
        if cfg.get("home") and norm_path(cfg["home"]) != home and not args.force:
            raise Stop(f"config already points home at {cfg['home']}. Re-run with --force to move it to {home}")
        if cfg.get("home") != home:
            cfg["home"] = home
            write_config(cfg)
            print(f"config      home = {home}  ({config_path()})")
    else:
        home, _ = resolve_home(None)

    accounts = load_accounts(home)
    check_conflicts(accounts)
    others = [a for a in accounts if a["name"] != name]
    taken = id_map(others)
    for i in {name, short}:
        if i in taken:
            raise Stop(f"'{i}' is already used by account {', '.join(sorted(taken[i]))}. Pick another --short")

    d = os.path.join(home, "x-" + name)
    acct_md = os.path.join(d, "account.md")
    os.makedirs(d, exist_ok=True)
    os.makedirs(voices_dir(home), exist_ok=True)

    if os.path.exists(acct_md):
        meta = frontmatter(acct_md)
        voice = voice_name(args.voice or meta.get("voice") or name)
        print(f"kept        {acct_md}  (short={meta.get('short') or name}; edit the file to change it)")
    else:
        voice = voice_name(args.voice or name)
        with open(acct_md, "w", encoding="utf-8") as fh:
            fh.write(render_template("account.md", {
                "name": name, "short": short, "handle": args.handle or "",
                "voice": voice, "timezone": args.timezone or "", "poster": args.poster,
            }))
        print(f"created     {acct_md}  (short={short})")

    vpath = os.path.join(voices_dir(home), voice + ".md")
    if os.path.exists(vpath):
        print(f"kept        {vpath}")
    else:
        with open(vpath, "w", encoding="utf-8") as fh:
            fh.write(render_template("voice.md", {"voice": voice}))
        print(f"created     {vpath}  (fill it in, or paste five posts and ask for it to be written)")


def load_env_file(path):
    env = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            env[k.strip()] = v
    return env


def credentials(account):
    """twitter.env for this account, with TWITTER_COOKIE split into the pair twitter-cli reads."""
    path = os.path.join(account["dir"], "twitter.env")
    if not os.path.exists(path):
        raise Stop(f"no {path}. Ask the user to create it, chmod 600, with TWITTER_COOKIE (raw Cookie "
                   f"header), or TWITTER_AUTH_TOKEN and TWITTER_CT0, or TWITTER_BROWSER and TWITTER_CHROME_PROFILE")
    if os.stat(path).st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(f"warning: {path} is readable by others, run: chmod 600 {path}", file=sys.stderr)
    creds = load_env_file(path)
    header = creds.pop("TWITTER_COOKIE", "")
    if header:
        jar = dict(p.strip().split("=", 1) for p in header.split(";") if "=" in p)
        for key, var in (("auth_token", "TWITTER_AUTH_TOKEN"), ("ct0", "TWITTER_CT0")):
            if not jar.get(key):
                raise Stop(f"TWITTER_COOKIE in {path} has no {key}")
            if creds.get(var) and creds[var] != jar[key]:
                raise Stop(f"{var} and TWITTER_COOKIE disagree in {path}, keep one")
            creds[var] = jar[key]
    elif creds.get("TWITTER_AUTH_TOKEN") and creds.get("TWITTER_CT0"):
        header = f"auth_token={creds['TWITTER_AUTH_TOKEN']}; ct0={creds['TWITTER_CT0']}"
    return creds, header


def account_env(account):
    """The process env with inherited TWITTER_* removed, so no other account's login leaks in."""
    creds, _ = credentials(account)
    env = {k: v for k, v in os.environ.items() if not k.startswith("TWITTER_")}
    env.update(creds)
    env["X_AGENT_ACCOUNT"] = account["name"]
    return env


def effective_poster(account):
    poster = account["meta"].get("poster") or "auto"
    if poster != "auto":
        return poster, "account.md"
    path = os.path.join(account["dir"], "twitter.env")
    if not os.path.exists(path):
        return "none", "auto: no twitter.env"
    if shutil.which("twitter"):
        return "twitter-cli", "auto: twitter on PATH"
    creds = load_env_file(path)
    if creds.get("TWITTER_COOKIE") or (creds.get("TWITTER_AUTH_TOKEN") and creds.get("TWITTER_CT0")):
        return "browser", "auto: twitter not on PATH"
    return "none", "auto: twitter not on PATH and no cookie in twitter.env"


def cmd_exec(args):
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if not cmd:
        raise Stop("exec needs a command: account.py exec -- twitter whoami")
    home, _ = resolve_home(args.home)
    account, _ = resolve_account(home, args.account)
    env = account_env(account)
    sys.stdout.flush()
    os.execvpe(cmd[0], cmd, env)


def cmd_verify(args):
    home, _ = resolve_home(args.home)
    account, _ = resolve_account(home, args.account)
    want = account["meta"].get("handle", "").strip().lstrip("@")
    if not want:
        raise Stop(f"no handle: in {os.path.join(account['dir'], 'account.md')}. Set it before publishing")
    try:
        out = subprocess.run(["twitter", "whoami", "--json"], env=account_env(account),
                             capture_output=True, text=True, timeout=90)
    except FileNotFoundError:
        raise Stop("twitter-cli is not on PATH")
    try:
        data = json.loads(out.stdout)
    except ValueError:
        raise Stop("twitter whoami failed: " + (out.stderr or out.stdout).strip()[-300:])
    if not data.get("ok"):
        raise Stop("twitter whoami failed: " + json.dumps(data.get("error"))[:300])
    got = data["data"]["user"]["screenName"]
    if got.lower() != want.lower():
        print(f"MISMATCH: logged in as @{got}, account {account['short']} expects @{want}. Do not post.",
              file=sys.stderr)
        sys.exit(3)
    print(f"ok  @{got} = {account['short']} ({account['name']})")


def cmd_cookie(args):
    home, _ = resolve_home(args.home)
    account, _ = resolve_account(home, args.account)
    _, header = credentials(account)
    if not header:
        raise Stop("twitter.env has no TWITTER_COOKIE or TWITTER_AUTH_TOKEN/TWITTER_CT0 pair, "
                   "so there is no cookie header to hand to a browser")
    path = os.path.join(account["dir"], "cookie.header")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(header + "\n")
    os.chmod(path, 0o600)
    print(path)


# ---------------------------------------------------------------- main

def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", help="workdir holding voices/ and x-<name>/ (overrides env and config)")
    common.add_argument("--account", help="full name, x-<name> or short name")
    common.add_argument("--voice", help="voice file name in <home>/voices/, without .md")

    ap = argparse.ArgumentParser(description="Resolve the X account, voice and file paths for the x-agent skills.")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("where", parents=[common], help="show the workdir and where it came from")
    sub.add_parser("list", parents=[common], help="list accounts and voices")

    p = sub.add_parser("use", parents=[common], help="set the default account")
    p.add_argument("id", nargs="?")
    p.add_argument("--clear", action="store_true", help="remove the default account")

    p = sub.add_parser("paths", parents=[common], help="show every resolved path")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("path", parents=[common], help="print one resolved path")
    p.add_argument("key", choices=PATH_KEYS)

    p = sub.add_parser("init", parents=[common], help="create an account (idempotent)")
    p.add_argument("name")
    p.add_argument("--short", help="short name to switch with (default: the name)")
    p.add_argument("--handle", help="@handle, checked before publishing")
    p.add_argument("--timezone", help="e.g. Europe/Paris")
    p.add_argument("--poster", default="auto", choices=POSTERS)
    p.add_argument("--force", action="store_true", help="allow --home to replace the configured home")

    p = sub.add_parser("exec", parents=[common], help="run a command with the account's twitter.env loaded")
    p.add_argument("cmd", nargs=argparse.REMAINDER)

    sub.add_parser("verify", parents=[common], help="check twitter whoami matches handle: in account.md")
    sub.add_parser("cookie", parents=[common], help="write the raw cookie header to cookie.header, print its path")

    args = ap.parse_args()
    try:
        {"where": cmd_where, "list": cmd_list, "use": cmd_use, "paths": cmd_paths,
         "path": cmd_path, "init": cmd_init, "exec": cmd_exec, "verify": cmd_verify,
         "cookie": cmd_cookie}[args.command](args)
    except Stop as e:
        print(f"x-agent: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
