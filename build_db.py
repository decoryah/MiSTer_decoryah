#!/usr/bin/env python3
"""Build a MiSTer Downloader database from an owner's public, non-archived core repositories.

    python build_db.py --out out                       # discover the owner's repositories
    python build_db.py --out out --repos o/a,o/b       # explicit list, no GitHub API needed

A repository is a core when it is public, not archived, not a fork, and contains at least one
``.rbf`` build. For each one the newest dated build of every core (``name_YYYYMMDD[a-z].rbf``) goes to
``_Arcade/cores/`` and its ``.mra`` files go to ``_Arcade/_Decoryah/_<Name>/``, where ``<Name>`` is
the repository name without the ``Arcade-`` and ``_MiSTer`` parts, keeping ``_alternatives/`` and
``_homebrew/`` below it. Nothing is copied: each file entry points at raw.githubusercontent.com pinned
to the commit it was read from, with its md5 and size, so the source repositories stay the single
source of truth.

Output in ``--out`` (this is the ``db`` branch of the repository):
    db.json, db.json.zip   the Downloader database (the zip is what downloader.ini points at)
    cores.md               human-readable list of what is in it
    cache.json             git blob sha -> md5, so unchanged files are never downloaded again
    status.json            when the last check ran and when the database last changed

Standard library and git only (Python 3.8+).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
# metro_20261018a.rbf, PGM2_20261017e.rbf, gaiapolis_20261014.rbf
DATED = re.compile(r"^(?P<stem>.+?)_(?P<date>\d{8})(?P<rev>[a-z]*)\.rbf$", re.I)
# the words dropped from a repository name to get its folder: Arcade-Metro_MiSTer -> Metro
NAME_NOISE = re.compile(r"(^|[-_ ])(arcade|mister)(?=$|[-_ ])", re.I)

DEFAULTS = {
    "owner": None,                      # default: GITHUB_REPOSITORY_OWNER
    "collection": "_Decoryah",          # MRAs go to _Arcade/<collection>/_<Name>/
    "include": [],                      # extra "owner/name" repositories, scanned even if forks
    "exclude": [],                      # "name" or "owner/name" to skip
    "include_forks": False,
    "strip_prefix": ["Arcade-"],        # removed from build file names, as the official distribution does
    "folder_names": {},                 # "owner/name": "_Folder" to override the derived core folder
    "mra_roots": {},                    # "owner/name": "path/in/repo" when a repository has MRAs in more than one place
    "categories": {},                   # "owner/name": "_Console" | "_Computer" | "_Utility" | "_Other" for non-arcade cores
    # not part of a release: build output, simulation, docs, and work-in-progress folders
    "ignore_paths": r"(^|/)(output_files|sim|docs?|\.github|unsupported|_dev)(/|$)",
    "keep_builds": 1,                   # newest N dated builds kept per core
}


# ---------------------------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------------------------

def api_get(path: str, token: str | None):
    req = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json", "User-Agent": "mister-db-builder"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def list_owner_repos(owner: str, token: str | None) -> list[dict]:
    """The owner's public repositories (this endpoint never lists private ones)."""
    out, page = [], 1
    while True:
        chunk = api_get(f"/users/{owner}/repos?type=owner&per_page=100&page={page}", token)
        out += chunk
        if len(chunk) < 100:
            return out
        page += 1


def select_repos(repos: list[dict], cfg: dict, self_full: str | None = None) -> list[str]:
    """Candidate repositories: public, not archived/disabled, not a fork (unless allowed),
    not excluded, not this repository itself."""
    skip = {x.lower() for x in cfg["exclude"]}
    chosen = []
    for r in repos:
        full, name = r["full_name"], r["name"]
        if self_full and full.lower() == self_full.lower():
            continue
        if full.lower() in skip or name.lower() in skip:
            continue
        if r.get("private") or r.get("visibility", "public") != "public" or r.get("archived") or r.get("disabled"):
            continue
        if r.get("fork") and not cfg["include_forks"]:
            continue
        chosen.append(full)
    return sorted(chosen, key=str.lower)


