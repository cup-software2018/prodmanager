# Admin manual

This manual is for the people who install, build and release products. It covers the **products server** and
the **client nodes**. For users, see the [README](../README.md).

Do everything as **cupsoft**. Use root only where a step says so.

## Contents

1. [How it works](#1-how-it-works)
2. [Install the tool](#2-install-the-tool)
3. [System packages](#3-system-packages)
4. [Server: build a new version](#4-server-build-a-new-version)
5. [Server: make a release](#5-server-make-a-release)
6. [Server: a new product](#6-server-a-new-product)
7. [Client: install releases](#7-client-install-releases)
8. [Check and repair](#8-check-and-repair)
9. [Remove things](#9-remove-things)
10. [Rules](#10-rules)
11. [Problems](#11-problems)
12. [For developers](#12-for-developers)

---

## 1. How it works

```
 products server (cupdev0)                         client nodes
 /home/cupsoft/Products/                           /home/cupsoft/Products/
   build-recipes/   build.sh, recipes
   ROOT/V6.32.24_3.11.14/   ── prodman sync ──▶      the same files, at the same path
   vlist/1.0.yml                                     only the releases this node uses
   modulefiles/
```

- On the **server**, you build each product version at its final path, `/home/cupsoft/Products/<NAME>/<VERSION>`.
- You put versions together in a **release** with `prodman release`. After that, the release and its versions are
  read-only and **never change**.
- Each **client** copies the releases it needs with `prodman sync`. The server never sends anything by itself.

Words:

| Word | Meaning |
|---|---|
| product | One piece of software, like ROOT |
| version | One build of a product: `ROOT/V6.32.24_3.11.14` |
| release, vlist | A named set of versions: `vlist/1.0.yml` |
| draft | The file you write to make a release: `vlist/drafts/1.0.yml` |
| recipe | How to build a version: `build-recipes/ROOT/V6.32.24_3.11.14.yml` |
| manifest | The list of every file of a released version, with its sha256: `ROOT/V6.32.24_3.11.14.manifest` |
| modulefile | The file that sets up the environment for one version: `modulefiles/ROOT/V6.32.24_3.11.14` |
| not released | A version that is built but is in no release yet. You may still change or delete it |

The server is **cupdev0** (`cupsoft@192.168.0.150`).

### Directory layout

```
/home/cupsoft/Products/
├── .prod/                          the tool (do not touch)
│   ├── current -> versions/<v>
│   ├── etc/prod.conf               role (server or client) and, on a client, the server
│   └── state/history.jsonl         a log of every release, sync, remove, ...
├── build-recipes/                  server only: build.sh and recipes (yours to edit)
├── packages/                       system package lists (server: yours to edit; client: a copy)
├── vlist/
│   ├── drafts/1.0.yml              server only: drafts (yours to edit)
│   └── 1.0.yml                     a release (read-only)
├── modulefiles/                    read-only
│   ├── ROOT/V6.32.24_3.11.14
│   └── vlist/1.0                   loads the whole release
└── ROOT/
    ├── V6.32.24_3.11.14/           the version (read-only after release)
    ├── V6.32.24_3.11.14.manifest
    └── V6.32.24_3.11.14.recipe.yml the recipe it was built with, saved by build.sh
```

---

## 2. Install the tool

The tool is `prodman`. It is installed into `<Products>/.prod`. The same steps work on the server and on clients.

### 2.1 Before you start (root)

1. Create the `cupsoft` account.
2. Create `/home/cupsoft/Products`, owned by cupsoft. To put it on a big disk, use a link:

   ```bash
   mkdir -p /scratch/cupsoft/Products && chown cupsoft: /scratch/cupsoft/Products
   ln -s /scratch/cupsoft/Products /home/cupsoft/Products
   ```

3. Install what the tool needs:

   ```bash
   dnf install environment-modules rsync openssh-clients python3 python3-pyyaml git
   ```

4. **Client only:** cupsoft must reach the server by ssh **without a password**. Test it:

   ```bash
   ssh -o BatchMode=yes cupsoft@192.168.0.150 true && echo ok
   ```

   If it fails, add cupsoft's ssh key to `~cupsoft/.ssh/authorized_keys` on the server.

### 2.2 First install

```bash
git clone https://github.com/cup-software2018/prodmanager.git
cd prodmanager
./install.sh --role server                                                       # on the server
./install.sh --role client --server cupsoft@192.168.0.150:/home/cupsoft/Products # on a client
```

`install.sh`:

- copies the tool into `Products/.prod`;
- writes `.prod/etc/prod.conf` (only the first time);
- links `~/setup_prod.sh`, `~/setup_prod.csh` and `~/.local/bin/prodman` to the tool. If a real file is in the
  way, it is renamed to `<name>.bak`;
- **on the server only:** creates `build-recipes/`, `packages/` and `vlist/drafts/`.

Other options: `--products_dir DIR` (default `~/Products`), `--force` (install the same version again).

Keep the clone. You use it for upgrades.

### 2.3 Upgrade

```bash
cd prodmanager
git pull
./install.sh
prodman --version
```

- `prod.conf` and the history stay. Only the newest tool version is kept.
- Releases do not change. A changed modulefile template is used only for versions released for the first time
  after the upgrade.
- On the server, `install.sh` **replaces** the `build.sh` scripts in `Products/build-recipes`. It only **adds**
  recipes and package lists that are missing. It never changes the ones you have.

---

## 3. System packages

The products need system packages (compilers, libraries, ...). The lists are in `Products/packages/*.yml`, one
file per group: `base`, `python`, `root`, `geant4`, `daq`, `caen`.

- On the **server**, you edit them there.
- A **client** gets a copy from the server with every `prodman sync`. So on a new client, run `prodman sync` once
  (section 7) before `postinstall.sh`.

Install them as **root**:

```bash
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh --check     # only show what is missing
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh             # install
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh --group daq # one group only
```

- It turns on the crb and epel repositories. It never runs `dnf update`.
- If one package fails, it tries the others one by one, and shows only the ones that failed.
- Packages under `optional` only give a warning.
- Log: `/var/log/prodmanager-postinstall.log`. Exit code 1 if a needed package is missing.

`prodman release`, `prodman sync` and `prodman verify` also warn when a needed package is missing.

---

## 4. Server: build a new version

Builds happen in `/home/cupsoft/Products/build-recipes`. Each product has a folder there, with:

- `build.sh`: builds, tests and installs a version (it comes from this repository);
- `V<version>.yml`: one **recipe** for each version (you write these);
- `buildinfo.sh`: writes a short build record (it comes from this repository).

### 4.1 Write the recipe

```yaml
# build-recipes/ROOT/V6.32.24_3.11.14.yml
product: "ROOT"
version: "V6.32.24_3.11.14"
depends:
  - {name: "PYTHON", version: "V3.11.14"}
source:
  url: "https://github.com/root-project/root.git"
  tag: "v6-32-24"
options:
  - "-DCMAKE_BUILD_TYPE=Release"
  - "-Dpyroot=ON"
```

| Field | What it is |
|---|---|
| `product`, `version` | Must match the file name and folder |
| `depends` | Products this version needs. `build.sh` loads them; the release must contain them |
| `source` | `url` and `tag`: a git tag. Only `url`: an archive to download |
| `options` | The cmake options (for PYTHON: the `./configure` options) |

- Comments (`#`) are fine. Other fields are not allowed.
- PYTHON also needs `V<version>.requirements.txt`: the pip packages to install.
- Recipes do not have to go into git. The copy that `build.sh` saves next to the version is the record.
- **The same source with different dependencies:** give each build its own version, for example
  `CUPDATAMODEL/V1.4.1.yml` (with ROOT 6.32) and `V1.4.2.yml` (with ROOT 6.40), both with `tag: "v1.4"`.
  Drafts can still say `V1.4` (5.1).

### 4.2 Build

The argument is the version **without the V**:

```bash
cd /home/cupsoft/Products/build-recipes
PYTHON/build.sh 3.11.14
ROOT/build.sh 6.32.24_3.11.14
HDF5/build.sh 1.14.6
GEANT4/build.sh 11.1.3
CUPDATAMODEL/build.sh 1.4
CUPDAQ/build.sh 4.4
CAENHVWRAPPER/build.sh 6.6
```

- `build.sh --help` shows its options. Most have `--workdir DIR` (where the source, build files and logs go;
  default `/tmp/prod-build-...`) and `--products_dir DIR`.
- It runs in a clean environment, so what you loaded in your shell does not matter. It loads only the products in
  the recipe's `depends`.
- Long steps print one line a minute: `build.sh: compiling 45% at 12:31 (23 min)`.
- At the end it checks the result (tests, a small test program, missing libraries).
- If all is fine, it saves the recipe next to the version, as `<NAME>/<VERSION>.recipe.yml`, and prints what to
  do next.
- Build dependencies first: PYTHON before ROOT, CUPDATAMODEL before CUPDAQ. A dependency does not have to be
  released yet; then release them together.
- To build again, first delete the old one: `prodman remove <NAME>/<VERSION> --yes` (9.2).

**CAENHVWRAPPER:** CAEN's download needs a login. Put the archive from CAEN next to the recipe, as
`build-recipes/CAENHVWRAPPER/CAENHVWrapper-<version>.tgz`, then run `build.sh`. It runs CAEN's own `install.sh`.

**A product with no build.sh:** put the files at the final path yourself, then save the recipe:

```bash
/home/cupsoft/Products/build-recipes/_common/save-recipe.py /home/cupsoft/Products/build-recipes/<NAME>/V<version>.yml
```

**Building on another node:** it must have the same OS (AlmaLinux 9) and the same path. Then copy the version
folder **and** its `.recipe.yml` to the server. `build.sh` prints the rsync command. Use the server's real path
(`/scratch/cupsoft/Products/...`): rsync on AlmaLinux 9 does not accept a target path that goes through a link.

---

## 5. Server: make a release

### 5.1 Write a draft

```yaml
# /home/cupsoft/Products/vlist/drafts/1.0.yml
description: "Python 3.11, ROOT 6.32, HDF5 1.14, Geant4 11.1"
products:
  - {name: "PYTHON", version: "V3.11.14"}
  - {name: "ROOT", version: "V6.32.24_3.11.14"}
  - {name: "HDF5", version: "V1.14.6"}
  - {name: "GEANT4", version: "V11.1.3"}
```

- **The order does not matter.** The release puts the dependencies first (from the recipes), and the products that
  nothing needs last.
- `description` is free text, for people.
- A version can be short: `V1.4` means `V1.4` or any `V1.4.<x>`. The release picks the build whose dependencies are
  in this release. If none or more than one fits, it stops and tells you.
- The file name is the release name. Use only letters, digits, `.`, `_` and `-`. Our names: `1.0`, `1.1`, ... for
  the base set, and `daq_1.0`, ... for the DAQ set.
- **Never use a name twice** (10).
- The draft stays after the release. Copy it to start the next one.

### 5.2 Check, then release

```bash
prodman release 1.0 --check      # checks everything, writes nothing
prodman release 1.0
```

Output:

```
release 1.0: 4 products
  PYTHON           V3.11.14                 frozen, verified                   module PYTHON/V3.11.14
  ROOT             V6.32.24_3.11.14         staging -> frozen, 7520 entries    module ROOT/V6.32.24_3.11.14 (new)
      note: recipe ROOT/V6.32.24_3.11.14.recipe.yml (...)
  HDF5             V1.14.6                  frozen, verified                   module HDF5/V1.14.6
  GEANT4           V11.1.3                  frozen, verified                   module GEANT4/V11.1.3
```

What `prodman release` does:

1. Puts the products in order, and picks the build for short versions like `V1.4`.
2. Checks that every dependency is in the release.
3. Versions released before: checks that they did not change (manifest).
4. New versions: checks the files and writes the manifest. Writes a modulefile for each new version.
5. Loads the whole release once, to be sure it works. If it does not load, nothing is released.
6. Makes everything read-only, and writes `vlist/1.0.yml` last.

It shows all problems at once. Fix them and run it again.

**Read the warnings.** For example:

- `suspicious file(s) such as editor backups, archives or core dumps`: these would be frozen too. Delete them
  first if they should not be there.
- `system package(s) not installed`: install them (section 3).
- missing shared libraries (ldd): look at them. Often an optional part needs a package that is not installed.

### 5.3 Tell the client admins

Each client admin runs `prodman sync 1.0` (section 7).

---

## 6. Server: a new product

A product name that does not exist yet (for example `FOO`) needs two things in **this repository**:

1. `modulefiles/FOO/template.tcl`: the modulefile template.
2. `build-recipes/FOO/build.sh` (and optional `buildinfo.sh`): copy one of the others and change it.

Commit them, then `git pull && ./install.sh` on the server (2.3). Then write the recipe and build (section 4).

Template example (HDF5):

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

Rules (the release checks them):

- It starts with `#%Module`.
- It has `@HEADER@` and `@DEPENDS@`, each once. `@PREFIX@` is the version folder; `@NAME@` and `@VERSION@` are also
  allowed.
- It has `conflict FOO`, so only one version is loaded.
- It has exactly one `setenv <VAR> @PREFIX@` line. Other products use this variable to check the version.
- It prints nothing, and does not add system paths like `/usr/lib64`.

If some lines must be computed for each version, add a program `modulefiles/FOO/generate` and put `@GENERATED@` in
the template. See ROOT and GEANT4: they use `modulefiles/_common/sh2mod.py` to turn `thisroot.sh` into modulefile
lines.

---

## 7. Client: install releases

```bash
prodman list --remote            # the releases on the server
prodman sync 1.0 daq_1.0         # install one or more releases
prodman sync 1.0 --dry-run       # only show what would be copied
prodman resync                   # sync every release on this node again (repair, or after a server change)
```

Output:

```
sync 1.0
  PYTHON           V3.11.14                 transferred
  ROOT             V6.32.24_3.11.14         transferred
  HDF5             V1.14.6                  in place
  GEANT4           V11.1.3                  transferred
  done in 41.3 s
```

What `prodman sync` does:

- It also copies the server's package lists (`packages/`), read-only.
- Versions that are already right stay (`in place`). Others are copied. Unchanged files are reused, so a repair
  is quick.
- It checks every file with sha256 and makes it read-only **before** it puts it in place. Programs that are
  running do not break.
- It writes the release file last. If `setup_prod.sh` shows a release, it is complete.
- If it stops (network, ...), run it again. It goes on where it stopped.
- At the end it loads the release once and warns about missing system packages or libraries.

Progress lines go to stderr, the summary to stdout. To keep a log: `prodman sync 1.0 2>&1 | tee sync.log`.

`prodman resync` takes only the releases already on this node. For a new release, use `prodman sync <name>`.

---

## 8. Check and repair

```bash
prodman verify                   # check every release on this node
prodman verify 1.0 --quick       # fast: no sha256, no library check
prodman verify --remote          # client: also compare with the server
prodman list                     # releases, versions, who uses them, versions not released
```

`prodman verify` checks:

- every file of every version against its manifest (sha256);
- that the modulefiles are there, and that the release loads;
- system packages and missing shared libraries (warnings);
- read-only files: a file that became writable is a warning, and verify makes it read-only again.

On the server it also checks released versions that no release uses any more.

**A file is wrong on a client:**

```
1.0          1 problem(s)
  error:   ROOT V6.32.24_3.11.14: lib/libCore.so: content differs
```

```bash
prodman resync                   # copy the right files from the server
prodman verify
```

**A file is wrong on the server:** someone changed a released version. Find out who and why. Clients stop with
"the server's files differ from the manifest" when they sync it.

Add `--json` to `verify` or `list` for output that scripts can read. Add `-q` to any command to see only errors.

---

## 9. Remove things

Without `--yes`, `prodman remove` and `prodman gc` only show what they would delete.

### 9.1 A release

```bash
prodman remove 1.0               # show what would be deleted
prodman remove 1.0 --yes         # delete the release (its versions stay)
prodman gc                       # show the versions and modulefiles no release uses
prodman gc --yes                 # delete them
```

- This works the same on the server and on clients.
- On the server, the draft stays.
- `gc` never deletes versions that are not released yet, or files that prodman does not manage.

### 9.2 A version

```bash
prodman remove ROOT/V6.38.06_3.14.9 --yes
```

- It deletes the version folder, its saved recipe, and its manifest and modulefile if it was released.
- If a release uses it, prodman refuses and names the release. Remove the release first, or in the same command:
  `prodman remove 1.0 PYTHON/V3.11.14 --yes`.

### 9.3 A bad release

A release never changes. If one is wrong:

1. Fix the problem and release it under a **new name** (for example `1.1`).
2. Tell the client admins to sync the new one.
3. Remove the bad one on the server and on the clients: `prodman remove 1.0 --yes`, then `prodman gc --yes`.

---

## 10. Rules

- **Never change a released version, manifest, modulefile or release by hand.** They are read-only only to stop
  accidents. If they change, `verify` and `sync` fail everywhere.
- **Never use a release name twice.** A client that has the old one would quietly get the new one with
  `prodman resync`.
- Do not edit a saved recipe (`<VERSION>.recipe.yml`). Fix the recipe in `build-recipes` and build again.
- Do not edit `build.sh` in `Products/build-recipes`: `install.sh` replaces it. Change it in this repository.
- Do not run `prodman` or `install.sh` as root (they refuse). Root is only for `postinstall.sh`.

---

## 11. Problems

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | A check found a problem (release refused, verify failed, ...) |
| 2 | Wrong command or options |
| 3 | Setup problem (not installed, wrong role, cannot reach the server) |
| 4 | Another prodman is running. Try again later |

### Messages

| Message | What to do |
|---|---|
| `no draft .../vlist/drafts/1.1.yml; write it first` | Write the draft (5.1) |
| `vlist '1.0' is already released` | Use a new name |
| `ROOT V... needs PYTHON V..., which is not in this vlist` | Add that product to the draft |
| `none of the built versions ... has all its depends in this vlist` | Add the dependency, or name the version exactly |
| `more than one built version fits this vlist: ...` | Name the version exactly (`V1.4.1`) |
| `the vlist does not load: ...` | Fix the modulefile template or the version |
| `ROOT V... is frozen but differs from its manifest` | A released version changed on the server. Find out why |
| `no recipe: .../ROOT/V....recipe.yml does not exist` | Build it with `build.sh`, or run `save-recipe.py` (4.2) |
| `no modulefile template .../template.tcl` | New product: add a template (6) |
| `... is used by vlist 1.0; remove the vlist first` | Remove the release first (9.2) |
| `vlist '1.1' is not on the products server` | Check the name: `prodman list --remote` |
| `the products server is missing files of vlist ...` | A problem on the server. Tell the server admin |
| `the server's files differ from the manifest even after a full re-transfer` | A problem on the server. Tell the server admin |
| `rsync failed ...` | Check ssh to the server (2.1) |
| `another prod is running on this node; try again later` | Wait for the other prodman to end |
| `'release' runs only where role is 'server'` | Releases are made on the server |
| `'sync' runs only where role is 'client'` | Do not sync on the server |
| `no configuration file .../prod.conf; run install.sh` | Install the tool (2.2) |
| `on a client, run 'prodman sync <vlist>' first` (postinstall.sh) | Sync once to get the package lists (3) |

---

## 12. For developers

| Path | What |
|---|---|
| `install.sh` | Installs the tool into `<Products>/.prod` |
| `postinstall.sh` | Installs the system packages (root) |
| `bin/prodman`, `lib/prod/` | The tool (system python3 and PyYAML) |
| `shell/` | `setup_prod.sh`, `setup_prod.csh` |
| `modulefiles/<NAME>/` | Modulefile templates (`template.tcl`, optional `generate`) |
| `build-recipes/<NAME>/` | `build.sh`, `buildinfo.sh`, `check/` test programs, example recipes |
| `build-recipes/_common/` | Shared build helpers and `save-recipe.py` |
| `packages/` | Default system package lists |
| `tests/` | Unit tests |

Run the unit tests:

```bash
/usr/bin/python3 -I tests/run_unit.py
```

They run in temporary folders and never touch the real Products, the server or `$HOME`.

Write everything in English: code, comments, messages and documents.
