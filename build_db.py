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
    cores.md               human-readable list of what is in it (with --readme also copied into the README)
    cache.json             git blob sha -> md5, so unchanged files are never downloaded again
    status.json            when the last check ran and when the database last changed
    external_files.csv     ROM zip download list derived from the published MRAs (when enabled)
    external_cache.json    remembered ROM zip sizes and md5s for archive.org outages
    external_missing.json  ROM zip groups unavailable in the configured merged set

Standard library and git only (Python 3.8+).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
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

ZIP_ATTR = re.compile(r"""<rom\b[^>]*?\bzip\s*=\s*["']([^"']+)["']""", re.I | re.S)

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
    # ROM zips the MRAs name, listed as external files (not copied; downloaded from the archive.org MAME set)
    "external": {
        "enabled": True,
        "dest": "games/mame",
        "item": "mame-roms-merged_",
        "folder": "MAME ROMs (merged)",
        "fallback_csv": "https://raw.githubusercontent.com/meathax/meatcores/main/external_files.csv",
    },
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
    zips: set[str] = set()
    groups: set[tuple[str, ...]] = set()
    if mras and category == "_Arcade":
        root, left_out = mra_root([p for p, _, _ in mras], cfg["mra_roots"].get(full))
        top = "/".join(x.strip("/") for x in ("_Arcade", cfg["collection"], folder) if x.strip("/"))
        for path, sha, size in mras:
            if root and not path.startswith(root + "/"):
                continue
            add(f"{top}/{path[len(root) + 1:] if root else path}", path, sha, size)
            published += 1
            if cfg["external"]["enabled"]:
                text = subprocess.run(["git", "cat-file", "blob", sha], cwd=d, capture_output=True, check=True).stdout.decode("utf-8", "replace")
                for m in ZIP_ATTR.finditer(text):
                    names = tuple(z.strip() for z in m.group(1).split("|") if z.strip().lower().endswith(".zip"))
                    zips.update(names)
                    groups.add(names)
        if left_out:
            print(f"  {full}: MRAs taken from '{root or '.'}/'; also found in (not published): "
                  + ", ".join(f"'{r or '.'}/'" for r in left_out))

    return {"repo": full, "head": head, "category": category, "folder": folder if published else "",
            "files": entries, "mras": published, "zips": sorted(zips), "zip_groups": sorted(groups),
            "builds": [(posixpath.basename(p), w[0]) for w, p, _, _ in builds]}


# ---------------------------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------------------------

DB_VERSION = 1      # "v": the version of the Downloader database format (a spec field Downloader reads, not a build counter)


def assemble(scans: list[dict], db_id: str, db_url: str, external: dict[str, dict] | None = None) -> dict:
    files: dict[str, dict] = {}
    for s in sorted(scans, key=lambda s: s["repo"].lower()):
        for dest, e in s["files"].items():
            if dest in files:
                print(f"warning: {dest} from {s['repo']} collides with an earlier repository; keeping the first", file=sys.stderr)
                continue
            files[dest] = e
    for dest, e in (external or {}).items():
        files.setdefault(dest, e)
    folders: dict[str, dict] = {}
    for dest in files:
        parts = dest.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            folders["/".join(parts[:i])] = {}
    return {"db_id": db_id, "db_url": db_url, "v": DB_VERSION, "timestamp": int(dt.datetime.now(dt.timezone.utc).timestamp()),
            "files": dict(sorted(files.items())), "folders": dict(sorted(folders.items()))}