# ---------------------------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------------------------

def git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace", check=True)


def clone(full: str, work: str) -> str:
    d = os.path.join(work, full.replace("/", "__"))
    if os.path.isdir(os.path.join(d, ".git")):
        git("fetch", "-q", "--filter=blob:limit=1m", "--depth", "1", "origin", "HEAD", cwd=d)
        git("update-ref", "HEAD", "FETCH_HEAD", cwd=d)
    else:
        # MRAs (small) come with the clone; the build files are fetched only when hashing needs them.
        git("clone", "-q", "--filter=blob:limit=1m", "--no-checkout", "--depth", "1", f"https://github.com/{full}.git", d)
    return d


def tree(d: str) -> list[tuple[str, str, int]]:
    """(path, blob sha, size) for every file at HEAD."""
    out = git("ls-tree", "-r", "-l", "-z", "HEAD", cwd=d).stdout
    files = []
    for rec in out.split("\0"):
        if not rec:
            continue
        meta, path = rec.split("\t", 1)
        mode, typ, sha, size = meta.split()
        if typ == "blob":
            files.append((path, sha, int(size)))
    return files


def md5_blob(d: str, sha: str) -> str:
    data = subprocess.run(["git", "cat-file", "blob", sha], cwd=d, capture_output=True, check=True).stdout
    return hashlib.md5(data).hexdigest()


# ---------------------------------------------------------------------------------------------
# one repository -> files of the database
# ---------------------------------------------------------------------------------------------

def strip_prefix(name: str, prefixes: list[str]) -> str:
    for p in prefixes:
        if name.lower().startswith(p.lower()):
            return name[len(p):]
    return name


def core_folder(full: str, cfg: dict) -> str:
    """Arcade-Metro_MiSTer -> _Metro, Arcade-MagicalCatAdventure_Nostradamus_MiSTer -> _MagicalCatAdventure_Nostradamus."""
    if full in cfg["folder_names"]:
        return "_" + cfg["folder_names"][full].lstrip("_")
    name = NAME_NOISE.sub("", full.split("/", 1)[1]).strip("-_ ")
    return "_" + (name or full.split("/", 1)[1])


def mra_root(mras: list[str], forced: str | None) -> tuple[str, list[str]]:
    """The folder whose MRAs are published, and the other MRA folders that are left out.

    The roots are the folders with MRAs that have no ancestor folder with MRAs (``mra/`` rather than
    ``mra/_alternatives/_X/``). Some repositories ship the same MRAs twice (``mra/`` and
    ``releases/_Arcade/``); the root with the most MRAs wins, then the shortest path."""
    dirs = {posixpath.dirname(p) for p in mras}

    def under(p: str, root: str) -> bool:
        return root == "" or p == root or p.startswith(root + "/")

    roots = [d for d in dirs if not any(o != d and under(d, o) for o in dirs)]
    if forced is not None:
        forced = forced.strip("/")
        return forced, sorted(r for r in roots if not under(r, forced))
    count = {r: sum(under(posixpath.dirname(p), r) for p in mras) for r in roots}
    roots.sort(key=lambda r: (-count[r], r.count("/"), len(r), r))
    return roots[0], roots[1:]


