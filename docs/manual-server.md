# Products server manual

For the person who runs the products server. The server is **cupdev0** (`cupsoft@192.168.0.150`). Run everything
as **cupsoft** unless a step says root. For nodes, see the [client manual](manual-client.md).

## Contents

1. [What the server does](#1-what-the-server-does)
2. [Directory layout](#2-directory-layout)
3. [Install, upgrade and roll back the tool](#3-install-upgrade-and-roll-back-the-tool)
4. [System packages](#4-system-packages)
5. [Release a new vlist](#5-release-a-new-vlist)
6. [Fix a released modulefile (revision)](#6-fix-a-released-modulefile-revision)
7. [Withdraw or remove a bad release](#7-withdraw-or-remove-a-bad-release)
8. [Checks](#8-checks)
9. [Don'ts](#9-donts)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. What the server does

- Products are **built at their final path** (`/home/cupsoft/Products/<NAME>/<VERSION>`). People do the builds.
- `prod release` bundles versions into a **vlist**. After a release, those versions and the vlist are locked
  (`a-w`) and never change.
- Clients only **read** from the server (`prod sync` pulls with rsync). The server never pushes anything.

Terms:

| Term | Meaning |
|---|---|
| vlist | A named set of product versions (`4.0`, `daq_2.1`), in `Products/vlist/<name>.yml` |
| staging | A version directory with no manifest yet (not released). You may change or delete it |
| frozen | A released version: it has a manifest and is locked. Never change it |
| manifest | List of every file in a version directory with its sha256: `<NAME>/<VERSION>.manifest` |
| fixed modulefile | The modulefile written for a version at release time: `modulefiles/<NAME>/<VERSION>` |

## 2. Directory layout

```
/home/cupsoft/Products/
├── .prod/
│   ├── current -> versions/vX.Y        the tool in use
│   ├── versions/vX.Y/                  the installed tool version (read-only)
│   ├── etc/prod.conf                   settings (role: server)
│   └── state/                          history.jsonl, packages.yml, lock
├── vlist/
│   ├── drafts/4.1.yml                  a draft before release (you write it)
│   ├── 4.0.yml                         a released vlist (locked)
│   └── 3.0.withdrawn                   withdrawal record (only if withdrawn)
├── modulefiles/
│   ├── ROOT/V6.40.04_3.14.9            fixed modulefile (locked)
│   └── vlist/4.0                       modulefile that loads the whole vlist (locked)
└── ROOT/
    ├── V6.40.04_3.14.9/                binaries (locked after release)
    ├── V6.40.04_3.14.9.manifest
    └── V6.40.04_3.14.9.recipe.yml      the recipe it was built with, and its buildinfo (saved by build.sh)
```

Files that `prod` does not manage (`CERNLIB.tgz`, unreleased versions, ...) are listed by `prod list` under "not
managed by prod". `prod` never deletes them. A directory with no version directories (`V…`) in it, such as the pyenv
checkout `PYENV/`, is shown as one line: `PYENV/  (whole directory, no versions in it)`.

## 3. Install, upgrade and roll back the tool

### 3.1 First install

```bash
git clone https://github.com/cup-software2018/prodmanager.git
cd prodmanager
git checkout vX.Y                                  # install tags only
./install.sh --role server
git checkout main                                  # back to main, for building (5)
```

`install.sh`:

- copies the tool to `/home/cupsoft/Products/.prod/versions/vX.Y/` and points `current` to it;
- creates `.prod/etc/prod.conf` only if it does not exist (`role: server`);
- links `~/setup_prod.sh`, `~/setup_prod.csh` and `~/.local/bin/prod` to it (a real file in their place is moved to
  `<name>.bak` first);
- ends with `prod doctor --quick`. Problems there do not fail the install.

Keep the clone: the later steps (upgrades, builds, releases) run in it.

If a package the tool needs is missing (`environment-modules`, `rsync`, `openssh-clients`, `python3`,
`python3-pyyaml`, `git`), `install.sh` stops and tells you what to install.

### 3.2 Upgrade

In your clone of the repository:

```bash
git fetch --tags && git checkout vX.Y
./install.sh
git checkout main                                  # back to main, for building (5)
prod --version
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh --check   # as root; see section 4
```

- `prod.conf` and `state/` stay. Only the installed tool version is kept; the others are removed.
- **If the new tag changed `packages/`, run `postinstall.sh` (section 4).** `--check` shows whether anything is
  missing.
- New modulefile templates are used from the next release on. Released vlists do not change.
- The installed tool does not use the clone, so the clone can stay on `main` between upgrades.

### 3.3 Roll back

Check out the older tag and install it:

```bash
git checkout vX.Y && ./install.sh
git checkout main
prod --version
```

### 3.4 Released versions are read-only

For example, **nobody can `pip install` into a released PYTHON**. Users install into their home or a venv
([client manual 3.1](manual-client.md#31-adding-python-packages-pip)). A package everyone needs goes into a new
PYTHON version and a new vlist.

## 4. System packages

The package lists are in `packages/*.yml` (groups: `base`, `root`, `geant4`, `python`, `daq`, `caen`) and are
installed with the tool. Run as **root**:

```bash
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh --check   # only show what would be installed
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh           # install
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh --group root --group daq   # some groups only
```

- Enables the crb and epel repositories. Never runs `dnf update`.
- If a group fails, it retries one package at a time and reports **only the failed packages**.
- `optional` packages (R) only warn. `R-Rcpp-devel` and `R-RInside-devel` are not in the repositories now.
- Log: `/var/log/prodmanager-postinstall.log`. Record: `Products/.prod/state/packages.yml`.
- Exit code 1 if any required package failed. `prod doctor` (as cupsoft) shows what is missing.

## 5. Release a new vlist

Everything happens on the server, in your clone of the repository:

```bash
git checkout main && git pull                                # the newest recipes (no tag, no install)
recipes/CUPDAQ/build.sh 4.3.2                                # build, check, save the recipe next to it
vi /home/cupsoft/Products/vlist/drafts/daq_3.1.yml           # write a draft
prod release daq_3.1 --check
prod release daq_3.1
```

- `build.sh` saves the recipe next to the new version, as `<NAME>/<VERSION>.recipe.yml`. `prod release` reads it
  there, so **a new version needs no new tag and no tool upgrade**.
- You need a tag and a tool upgrade ([3.2](#32-upgrade)) only when the tool itself changes: a new product name (it
  needs a modulefile template, [5.6](#56-modulefile-template-for-a-new-product)), a changed template, the tool's code,
  or `packages/`.

### 5.1 Write the recipe

Every version needs a **recipe**, `recipes/<NAME>/<VERSION>.yml`:

```yaml
# recipes/ROOT/V6.40.04_3.14.9.yml
product: "ROOT"
version: "V6.40.04_3.14.9"
depends:
  - {name: "PYTHON", version: "V3.14.9"}      # loaded with exactly this version
source:
  url: "https://root.cern/download/root_v6.40.04.source.tar.gz"
options:                                       # the cmake options of build.sh
  - "-DCMAKE_BUILD_TYPE=Release"
  - "-Dpyroot=ON"
```

| Field | Used by |
|---|---|
| `product`, `version` | must match the file location |
| `depends` | `build.sh` loads them; `prod release` checks that they are earlier in the vlist |
| `source` | `build.sh` gets the source: `{url}` downloads an archive, `{url, tag}` checks out that git tag |
| `options` | `build.sh`: the cmake options (`./configure` options for PYTHON) |

- `#` comments are free. Nothing else is allowed in a recipe. (`built` and `buildinfo` are added by `build.sh` to the
  saved copy; don't write them yourself.)
- PYTHON also needs `<VERSION>.requirements.txt` (pip packages) next to its recipe.
- **Our own packages from GitHub** (CUPDATAMODEL, CUPDAQ) name a git tag:

  ```yaml
  depends:
    - {name: "ROOT", version: "V6.32.20_3.11.14"}
    - {name: "HDF5", version: "V1.14.2"}
  source:
    url: "https://github.com/cup-software2018/CUPDataModel.git"
    tag: "v1.4"
  options:
    - "-DCMAKE_BUILD_TYPE=Release"
    - "-DCUPDM_AUTO_INSTALL_DEPENDENCIES=OFF"
    - "-DCUPDM_ENABLE_HDF5=ON"
  ```

- **The same source with other dependencies** (v1.4 with ROOT 6.32 and with ROOT 6.40): give each build its own
  version, `V1.4.1.yml` and `V1.4.2.yml`, both with `tag: "v1.4"`. Drafts still say `V1.4` (5.3).
- A **new product** (a name that does not exist yet) also needs a modulefile template
  ([5.6](#56-modulefile-template-for-a-new-product)). The template is part of the tool: tag and upgrade the tool
  ([3.2](#32-upgrade)) before you release the product. `recipes/<NAME>/build.sh` and the optional
  `recipes/<NAME>/buildinfo.sh` are run from where you build.
- The recipes only have to be on the products server where you build (for example a copy of `recipes/`). They don't
  have to be committed: the copy saved next to the version is the record.

### 5.2 Build (staging)

Products are not relocatable, so **build at the final path**. Until the release you may change, delete or rebuild
the directory; clients do not see it.

For these products the repository has a build script. It gets the recipe's source and builds, tests, installs and
checks the product. The argument is the version **without the V**: `build.sh 1.4.1` builds the recipe `V1.4.1.yml`
into `<Products>/<NAME>/V1.4.1`.

| Product | Command |
|---|---|
| PYTHON | `recipes/PYTHON/build.sh 3.14.9` |
| ROOT | `recipes/ROOT/build.sh 6.40.04_3.14.9` (the PYTHON comes from the recipe's `depends`) |
| GEANT4 | `recipes/GEANT4/build.sh 11.4.3` |
| HDF5 | `recipes/HDF5/build.sh 2.2.0` |
| CUPDATAMODEL | `recipes/CUPDATAMODEL/build.sh 1.4.1` |
| CUPDAQ | `recipes/CUPDAQ/build.sh 4.4` |

- The script runs in a clean environment, so a loaded vlist does not leak in.
- Build output goes to log files in `--workdir` (default `/tmp/prod-build-<name>-V<version>`). The screen shows one
  progress line a minute, for example `build.sh: compiling 45% at 12:31 (23 min)`.
- At the end it checks the result. Some products also use files from `recipes/<NAME>/check/`: HDF5 and
  CUPDATAMODEL build a small program against the new installation the way the DAQ uses them; CUPDAQ starts every
  program and reads a sample configuration (no hardware needed).
- When the checks pass, it saves the recipe next to the version, with `built` (which file, where, when) and
  `buildinfo` (what `recipes/<NAME>/buildinfo.sh` prints for the new version: features, versions, RPATHs; a record
  for people, not used for checks):

  ```
  build.sh: /home/cupsoft/Products/CUPDAQ/V4.3.2.recipe.yml
  build.sh:   built: /home/cupsoft/build-recipes/CUPDAQ/V4.3.2.yml (not in a git checkout) on cupdev0 at 2026-11-23T10:12:00+0900
  build.sh: CUPDAQ V4.3.2 is ready (3.3M).
  ```

- `build.sh` loads the modulefiles of the recipe's `depends` and nothing else, so the build sees exactly what users
  of the vlist get. Released dependencies use their fixed modulefiles.
- A dependency that is **built but not released yet** (for example CUPDATAMODEL, built just before CUPDAQ) gets a
  modulefile made from the template and its saved recipe, the same one `prod release` would write. Release both in
  the same vlist. Build them in dependency order: CUPDATAMODEL, then CUPDAQ.

**A version without a build script** (for example a vendor binary such as CAENHVWRAPPER): unpack it at the final
path, then save its recipe yourself:

```bash
recipes/_common/save-recipe.py recipes/CAENHVWRAPPER/V6.7.yml
```

**You can build on another node** with the same OS (AlmaLinux 9) and the same path (`/home/cupsoft/Products`). Then
copy the directory **and its saved recipe** to the server (`build.sh` prints the command), and change neither side
afterwards:

```bash
rsync -a /home/cupsoft/Products/ROOT/V6.40.04_3.14.9 /home/cupsoft/Products/ROOT/V6.40.04_3.14.9.recipe.yml \
      cupsoft@192.168.0.150:/scratch/cupsoft/Products/ROOT/
```

- Use the server's **real path**. On cupdev0, `/home/cupsoft/Products` is a symlink to `/scratch/cupsoft/Products`,
  and the AlmaLinux 9 rsync (3.2.7-1.el9_8) refuses a destination that goes through a symlink ("Too many levels of
  symbolic links"). Find the real path with `ssh cupsoft@192.168.0.150 readlink -f /home/cupsoft/Products`.

### 5.3 Write a draft

```bash
mkdir -p /home/cupsoft/Products/vlist/drafts      # once
```

```yaml
# /home/cupsoft/Products/vlist/drafts/4.1.yml
description: "PYTHON 3.14 + ROOT 6.40.06 + HDF5 2.2 + Geant4 11.4"
products:                          # list order = load order: dependencies first
  - {name: "PYTHON", version: "V3.14.9"}
  - {name: "ROOT", version: "V6.40.06_3.14.9"}
  - {name: "HDF5", version: "V2.2.0"}
  - {name: "GEANT4", version: "V11.4.3"}
```

- A version in a draft may stand for several builds: `V1.4` means `V1.4` or any `V1.4.<x>`. `prod release` takes the
  one whose recipe's `depends` are all earlier in the draft (with ROOT V6.32.20 in the list, the V1.4.x built with
  ROOT V6.32.20), and the released vlist names it (`V1.4.1`). If none or more than one fits, it stops and lists them.
- The file name is the vlist name. Use only `[A-Za-z0-9._-]`. **A name is never used twice**, including withdrawn
  names. (Only a removed release that no node had synced frees its name, 7.2.)
- Convention: `[<purpose>_]<major>.<minor>` (`4.1`, `daq_3.0`). Bump the major when the Python or ROOT generation
  changes.

### 5.4 Check, then release

```bash
prod release 4.1 --check     # writes nothing; hashes new versions (tens of seconds for big ones)
prod release 4.1
```

Example output:

```
release 4.1: 4 products
  PYTHON           V3.14.9                  frozen, verified                   module PYTHON/V3.14.9
  ROOT             V6.40.06_3.14.9          staging -> frozen, 8660 entries    module ROOT/V6.40.06_3.14.9 (new)
      note: recipe ROOT/V6.40.06_3.14.9.recipe.yml (recipes/ROOT/V6.40.06_3.14.9.yml at commit … on cupdev0 at …)
  HDF5             V2.2.0                   frozen, verified                   module HDF5/V2.2.0
  GEANT4           V11.4.3                  frozen, verified                   module GEANT4/V11.4.3
vlist hash sha256:…
```

What `prod release` does:

0. Draft versions such as `V1.4` become the build that fits (5.3); it prints `CUPDATAMODEL V1.4 -> V1.4.1`.
1. Frozen versions: checks that they still match their manifest.
2. Staging versions: checks them (absolute or broken symlinks, special files, ...) and computes the manifest.
3. Makes the fixed modulefiles (keeps existing ones) and checks the dependencies within the vlist. The recipe is
   the one saved next to the version (`note: recipe …` shows it); a version without one uses the tool's recipe.
4. Locks the version directories and their saved recipes, writes manifests, modulefiles and the vlist modulefile,
   and **writes the vlist last**. The draft is deleted.

It reports all problems at once. Fix them and run again. Progress goes to stderr, the summary to stdout;
`prod -q release …` turns off both.

**Look at `warning: … suspicious file …` before you release.** It lists editor backups (`*~`, `*.orig`), archives
and core dumps. A release freezes them too. Files that a package installs on purpose are fine; for example, Python's
`lib/python3.14/test/*.zip` and JupyterLab's `package.json.orig`.

### 5.5 Tell the nodes

Node admins run `prod sync 4.1` on each node ([client manual 5](manual-client.md#5-install-and-update-vlists)).
The server cannot install on clients.

### 5.6 Modulefile template for a new product

Each product needs `modulefiles/<NAME>/template.tcl` in the repository. `prod release` fills it in for each
version. Example (HDF5):

```tcl
#%Module
@HEADER@
module-whatis "HDF5 @VERSION@"
conflict HDF5
@DEPENDS@
setenv       HDF5SYS           @PREFIX@
prepend-path PATH              @PREFIX@/bin
prepend-path LD_LIBRARY_PATH   @PREFIX@/lib
prepend-path CMAKE_PREFIX_PATH @PREFIX@
```

Rules (`prod release` checks them):

- Starts with `#%Module`.
- Placeholders: `@NAME@`, `@VERSION@`, `@PREFIX@` (absolute path of the version directory), `@DEPENDS@`,
  `@GENERATED@`, `@HEADER@`. `@HEADER@` and `@DEPENDS@` appear exactly once.
- Has a `conflict <NAME>` line, so only one version of a product is loaded.
- Has **exactly one** `setenv <VAR> @PREFIX@` line. This is the product's home variable (`ROOTSYS`, `PYTHON_HOME`,
  ...); other products use it to check dependencies.
- Prints nothing, and adds no system paths (`/usr/lib64`, ...) and no macOS variables.
- Per-user values use `$::env(HOME)`.

If lines differ per version, add an executable `modulefiles/<NAME>/generate` and put `@GENERATED@` in the template
once. `generate` gets the version directory and prints Tcl lines; it runs once, at release. To capture what a
product's own setup script (like `thisroot.sh`) does to the environment, use `modulefiles/_common/sh2mod.py` (see
ROOT's and GEANT4's `generate`). The result is fixed as plain lines, so the script never runs at load time.

`recipes/<NAME>/buildinfo.sh` gets the version directory and prints `key<TAB>value` lines. `build.sh` saves them in
`<VERSION>.recipe.yml` as `buildinfo`, a record (not used for checks). If it fails, the recipe is saved without it.

## 6. Fix a released modulefile (revision)

Released modulefiles never change. Fix the template (`template.tcl` or `generate`) in the repository, upgrade the
tool, and release **a new vlist** with a revision:

```bash
prod release 4.0.1 --module-rev ROOT/V6.40.04_3.14.9
#   ROOT ... module ROOT/V6.40.04_3.14.9-m2 (new)
```

- Binaries and manifests stay the same. Only the new vlist uses `ROOT/V6.40.04_3.14.9-m2`.
- The old vlist (`4.0`) keeps its old modulefile.
- Without `--module-rev`, a release uses the highest revision of each version.

## 7. Withdraw or remove a bad release

### 7.1 Withdraw

A release is never edited. Withdraw it and release a fixed one under a new name:

```bash
prod withdraw 4.1 --reason "Geant4 data sets incomplete" --replaced-by 4.1.1
```

- `--replaced-by` must name an already released vlist. Leave it out if there is none.
- This writes `vlist/4.1.withdrawn`. The vlist itself stays.
- A new `prod sync 4.1` on a client refuses ("use 4.1.1 instead"); `--force` installs it anyway.
- Where it is already installed, `setup_prod.sh 4.1` and `prod verify` show a warning.
- The server keeps the withdrawn files (it does not know who still uses them).

### 7.2 Remove (only if no node has synced it)

A release that no node has synced yet (for example one you found wrong while testing it on the server) can be
removed, and its name used again:

```bash
prod remove 4.1              # only shows what would be deleted
prod remove 4.1 --yes        # deletes vlist/4.1.yml, vlist/4.1.withdrawn and modulefiles/vlist/4.1
prod gc                      # shows the versions and modulefiles that no vlist uses any more
prod gc --yes                # deletes them
```

- `prod remove` deletes only the vlist, never a version. `prod gc` then deletes the released versions (directory,
  manifest, saved recipe) and fixed modulefiles that no remaining vlist uses. Versions that another
  vlist uses, staging versions and files that `prod` does not manage stay.
- To keep the versions for a later vlist (to skip rebuilding them), don't run `prod gc`. They stay frozen and
  `prod verify` keeps checking them.
- **If a node has synced it, withdraw it instead (7.1).** A node that synced the old release stops with "the
  server's vlist differs" if the name is released again; on that node, run `prod remove 4.1` and `prod gc --yes`.
- Refused if another vlist was withdrawn in favour of this one (`--replaced-by`): remove both, or keep it.
- Each removal is recorded in `history.jsonl`.

## 8. Checks

```bash
prod verify              # every released vlist, and frozen versions in no vlist, against their manifests
prod verify 4.0 --quick  # type, exec bit and size only (fast)
prod verify --fix-perms  # re-lock anything that became writable (never fixes content)
prod list                # vlists, versions and who uses them, unmanaged files
prod doctor              # tool, system packages, module load, ldd
```

If `prod verify` reports errors (`content differs`, ...), **released bytes on the server have changed**. Find out
why. Clients stop with "the server's files differ from the manifest" when they try to get such a version.

History: `Products/.prod/state/history.jsonl` (one line per release, withdraw, verify, doctor, install, ...).

## 9. Don'ts

- **Don't change or delete released version directories, manifests, modulefiles or vlists by hand.** The lock
  (`a-w`) only prevents accidents; `chmod` can undo it. If you change them, every client's sync and verify fails.
  To delete a release, use `prod remove` and `prod gc` (7.2).
- **Don't reuse the name of a vlist that a node has synced.** Fix mistakes with a new name and a withdrawal.
- Don't edit a saved recipe (`<VERSION>.recipe.yml`) by hand. Fix the recipe where you build and run `build.sh`
  (or `save-recipe.py`) again.
- Don't run `prod sync` on the server (it refuses: role is server).
- Don't run `prod` or `install.sh` as root (they refuse). Root is only for `postinstall.sh`.

## 10. Troubleshooting

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | A check found problems (release refused, verify failed, ...) |
| 2 | Usage error |
| 3 | Environment error (no configuration, wrong role, cannot reach the server) |
| 4 | Another `prod` is running |

### Common messages

| Message | What to do |
|---|---|
| `no draft .../vlist/drafts/4.1.yml; write it first` | Write the draft (5.3) |
| `vlist '4.1' is already released; names are never reused` | Pick another name. To fix a release: withdraw + new name (or remove it if no node has synced it, 7.2) |
| `ROOT V… needs PYTHON V…, which is not in this vlist` | Add the product from the recipe's `depends` to the vlist |
| `… which must come before it in the list` | Move the dependency up in the list |
| `ROOT V… is frozen but differs from its manifest: …` | A released version changed on the server. Find out who and why |
| `no recipe: neither …/ROOT/V….recipe.yml nor …/recipes/ROOT/V….yml exists` | Build it with `build.sh`, or run `save-recipe.py` (5.2) |
| `no modulefile template …/template.tcl` | New product: add a template (5.6) |
| `--module-rev …: there is no fixed modulefile to revise yet` | The version was never released. Release without `--module-rev` |
| `another prod is running on this node; try again later` | Wait for the other `prod` to finish |
| `'sync' runs only where role is 'client' (this node: 'server')` | Don't sync on the server |
| `vlist '4.0' was withdrawn in favour of '4.1'; remove '4.0' too, or keep '4.1'` | `prod remove 4.0 4.1 --yes`, or keep 4.1 (7.2) |
| `…/PRODUCTS is missing (installed by an older install.sh); run install.sh again` | Run `./install.sh --force` from the current tag |