def fetch(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "mister-db-builder"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def rom_index(ext: dict) -> dict[str, tuple[int, str]]:
    """zip name -> (size, md5) of every zip in the archive.org item, falling back to a published CSV."""
    index: dict[str, tuple[int, str]] = {}
    try:
        meta = json.loads(fetch(f"https://archive.org/metadata/{ext['item']}", 300))
        prefix = ext["folder"].strip("/") + "/"
        for f in meta.get("files", []):
            n = f.get("name", "")
            if n.startswith(prefix) and n.lower().endswith(".zip") and "/" not in n[len(prefix):] and f.get("md5") and f.get("size"):
                index[n[len(prefix):]] = (int(f["size"]), f["md5"].lower())
    except Exception as e:
        print(f"warning: archive.org metadata unavailable ({e})", file=sys.stderr)
    if not index and ext.get("fallback_csv"):
        try:
            for row in csv.reader(fetch(ext["fallback_csv"]).decode("utf-8").splitlines()[1:]):
                if len(row) >= 4 and row[2].isdigit() and row[3]:
                    index[posixpath.basename(row[0])] = (int(row[2]), row[3].lower())
        except Exception as e:
            print(f"warning: fallback external list unavailable ({e})", file=sys.stderr)
    return index


def external_files(scans: list[dict], ext: dict, known: dict) -> tuple[dict[str, dict], list[str]]:
    """Database entries for every ROM zip the scanned MRAs name. `known` is the remembered
    zip -> [size, md5], updated in place so an archive.org outage never drops entries."""
    wanted = sorted({z for s in scans for z in s.get("zips", [])}, key=str.lower)
    if any(z not in known for z in wanted):
        for z, (size, md5) in rom_index(ext).items():
            known[z] = [size, md5]
    base = f"https://archive.org/download/{ext['item']}/{urllib.parse.quote(ext['folder'])}"
    entries = {}
    for z in wanted:
        if z not in known:
            continue
        size, md5 = known[z]
        # overwrite=false: a zip the user already has (from any set) is left alone
        entries[f"{ext['dest'].strip('/')}/{z}"] = {"hash": md5, "size": size, "url": f"{base}/{urllib.parse.quote(z)}", "overwrite": False}
    # a clone's zip is normally absent from a merged set; the MRA then names its parent too ("clone.zip|parent.zip"),
    # so a zip only counts as missing when none of the zips in its list is available
    missing = sorted({g[0] for s in scans for g in s.get("zip_groups", []) if g and not any(z in known for z in g)}, key=str.lower)
    return entries, missing


def external_csv(entries: dict[str, dict]) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["Path in MiSTer", "URL", "Size in bytes (optional but recommended)", "MD5 Hash (optional but recommended)", "Filter Terms (optional)"])
    for p, e in sorted(entries.items()):
        w.writerow([p, e["url"], e["size"], e["hash"], ""])
    return out.getvalue()


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


README_START, README_END = "<!-- cores:start -->", "<!-- cores:end -->"


def splice_readme(readme: str, cores_md: str) -> str | None:
    """``readme`` with the list of ``cores_md`` (without its title) between the markers, each on a line
    of its own, or None without them."""
    m = re.search(rf"^{re.escape(README_START)}[ \t]*$(.*?)^{re.escape(README_END)}[ \t]*$", readme, re.M | re.S)
    if not m:
        return None
    body = re.sub(r"\A#[^\n]*\n+", "", cores_md).strip()
    return f"{readme[:m.start(1)]}\n{body}\n{readme[m.end(1):]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="output directory (the db branch checkout)")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
    ap.add_argument("--repos", help="comma-separated owner/name list instead of asking the GitHub API")
    ap.add_argument("--owner", help="GitHub user whose repositories to scan (default: config or GITHUB_REPOSITORY_OWNER)")
    ap.add_argument("--work", help="directory for repository clones (default: a temporary one)")
    ap.add_argument("--force", action="store_true", help="publish even when nothing changed")
    ap.add_argument("--readme", help=f"also put the core list into this file, between {README_START} and {README_END}")
    a = ap.parse_args()

    cfg = {**DEFAULTS, **load_json(a.config, {})}
    cfg["external"] = {**DEFAULTS["external"], **(cfg.get("external") or {})}
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

    ext = cfg["external"]
    ext_entries: dict[str, dict] = {}
    if ext["enabled"]:
        known = load_json(os.path.join(a.out, "external_cache.json"), {})
        ext_entries, missing = external_files(scans, ext, known)
        live_zips = {p.rsplit("/", 1)[1] for p in ext_entries}
        write_json(os.path.join(a.out, "external_cache.json"), {z: known[z] for z in sorted(known) if z in live_zips}, indent=0)
        print(f"{len(ext_entries)} external ROM zips; not in the archive.org set: {len(missing)}")
        write_json(os.path.join(a.out, "external_missing.json"), missing, indent=0)
        with open(os.path.join(a.out, "external_files.csv"), "w", encoding="utf-8", newline="") as f:
            f.write(external_csv(ext_entries))
    else:
        # Do not leave a previously published ROM list active after disabling the feature.
        for name in ("external_files.csv", "external_cache.json", "external_missing.json"):
            path = os.path.join(a.out, name)
            if os.path.exists(path):
                os.remove(path)
    new = assemble(scans, db_id, db_url, ext_entries)
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
    if a.readme:
        # from cores.md rather than this run's scans, so the README always shows what is published
        try:
            with open(a.readme, encoding="utf-8") as f:
                text = f.read()
            with open(os.path.join(a.out, "cores.md"), encoding="utf-8") as f:
                spliced = splice_readme(text, f.read())
        except OSError as e:
            print(f"warning: README not updated ({e})", file=sys.stderr)
        else:
            if spliced is None:
                print(f"warning: {a.readme} has no {README_START} ... {README_END} section; not updated", file=sys.stderr)
            elif spliced != text:
                with open(a.readme, "w", encoding="utf-8", newline="\n") as f:
                    f.write(spliced)
                print(f"{a.readme} updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