def scan_repo(full: str, work: str, cfg: dict, cache: dict) -> dict | None:
    """Everything this repository contributes, or None when it has no ``.rbf`` build."""
    d = clone(full, work)
    head = git("rev-parse", "HEAD", cwd=d).stdout.strip()
    ignore = re.compile(cfg["ignore_paths"], re.I)
    files = [f for f in tree(d) if not ignore.search(f[0])]
    rbfs = [f for f in files if f[0].lower().endswith(".rbf")]
    if not rbfs:
        return None
    mras = [f for f in files if f[0].lower().endswith(".mra") and f[2] > 0]

    # newest N dated builds per core name (undated builds are their own core)
    cores: dict[str, list[tuple[tuple[str, str], str, str, int]]] = {}
    for path, sha, size in rbfs:
        base = posixpath.basename(path)
        m = DATED.match(base)
        stem, when = (m.group("stem"), (m.group("date"), m.group("rev").lower())) if m else (base[:-4], ("", ""))
        cores.setdefault(stem.lower(), []).append((when, path, sha, size))
    builds = []
    for stem, items in cores.items():
        items.sort(key=lambda x: (x[0], -len(x[1])), reverse=True)
        builds += items[: max(1, int(cfg["keep_builds"]))]

    category = cfg["categories"].get(full) or ("_Arcade" if mras else "_Other")
    folder = core_folder(full, cfg)
    entries: dict[str, dict] = {}

    def add(dest: str, path: str, sha: str, size: int):
        if sha not in cache:
            cache[sha] = md5_blob(d, sha)
        entries[dest] = {"hash": cache[sha], "size": size,
                         "url": f"{RAW}/{full}/{head}/{urllib.parse.quote(path)}"}

    for when, path, sha, size in builds:
        name = strip_prefix(posixpath.basename(path), cfg["strip_prefix"])
        add(f"_Arcade/cores/{name}" if category == "_Arcade" else f"{category}/{name}", path, sha, size)

    published, left_out = 0, []
    if mras and category == "_Arcade":
        root, left_out = mra_root([p for p, _, _ in mras], cfg["mra_roots"].get(full))
        top = "/".join(x.strip("/") for x in ("_Arcade", cfg["collection"], folder) if x.strip("/"))
        for path, sha, size in mras:
            if root and not path.startswith(root + "/"):
                continue
            add(f"{top}/{path[len(root) + 1:] if root else path}", path, sha, size)
            published += 1
        if left_out:
            print(f"  {full}: MRAs taken from '{root or '.'}/'; also found in (not published): "
                  + ", ".join(f"'{r or '.'}/'" for r in left_out))

    return {"repo": full, "head": head, "category": category, "folder": folder if published else "",
            "files": entries, "mras": published,
            "builds": [(posixpath.basename(p), w[0]) for w, p, _, _ in builds]}


# ---------------------------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------------------------

DB_VERSION = 1      # "v": the version of the Downloader database format (a spec field Downloader reads, not a build counter)


def assemble(scans: list[dict], db_id: str, db_url: str) -> dict:
    files: dict[str, dict] = {}
    for s in sorted(scans, key=lambda s: s["repo"].lower()):
        for dest, e in s["files"].items():
            if dest in files:
                print(f"warning: {dest} from {s['repo']} collides with an earlier repository; keeping the first", file=sys.stderr)
                continue
            files[dest] = e
    folders: dict[str, dict] = {}
    for dest in files:
        parts = dest.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            folders["/".join(parts[:i])] = {}
    return {"db_id": db_id, "db_url": db_url, "v": DB_VERSION, "timestamp": int(dt.datetime.now(dt.timezone.utc).timestamp()),
            "files": dict(sorted(files.items())), "folders": dict(sorted(folders.items()))}


def signature(db: dict) -> dict:
    """What decides whether the database changed: paths, hashes, sizes (not URLs or the timestamp)."""
    return {p: (e["hash"], e["size"]) for p, e in db.get("files", {}).items()}


def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path: str, obj, **kw):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, **kw)
        f.write("\n")


