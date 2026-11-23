# Products server manual

For the person who runs the products server. The server is **cupdev0** (`cupsoft@192.168.0.150`). Run everything
as **cupsoft** unless a step says root. For nodes, see the [client manual](manual-client.md).

## Contents

1. [What the server does](#1-what-the-server-does)
2. [Directory layout](#2-directory-layout)
3. [Install, upgrade and roll back the tool](#3-install-upgrade-and-roll-back-the-tool)
4. [System packages](#4-system-packages)
5. [Release a new vlist](#5-release-a-new-vlist)
6. [Register an old vlist (once)](#6-register-an-old-vlist-once)
7. [Fix a released modulefile (revision)](#7-fix-a-released-modulefile-revision)
8. [Withdraw a bad release](#8-withdraw-a-bad-release)
9. [Checks](#9-checks)
10. [Don'ts](#10-donts)
11. [Troubleshooting](#11-troubleshooting)

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
│   ├── current -> versions/v1.0        the tool in use
│   ├── versions/v1.0/                  tool versions (read-only)
│   ├── etc/prod.conf                   settings (role: server)
│   └── state/                          history.jsonl, packages.yml, lock
├── vlist/
│   ├── drafts/4.1.yml                  a draft before release (you write it)
│   ├── 4.0.yml                         a released vlist (locked)
│   ├── 3.1.vlist                       old format; kept for nodes on the old system
│   └── 3.0.withdrawn                   withdrawal record (only if withdrawn)
├── modulefiles/
│   ├── ROOT/V6.40.04_3.14.9            fixed modulefile (locked)
│   └── vlist/4.0                       modulefile that loads the whole vlist (locked)
└── ROOT/
    ├── V6.40.04_3.14.9/                binaries (locked after release)
    ├── V6.40.04_3.14.9.manifest
    ├── V6.40.04_3.14.9.buildinfo       build record, for people to read
    ├── V6.40.04_3.14.9.recipe.yml      the recipe it was built with (saved by build.sh)
    └── setup_ROOT.sh                   old system; kept for now
```

Files that `prod` does not manage (`CERNLIB.tgz`, unreleased versions, ...) are listed by `prod list` under "not
managed by prod". `prod` never deletes them. A directory with no version directories (`V…`) in it, such as the pyenv
checkout `PYENV/`, is shown as one line: `PYENV/  (whole directory, no versions in it)`.

## 3. Install, upgrade and roll back the tool

### 3.1 First install

```bash
git clone https://github.com/cup-software2018/prodmanager.git ~/prodtemp/prodmanager   # any place
cd ~/prodtemp/prodmanager
git checkout vX.Y                                  # install tags only
./install.sh --role server
git checkout main                                  # back to main, for building (5)
```

`install.sh`:

- copies the tool to `/home/cupsoft/Products/.prod/versions/vX.Y/` and points `current` to it;
- creates `.prod/etc/prod.conf` only if it does not exist (`role: server`);
- links `~/.local/bin/prod`;
- on the server, **leaves the old `~/setup_prod.sh` alone and creates no `~/setup_prod.csh`** (see
  [3.4](#34-living-with-the-old-system));
- ends with `prod doctor --quick`. Problems there do not fail the install.

If a package the tool needs is missing (`environment-modules`, `rsync`, `openssh-clients`, `python3`,
`python3-pyyaml`, `git`), `install.sh` stops and tells you what to install.

### 3.2 Upgrade

```bash
cd ~/prodtemp/prodmanager && git fetch --tags && git checkout vX.Y
./install.sh
git checkout main                                  # back to main, for building (5)
prod --version
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh --check   # as root; see section 4
```

- `prod.conf` and `state/` stay. The newest 3 tool versions are kept (`--keep N`).
- **If the new tag changed `packages/`, run `postinstall.sh` (section 4).** `--check` shows whether anything is
  missing. For example, v1.0 added `xerces-c`, `unixODBC`, `pcsc-lite-libs`, `libtiff-devel` and `libcurl-devel`.
- New modulefile templates are used from the next release on. Released vlists do not change.
- The installed tool does not use the clone, so the clone can stay on `main` between upgrades.

### 3.3 Roll back

```bash
ln -sfn versions/v0.4 /home/cupsoft/Products/.prod/current.tmp
mv -T /home/cupsoft/Products/.prod/current.tmp /home/cupsoft/Products/.prod/current
prod --version
```

Or check out the older tag and run `./install.sh --force`.

### 3.4 Living with the old system

Some nodes still use the old system: `install_products.sh` to install, and the server's `~/setup_prod.sh <vlist>`,
which it copies. Until all nodes are moved, the server serves both. `prod` never deletes or changes old files:

| Old file | What `prod` does |
|---|---|
| Server's `~/setup_prod.sh` | Leaves it alone (install.sh creates no `setup_prod*` files on the server) |
| `vlist/<name>.vlist` | Leaves it alone; writes `<name>.yml` next to it |
| `<NAME>/setup_<NAME>.sh` | Leaves it alone |
| `<NAME>/<VERSION>/` | Same content, but **locked** once released. Old tar copies still work |

To use the new setup on the server meanwhile:

```bash
. /home/cupsoft/Products/.prod/current/share/setup_prod.sh 4.0
```

Released versions are read-only. For example, **nobody can `pip install` into a released PYTHON**. Users install
into their home or a venv ([client manual 3.1](manual-client.md#31-adding-python-packages-pip)). A package everyone
needs goes into a new PYTHON version and a new vlist.

When no node uses the old system any more, replace the server's old `~/setup_prod.sh` (it is kept as
`~/setup_prod.sh.pre-prod`):

```bash
cd ~/prodtemp/prodmanager && ./install.sh --replace-legacy-setup
```

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

Everything happens on the server, in the repository clone (`~/prodtemp/prodmanager`):

```bash
cd ~/prodtemp/prodmanager && git checkout main && git pull   # the newest recipes (no tag, no install)
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

Every version needs a **recipe**, `recipes/<NAME>/V<version>.yml`, in the repository:

```yaml
# recipes/ROOT/V6.40.04_3.14.9.yml
format: "1"
product: "ROOT"
version: "V6.40.04_3.14.9"
depends:
  - {name: "PYTHON", version: "V3.14.9"}      # must be loaded with this version
source: {url: "https://root.cern/download/root_v6.40.04.source.tar.gz"}
build: |
  recipes/ROOT/build.sh 6.40.04 3.14.9
notes: "free text"
```

- The tool checks only `format`, `product`, `version`, `depends` and `ldd_ignore`. `source`, `build` and `notes`
  are a record. (`built` is added by `build.sh`; don't write it yourself.)
- Products that we build also need their options: `V<version>.options` (cmake), or for PYTHON
  `V<version>.requirements.txt` (pip packages).
- **Our own packages from GitHub** (CUPDATAMODEL, CUPDAQ): `source` names the repository and the exact commit:

  ```yaml
  depends:
    - {name: "ROOT", version: "V6.40.04_3.14.9"}
    - {name: "HDF5", version: "V2.2.0"}
  source:
    url: "https://github.com/cup-software2018/CUPDataModel.git"
    branch: "v1.3"                                         # for people; branches move
    commit: "93fcf9c8493843af3f1b5572bc3151b16a7f33cb"   # build.sh checks out exactly this
  ```

- `ldd_ignore` lists optional files (globs in the version directory) that `prod doctor` should not count when they
  miss a shared library, with the reason:

  ```yaml
  ldd_ignore:
    - path: "lib/python3.14/site-packages/numba/np/ufunc/tbbpool.*.so"
      reason: "numba's optional TBB threading layer; EL9 has no libtbb.so.12"
  ```

  You don't need to list libraries that a pip wheel ships in the same directory, or Qt plugins and QML modules
  (except platform plugins). They are not counted anyway.
- A **new product** (a name that does not exist yet) also needs a modulefile template
  ([5.6](#56-modulefile-template-for-a-new-product)), and optionally `recipes/<NAME>/buildinfo.sh`. These are part of
  the tool: tag and upgrade the tool ([3.2](#32-upgrade)) before you release the product.
- Write the recipe where you like: in the server's clone, or elsewhere and then `git pull` on the server. Commit and
  push it in the end, because the git history is the record. The saved copy names the commit it came from, and says
  "with uncommitted changes" if `recipes/<NAME>/` was not committed when you built.

### 5.2 Build (staging)

Products are not relocatable, so **build at the final path**. Until the release you may change, delete or rebuild
the directory; clients do not see it.

For these products the repository has a build script. It gets the source and builds, tests, installs and checks
the product:

| Product | Command |
|---|---|
| PYTHON | `recipes/PYTHON/build.sh <version>` |
| ROOT | `recipes/ROOT/build.sh <ROOT version> <Python version>` |
| GEANT4 | `recipes/GEANT4/build.sh <version>` |
| HDF5 | `recipes/HDF5/build.sh <version>` |
| CUPDATAMODEL | `recipes/CUPDATAMODEL/build.sh <version>` |
| CUPDAQ | `recipes/CUPDAQ/build.sh <version>` |

- The script runs in a clean environment, so a loaded vlist does not leak in.
- Build output goes to log files in `--workdir` (default `/tmp/prod-build-<name>-<version>`). The screen shows one
  progress line a minute, for example `build.sh: compiling 45% at 12:31 (23 min)`.
- At the end it checks the result. Some products also use files from `recipes/<NAME>/check/`: HDF5 and
  CUPDATAMODEL build a small program against the new installation the way the DAQ uses them; CUPDAQ starts every
  program and reads a sample configuration (no hardware needed).
- When the checks pass, it saves the recipe next to the version:

  ```
  build.sh: /home/cupsoft/Products/CUPDAQ/V4.3.2.recipe.yml
  build.sh:   built: recipes/CUPDAQ/V4.3.2.yml at commit 4b615872706f on cupdev0 at 2026-11-23T10:12:00+0900
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
- Write the build node into the recipe's `notes`.

### 5.3 Write a draft

```bash
mkdir -p /home/cupsoft/Products/vlist/drafts      # once
```

```yaml
# /home/cupsoft/Products/vlist/drafts/4.1.yml
format: "1"
description: "PYTHON 3.14 + ROOT 6.40.06 + HDF5 2.2 + Geant4 11.4"
products:                          # list order = load order: dependencies first
  - {name: "PYTHON", version: "V3.14.9"}
  - {name: "ROOT", version: "V6.40.06_3.14.9"}
  - {name: "HDF5", version: "V2.2.0"}
  - {name: "GEANT4", version: "V11.4.3"}
```

- The file name is the vlist name. Use only `[A-Za-z0-9._-]`. **A name is never used twice**, including withdrawn
  names and old `.vlist` names.
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

`recipes/<NAME>/buildinfo.sh` gets the version directory and prints `key<TAB>value` lines. They are saved in
`<VERSION>.buildinfo` as a record (not used for checks).

## 6. Register an old vlist (once)

This registers an old `vlist/<name>.vlist` under **the same name**, without rebuilding anything.

- **Done:** `daq_2.1` and `mon_1.0`.
- **Still to do:** `3.0`, `3.1`, `daq_1.0`, `daq_2.0`.
- **Not registered:** `1.0` and `2.0` (they contain ANACONDA), and `0.1`, `0.9`, `1.1`, `2.1` (no versions).

```bash
prod release --migrate 3.1 --check      # check first
prod release --migrate 3.1
```

- The `NAME VERSION` lines of the old `.vlist` are copied as they are (comments dropped). You cannot change them.
- The old `.vlist` and `setup_<NAME>.sh` stay, so nodes on the old system keep working.
- Old version directories are locked here for the first time. **Read the suspicious file warnings.**
- A name can be registered only once.

## 7. Fix a released modulefile (revision)

Released modulefiles never change. Fix the template (`template.tcl` or `generate`) in the repository, upgrade the
tool, and release **a new vlist** with a revision:

```bash
prod release 4.0.1 --module-rev ROOT/V6.40.04_3.14.9
#   ROOT ... module ROOT/V6.40.04_3.14.9-m2 (new)
```

- Binaries and manifests stay the same. Only the new vlist uses `ROOT/V6.40.04_3.14.9-m2`.
- The old vlist (`4.0`) keeps its old modulefile.
- Without `--module-rev`, a release uses the highest revision of each version.

## 8. Withdraw a bad release

A release is never edited. Withdraw it and release a fixed one under a new name:

```bash
prod withdraw 4.1 --reason "Geant4 data sets incomplete" --replaced-by 4.1.1
```

- `--replaced-by` must name an already released vlist. Leave it out if there is none.
- This writes `vlist/4.1.withdrawn`. The vlist itself stays.
- A new `prod sync 4.1` on a client refuses ("use 4.1.1 instead"); `--force` installs it anyway.
- Where it is already installed, `setup_prod.sh 4.1` and `prod verify` show a warning.
- The server keeps the withdrawn files (it does not know who still uses them).

## 9. Checks

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

## 10. Don'ts

- **Don't change or delete released version directories, manifests, modulefiles or vlists.** The lock (`a-w`) only
  prevents accidents; `chmod` can undo it. If you change them, every client's sync and verify fails.
- **Don't reuse a released vlist name.** Fix mistakes with a new name and a withdrawal.
- Don't edit a saved recipe (`<VERSION>.recipe.yml`) by hand. Fix the recipe in the repository and run `build.sh`
  (or `save-recipe.py`) again.
- Don't run `prod sync` on the server (it refuses: role is server).
- Don't run `prod` or `install.sh` as root (they refuse). Root is only for `postinstall.sh`.

## 11. Troubleshooting

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | A check found problems (release refused, verify failed, ...) |
| 2 | Usage error |
| 3 | Environment error (no configuration, wrong role, cannot reach the server) |
| 4 | Another `prod` is running |
| 5 | A file format this tool does not know → upgrade the tool |

### Common messages

| Message | What to do |
|---|---|
| `no draft .../vlist/drafts/4.1.yml; write it first` | Write the draft (5.3) |
| `vlist '4.1' is already released; names are never reused` | Pick another name. To fix a release: withdraw + new name |
| `'3.0' is a legacy vlist name; use --migrate to register it` | It is an old `.vlist` name. Register it with `--migrate` |
| `ROOT V… needs PYTHON V…, which is not in this vlist` | Add the product from the recipe's `depends` to the vlist |
| `… which must come before it in the list` | Move the dependency up in the list |
| `ROOT V… is frozen but differs from its manifest: …` | A released version changed on the server. Find out who and why |
| `no recipe: neither …/ROOT/V….recipe.yml nor …/recipes/ROOT/V….yml exists` | Build it with `build.sh`, or run `save-recipe.py` (5.2) |
| `no modulefile template …/template.tcl` | New product: add a template (5.6) |
| `--module-rev …: there is no fixed modulefile to revise yet` | The version was never released. Release without `--module-rev` |
| `another prod is running on this node; try again later` | Wait for the other `prod` to finish |
| `… format 2 is newer than this tool supports (1); update the tool` | Upgrade the tool |
| `'sync' runs only where role is 'client' (this node: 'server')` | Don't sync on the server |
| `…/PRODUCTS is missing (installed by an older install.sh); run install.sh again` | Run `./install.sh --force` from the current tag |
