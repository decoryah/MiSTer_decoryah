# MiSTer_decoryah

A [Downloader](https://github.com/MiSTer-devel/Downloader_MiSTer) database of [decoryah](https://github.com/decoryah)'s
public MiSTer arcade cores. It is rebuilt every 6 hours by GitHub Actions, so the cores, their latest builds and
their MRAs on your MiSTer follow the source repositories.

## Install

Add this to `downloader.ini` on the MiSTer's SD card (the cores are then installed by `update_all`
or Downloader like any others):

```ini
[decoryah/MiSTer_decoryah]
db_url = https://raw.githubusercontent.com/decoryah/MiSTer_decoryah/db/db.json.zip
```

## Cores

What is in the database right now. Each run refreshes this list whenever the database changes; the same
list is in [`cores.md`](https://github.com/decoryah/MiSTer_decoryah/blob/db/cores.md) on the `db` branch.

<!-- cores:start -->
Updated 2026-10-18. 153 files from 11 repositories.

| Repository | MRA folder | Builds | Latest build | MRAs |
|---|---|---|---|---|
| [decoryah/Arcade-Gaiapolis_MiSTer](https://github.com/decoryah/Arcade-Gaiapolis_MiSTer) | `_Arcade/_Decoryah/_Gaiapolis` | `gaiapolis_20261014.rbf` | 2026-10-14 | 3 |
| [decoryah/Arcade-HeavyUnit_MiSTer](https://github.com/decoryah/Arcade-HeavyUnit_MiSTer) | `_Arcade/_Decoryah/_HeavyUnit` | `heavyunit_20261017.rbf` | 2026-10-17 | 5 |
| [decoryah/Arcade-IGSPGM2_MiSTer](https://github.com/decoryah/Arcade-IGSPGM2_MiSTer) | `_Arcade/_Decoryah/_IGSPGM2` | `PGM2_20261017e.rbf` | 2026-10-17 | 21 |
| [decoryah/Arcade-JackieChan_MiSTer](https://github.com/decoryah/Arcade-JackieChan_MiSTer) | `_Arcade/_Decoryah/_JackieChan` | `jchan_20261014.rbf` | 2026-10-14 | 3 |
| [decoryah/Arcade-MagicalCatAdventure_Nostradamus_MiSTer](https://github.com/decoryah/Arcade-MagicalCatAdventure_Nostradamus_MiSTer) | `_Arcade/_Decoryah/_MagicalCatAdventure_Nostradamus` | `mcatadv_20261005.rbf` | 2026-10-05 | 6 |
| [decoryah/Arcade-MetamorphicForce_MiSTer](https://github.com/decoryah/Arcade-MetamorphicForce_MiSTer) | `_Arcade/_Decoryah/_MetamorphicForce` | `metamorphicforce_20261014.rbf` | 2026-10-14 | 5 |
| [decoryah/Arcade-Metro_MiSTer](https://github.com/decoryah/Arcade-Metro_MiSTer) | `_Arcade/_Decoryah/_Metro` | `metro_20261018a.rbf`, `metrod64_20261018a.rbf`, `metrof64_20261014c.rbf` | 2026-10-18 | 47 |
| [decoryah/Arcade-MonsterMaulers_MiSTer](https://github.com/decoryah/Arcade-MonsterMaulers_MiSTer) | `_Arcade/_Decoryah/_MonsterMaulers` | `monstermaulers_20261014.rbf` | 2026-10-14 | 3 |
| [decoryah/Arcade-OhMyGod_MiSTer](https://github.com/decoryah/Arcade-OhMyGod_MiSTer) | `_Arcade/_Decoryah/_OhMyGod` | `ohmygod_20261010.rbf` | 2026-10-10 | 2 |
| [decoryah/Arcade-TaitoLSystem_MiSTer](https://github.com/decoryah/Arcade-TaitoLSystem_MiSTer) | `_Arcade/_Decoryah/_TaitoLSystem` | `taitol_20261007.rbf` | 2026-10-07 | 38 |
| [decoryah/Arcade-ViolentStorm_MiSTer](https://github.com/decoryah/Arcade-ViolentStorm_MiSTer) | `_Arcade/_Decoryah/_ViolentStorm` | `violentstorm_20261014.rbf` | 2026-10-14 | 7 |
<!-- cores:end -->

## Layout on the SD card

```
_Arcade/
├── cores/
│   ├── metro_20261018a.rbf
│   ├── taitol_20261007.rbf
│   └── …
└── _Decoryah/
    ├── _Metro/
    │   ├── Blazing Tornado.mra
    │   ├── …
    │   └── _alternatives/
    │       └── _Pang Pom's/
    │           └── Pang Pom's (Nova).mra
    ├── _TaitoLSystem/
    │   ├── Puzznic (World).mra
    │   ├── …
    │   ├── _alternatives/…
    │   └── _homebrew/
    │       └── Sokoban LE.mra
    └── …
```

Each core repository gets its own folder `_Arcade/_Decoryah/_<Name>`, where `<Name>` is the repository name
without `Arcade-` and `_MiSTer` (`Arcade-MagicalCatAdventure_Nostradamus_MiSTer` → `_MagicalCatAdventure_Nostradamus`).
Everything below the repository's MRA folder (`_alternatives/…`, `_homebrew/…`) is kept as it is.

The builds go to `_Arcade/cores/`. MiSTer looks for an MRA's core in the `cores` folder of the first `_` folder
in the MRA's path (here `_Arcade`), so MRAs nested this deep still find it.

## What goes in

Every repository of the owner that is **public, not archived, not a fork** and contains at least one
`.rbf` build. New core repositories are picked up automatically; archiving a repository or making it
private removes it at the next run.

For each such repository the database lists

- the **newest dated build of every core** (`name_YYYYMMDD.rbf`, also with a letter suffix such as
  `metro_20261018a.rbf`) as `_Arcade/cores/<file>`, and
- **all of its `.mra` files** as `_Arcade/_Decoryah/_<Name>/…`.

If a repository has its MRAs in more than one place (Arcade-IGSPGM2_MiSTer has the same set in `mra/` and
`releases/_Arcade/`), the folder with the most MRAs is used, and on a tie the shorter path. The run log names
the folder it left out; `mra_roots` in `config.json` picks one explicitly.

Nothing is copied into this repository. Each file entry carries its md5, its size and a
raw.githubusercontent.com URL pinned to the commit it was read from, so the source repositories remain the
single source of truth and downloads are consistent even while a repository is being pushed to.
When a newer build replaces the old one in the database, Downloader removes the old file from the MiSTer.

ROMs are not part of the database.

## How it updates

`.github/workflows/update.yml` runs every 6 hours (also on demand from the Actions tab, and when the script or
config changes on `main`). It runs `build_db.py`, which

1. lists the owner's public repositories through the GitHub API,
2. clones the candidates (tree only, no checkout) and finds the builds and MRAs,
3. hashes only files it has not seen before (`cache.json` maps git blob ids to md5),
4. publishes `db.json`, `db.json.zip`, `cores.md`, `cache.json` and `status.json` to the **`db` branch**,
   but only when a path, hash or size actually changed,
5. copies the list from `cores.md` into the [Cores](#cores) section of this README on `main`
   (between the `cores:start` and `cores:end` comment lines) when it differs.

The `db` branch is machine-written; the source lives on `main`. On `main` the workflow only ever changes the
core list in this README, so run `git pull` before pushing if it has updated it.

GitHub switches off scheduled workflows in repositories with no recent activity. When the database has not
changed for a month, the run records a heartbeat in `status.json`, which keeps the schedule alive.

## Configuration

`config.json` (all optional):

| Key | Default | Meaning |
|---|---|---|
| `owner` | repository owner | GitHub user whose repositories are scanned |
| `collection` | `"_Decoryah"` | folder under `_Arcade/` that holds the per-core MRA folders |
| `include` | `[]` | extra `owner/name` repositories to scan, even forks or other owners' |
| `exclude` | `[]` | repository names (or `owner/name`) to skip |
| `include_forks` | `false` | also scan the owner's forks |
| `keep_builds` | `1` | newest N dated builds kept per core |
| `strip_prefix` | `["Arcade-"]` | prefixes removed from build file names |
| `folder_names` | `{}` | `"owner/name": "_Folder"` to override a derived core folder name |
| `mra_roots` | `{}` | `"owner/name": "path/in/repo"`: the MRA folder to use when there are several |
| `categories` | `{}` | `"owner/name": "_Console"` etc. for repositories that are not arcade cores |
| `ignore_paths` | build/doc folders | regular expression of paths to ignore |

Repositories with a build but no MRAs go to `_Other` unless `categories` says otherwise.

## Run it locally

```bash
python build_db.py --out out --repos decoryah/Arcade-Metro_MiSTer,decoryah/Arcade-TaitoLSystem_MiSTer
```

`--repos` skips the GitHub API; without it the script asks the API for the owner's public repositories
(`GITHUB_TOKEN` is optional and only raises the rate limit). `--readme README.md` also refreshes the core
list here. Python 3.8+ and git; no packages.