def cores_markdown(scans: list[dict], db: dict, cfg: dict, when: str) -> str:
    lines = ["# Cores in this database", "", f"Updated {when}. {len(db['files'])} files from {len(scans)} repositories.", "",
             "| Repository | MRA folder | Builds | Latest build | MRAs |", "|---|---|---|---|---|"]
    for s in sorted(scans, key=lambda s: s["repo"].lower()):
        names = ", ".join(f"`{n}`" for n, _ in s["builds"])
        latest = max((d for _, d in s["builds"] if d), default="")
        latest = f"{latest[:4]}-{latest[4:6]}-{latest[6:]}" if latest else ""
        where = f"`_Arcade/{cfg['collection'].strip('/')}/{s['folder']}`" if s["folder"] else ""
        lines.append(f"| [{s['repo']}](https://github.com/{s['repo']}) | {where} | {names} | {latest} | {s['mras']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="output directory (the db branch checkout)")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
    ap.add_argument("--repos", help="comma-separated owner/name list instead of asking the GitHub API")
    ap.add_argument("--owner", help="GitHub user whose repositories to scan (default: config or GITHUB_REPOSITORY_OWNER)")
    ap.add_argument("--work", help="directory for repository clones (default: a temporary one)")
    ap.add_argument("--force", action="store_true", help="publish even when nothing changed")
    a = ap.parse_args()

    cfg = {**DEFAULTS, **load_json(a.config, {})}
    token = os.environ.get("GITHUB_TOKEN")
    self_full = os.environ.get("GITHUB_REPOSITORY")
    owner = a.owner or cfg["owner"] or os.environ.get("GITHUB_REPOSITORY_OWNER") or (self_full or "/").split("/")[0]
    if a.repos:
        candidates = [r.strip() for r in a.repos.split(",") if r.strip()]
    else:
        if not owner:
            ap.error("no owner: pass --owner, set it in config.json, or run inside GitHub Actions")
        candidates = select_repos(list_owner_repos(owner, token), cfg, self_full)
    candidates += [r for r in cfg["include"] if r not in candidates]
    db_id = self_full or f"{owner}/MiSTer_{owner}"
    db_url = f"{RAW}/{db_id}/db/db.json.zip"

    os.makedirs(a.out, exist_ok=True)
    cache = load_json(os.path.join(a.out, "cache.json"), {})
    work = a.work or tempfile.mkdtemp(prefix="mister-db-")
    os.makedirs(work, exist_ok=True)
    scans, skipped = [], []
    for full in candidates:
        try:
            s = scan_repo(full, work, cfg, cache)
        except subprocess.CalledProcessError as e:
            print(f"warning: {full}: {(e.stderr or str(e)).strip().splitlines()[-1:]}", file=sys.stderr)
            continue
        if s is None:
            skipped.append(full)
            continue
        scans.append(s)
        print(f"{full}: {len(s['builds'])} build(s), {s['mras']} MRAs -> {s['folder'] or s['category']}, pinned at {s['head'][:8]}")
    print(f"{len(candidates)} candidate repositories, {len(scans)} with a core build; skipped (no .rbf): {', '.join(skipped) or '-'}")
    if not scans:
        print("error: no core repositories found; refusing to publish an empty database", file=sys.stderr)
        return 1

    new = assemble(scans, db_id, db_url)
    old = load_json(os.path.join(a.out, "db.json"), {})
    now = dt.datetime.now(dt.timezone.utc)
    status = load_json(os.path.join(a.out, "status.json"), {})
    changed = signature(new) != signature(old) or old.get("v") != DB_VERSION
    if changed or a.force:
        write_json(os.path.join(a.out, "db.json"), new, indent=1)
        with zipfile.ZipFile(os.path.join(a.out, "db.json.zip"), "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("db.json", json.dumps(new, ensure_ascii=False, indent=1) + "\n")
        with open(os.path.join(a.out, "cores.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(cores_markdown(scans, new, cfg, now.strftime("%Y-%m-%d")))
        status["changed"] = now.isoformat(timespec="seconds")
    # only blobs still in use stay in the cache
    live = {e["hash"] for e in new["files"].values()}
    write_json(os.path.join(a.out, "cache.json"), {k: v for k, v in sorted(cache.items()) if v in live})
    # status.json is touched when the database changed, or monthly so the repository never looks idle
    # (GitHub disables scheduled workflows in repositories without recent activity)
    last = dt.datetime.fromisoformat(status["checked"]) if status.get("checked") else None
    if changed or a.force or last is None or (now - last).days >= 30:
        status["checked"] = now.isoformat(timespec="seconds")
    write_json(os.path.join(a.out, "status.json"), status, indent=1)
    print("database changed" if changed else "database unchanged", f"({len(new['files'])} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
