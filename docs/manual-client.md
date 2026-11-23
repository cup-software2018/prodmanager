# Client manual

For the nodes that use products. Sections 1–3 are for **users** (who only set up their environment). Sections 4–10
are for **node admins** (who prepare the node and install products, as cupsoft). For the server, see the
[server manual](manual-server.md).

## Contents

**Users**

1. [Set up the environment](#1-set-up-the-environment)
2. [In scripts](#2-in-scripts)
3. [Using module directly](#3-using-module-directly), [Adding Python packages (pip)](#31-adding-python-packages-pip)

**Node admins**

4. [Prepare a new node](#4-prepare-a-new-node)
5. [Install and update vlists](#5-install-and-update-vlists)
6. [Check and repair](#6-check-and-repair)
7. [Remove vlists and clean up](#7-remove-vlists-and-clean-up)
8. [Upgrade and roll back the tool](#8-upgrade-and-roll-back-the-tool)
9. [Moving a node from the old system](#9-moving-a-node-from-the-old-system)
10. [Troubleshooting](#10-troubleshooting)

---

# Users

## 1. Set up the environment

Same as before:

```bash
. ~/setup_prod.sh 4.0                    # as cupsoft
. /home/cupsoft/setup_prod.sh 4.0        # other accounts (e.g. amore2)
source /home/cupsoft/setup_prod.csh 4.0  # csh/tcsh
```

- With no argument it lists the vlists installed on this node.
- Sourcing another vlist **cleanly replaces** the previous one. Sourcing the same one twice adds nothing twice to
  `PATH`.
- In an interactive shell it prints one summary line:

  ```
  prod: vlist 4.0: PYTHON/V3.14.9 ROOT/V6.40.04_3.14.9 HDF5/V2.2.0 GEANT4/V11.4.3
  ```

- A withdrawn vlist still loads, with a warning and the vlist to use instead:

  ```
  prod: warning: vlist 3.0 was withdrawn: "HDF5 1.14.1 is too old"
  prod: warning: use "3.1" instead
  ```

- If the vlist is not on this node you get `prod: vlist '4.1' is not installed on this node; run 'prod sync 4.1'`.
  Ask the node admin.
- `PRODUCTS_DIR` (= `/home/cupsoft/Products`) is set, as before.

What is new: environment-modules sets up the environment, so `module list` shows what is loaded. Duplicate paths
and paths left over from a previous vlist are gone.

## 2. In scripts

```bash
#!/bin/bash
set -eu
. /home/cupsoft/setup_prod.sh 4.0        # stops here if it fails (set -e)
root -b -q analysis.C
```

- In a non-interactive shell it prints nothing except errors and warnings.
- Safe with `set -e` and `set -u`. Returns non-zero on failure.
- Works where the `module` command is not set up (cron, commands run over ssh, ...).

## 3. Using module directly

```bash
module use /home/cupsoft/Products/modulefiles
module avail                             # vlist/4.0, ROOT/V6.40.04_3.14.9, ...
module load vlist/4.0                    # same as setup_prod.sh 4.0
module load ROOT/V6.40.04_3.14.9         # one product (loads the PYTHON it needs too)
module switch vlist vlist/daq_2.1
module unload ROOT                       # also unloads the PYTHON it loaded
```

Only one version of a product can be loaded. Loading ROOT while another PYTHON is loaded fails with "needs PYTHON
V3.14.9, but another PYTHON is loaded".

## 3.1 Adding Python packages (pip)

The products' PYTHON is read-only, so you cannot install into it. Install into your home instead:

```bash
. ~/setup_prod.sh 4.0
pip install iminuit                      # goes to ~/.local/lib/python3.14/ automatically
```

pip says "Defaulting to user installation because normal site-packages is not writeable" and installs into **your
home** (same as `pip install --user`). Only you see it; products don't change.

For a separate set of packages per project, use a venv. It still sees the products' packages (numpy, ROOT's Python
bindings, ...):

```bash
. ~/setup_prod.sh 4.0
python3 -m venv --system-site-packages ~/venvs/myana
. ~/venvs/myana/bin/activate
pip install iminuit
```

**Packages that everyone or the DAQ needs:** don't install them per node. Ask the server admin; they go into a new
PYTHON version and a new vlist.

---

# Node admins

Everything is done as **cupsoft**, except the root steps (4.1 and 4.4). Run `prod` on each node itself; there is no
way to install on many nodes at once.

## 4. Prepare a new node

### 4.1 As root

1. Create the `cupsoft` account.
2. Create `/home/cupsoft/Products`. To put it on a big local disk, use a symlink (only the path
   `/home/cupsoft/Products` matters):

   ```bash
   mkdir -p /scratch/cupsoft/Products && chown cupsoft: /scratch/cupsoft/Products
   ln -s /scratch/cupsoft/Products /home/cupsoft/Products
   ```

3. Install what the tool itself needs (the rest comes with `postinstall.sh` in 4.4):

   ```bash
   dnf install environment-modules rsync openssh-clients python3 python3-pyyaml git
   ```

### 4.2 Server access

The node must reach the server by ssh **without a password**:

```bash
ssh -o BatchMode=yes cupsoft@192.168.0.150 true && echo ok
```

If not, add cupsoft's ssh key to `~cupsoft/.ssh/authorized_keys` on the server.

### 4.3 Install the tool

```bash
git clone https://github.com/cup-software2018/prodmanager.git
cd prodmanager && git checkout vX.Y
./install.sh --role client --server cupsoft@192.168.0.150:/home/cupsoft/Products
```

- The tool goes to `/home/cupsoft/Products/.prod/`, and `~/setup_prod.sh`, `~/setup_prod.csh` and
  `~/.local/bin/prod` are linked to it. An old real `~/setup_prod.sh` is moved to `~/setup_prod.sh.pre-prod`.
- Settings are in `/home/cupsoft/Products/.prod/etc/prod.conf`. Change them only to change the server.
- It ends with `prod doctor --quick`.

**Test install.** You can install somewhere else without touching the real Products. When `--products` is not
`~/Products`, install.sh **leaves `$HOME` alone**, so the node's current setup does not change. Run the tool by its
path:

```bash
T=/scratch/cupsoft/prodtest && mkdir -p $T/Products
./install.sh --products $T/Products --role client --server cupsoft@192.168.0.150:/home/cupsoft/Products
$T/Products/.prod/current/bin/prod sync daq_2.1
. $T/Products/.prod/current/share/setup_prod.sh daq_2.1
```

The fixed modulefiles point to `/home/cupsoft/Products/...`, so the environment still uses the binaries there. A
test install is for trying sync and verify. Remove it with `chmod -R u+w $T && rm -rf $T`.

### 4.4 System packages (root)

```bash
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh --check   # only show what is missing
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh
```

Details are in [server manual 4](manual-server.md#4-system-packages). Exit code 1 if a required package failed;
`prod doctor` shows what is missing.

## 5. Install and update vlists

```bash
prod list --remote               # vlists on the server
prod sync 4.0 daq_2.1            # install (one or more)
prod sync 4.0 --dry-run          # only show what would be transferred
prod sync --installed            # bring every vlist on this node back to the server's state (repair)
```

Example output:

```
sync 4.0 sha256:… (first sync)
  PYTHON           V3.14.9                  transferred
  ROOT             V6.40.04_3.14.9          transferred
  HDF5             V2.2.0                   transferred
  GEANT4           V11.4.3                  in place
  done in 41.3 s
```

What `prod sync` does:

- On the first sync it **records the hash of the server's vlist**. Later it stops if the server's vlist differs
  (a released vlist must never change).
- Versions that are already right are left alone (`in place`). Only missing or different ones are transferred;
  unchanged files are reused, so repairs are small.
- It checks everything with sha256, locks it (`a-w`) and only then moves it into place. Running programs don't
  break.
- The vlist file is written last. A vlist that `setup_prod.sh` shows is complete.
- If it is interrupted (network, ...), run it again; it continues.

Progress goes to stderr, one line per version (`transferring … entries, 2.6 GB ...` → `verifying ...` →
`transferred and verified (2.6 GB, 1m12s)`); in a terminal, rsync's progress is shown too. The summary goes to
stdout. To log both: `prod sync 4.0 2>&1 | tee sync.log`. `prod -q sync …` prints only errors.

## 6. Check and repair

```bash
prod verify                      # every vlist on this node against its manifests (no server access)
prod verify 4.0 --quick          # fast: no content hashes
prod verify --remote             # also check that the server's vlists are unchanged since the first sync
prod verify --fix-perms          # re-lock anything that became writable
prod doctor                      # tool, server access, system packages, module load, missing shared libraries (ldd)
prod list                        # vlists, versions and who uses them, unmanaged files
```

`prod doctor` checks that every program and library in the vlists finds its shared libraries. It does not count
(it only shows "not counted: …"): libraries a pip wheel ships next to itself, Qt plugins and QML modules (loaded only
when used; platform plugins are counted), and files in a recipe's `ldd_ignore`. On FAIL, the missing library is
usually a system package: run `postinstall.sh` again (4.4).

Compare the hashes (`sha256:…`) in `prod verify` output between nodes to see that they use the same release.
`prod --json verify` (also `list` and `doctor`) prints JSON for scripts.

### A broken installation

```
4.0          1 problem(s)   sha256:…
  error:   ROOT V6.40.04_3.14.9: lib/libCore.so: content differs
```

```bash
prod sync --installed            # restore from the server
prod verify
```

Installed files are locked; don't edit them. To experiment, copy them somewhere else.

### "The server's vlist differs"

```
prod: the server's vlist '4.0' differs from the one first synced on this node
```

A released vlist must never change. **Tell the server admin.** Only if they confirm the change was intended:

```bash
prod sync --retrust 4.0          # record the new hash and continue (logged in history)
```

### "The server's files differ from the manifest"

```
prod: ROOT V…: the server's files differ from the manifest even after a full re-transfer (…);
      the products server needs to be checked
```

Released files changed on the server. Nothing changed on this node. Tell the server admin.

### A withdrawn vlist

```
prod: vlist '3.0' was withdrawn: HDF5 1.14.1 is too old; use 3.1 instead (--force installs it anyway)
```

Use the replacement. If you really need it: `prod sync --force 3.0`.

## 7. Remove vlists and clean up

```bash
prod remove 4.0                  # remove the vlist from this node (its versions stay for now)
prod gc                          # list what is no longer used by any vlist
prod gc --yes                    # delete it
```

- `gc` deletes only what `prod` manages. It never deletes old-system files (`setup_ROOT.sh`, `CERNLIB.tgz`, ...).
- If you sync a removed vlist again, it is compared with the hash from its first sync.

## 8. Upgrade and roll back the tool

In your clone of the repository:

```bash
git fetch --tags && git checkout vX.Y
./install.sh                     # prod.conf and history stay
prod --version
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh --check   # as root
```

- **If the new tag changed `packages/`, run `postinstall.sh` (4.4).** `--check` shows whether anything is missing.
- Installed vlists don't change: their modulefiles were fixed at release.
- If `prod` says "format N is newer than this tool supports", upgrade the tool.

Roll back:

```bash
ls /home/cupsoft/Products/.prod/versions/          # kept versions (newest 3)
ln -sfn versions/vX.Y /home/cupsoft/Products/.prod/current.tmp   # vX.Y: the version to go back to
mv -T /home/cupsoft/Products/.prod/current.tmp /home/cupsoft/Products/.prod/current
```

## 9. Moving a node from the old system

For nodes that installed products with `install_products.sh`:

1. **Check first** which vlists and scripts this node uses. If someone edited an old vlist file on the node itself
   (for example `3.0` on cupdev1), the server's version replaces it, so fix the scripts that use it first.
2. Install the tool (4.3) and the system packages (4.4).
3. `prod sync <the vlists you use>`. Existing version directories are reused; only differences from the server are
   fixed (files Python created, like `__pycache__`, are removed).
4. Run `prod verify`, `prod doctor`, and your real scripts once.

To go back: `mv ~/setup_prod.sh.pre-prod ~/setup_prod.sh`. Old files (`vlist/*.vlist`, `setup_<NAME>.sh`) were not
deleted, so the old system still works.

On a moved node, **don't run `install_products.sh` again.** It copies the server's old `~/setup_prod.sh` over the
new one. To keep using the old system on a node, install with `--keep-legacy-setup` (keeps the old
`~/setup_prod.sh`) and use the new one as `. /home/cupsoft/Products/.prod/current/share/setup_prod.sh <vlist>`.

## 10. Troubleshooting

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | A check found problems (verify failed, hash mismatch, withdrawn, doctor FAIL, ...) |
| 2 | Usage error |
| 3 | Environment error (no configuration, wrong role, cannot reach the server) |
| 4 | Another `prod` is running → try again when it ends |
| 5 | A file format this tool does not know → upgrade the tool |

### Common messages

| Message | What to do |
|---|---|
| `prod: vlist '4.1' is not installed on this node; run 'prod sync 4.1'` | `prod sync 4.1` |
| `vlist '4.1' is not on the products server` | Check the name (`prod list --remote`) |
| `cannot read from the products server …` | Check ssh access (4.2) |
| `another prod is running on this node; try again later` | Wait for the other `prod` to finish |
| `the products server is inconsistent for vlist …` | A server problem. Tell the server admin |
| `no configuration file …/prod.conf; run install.sh` | The tool is not installed (4.3) |
| `…/PRODUCTS is missing (installed by an older install.sh); run install.sh again` | Run `./install.sh --force` from the current tag |
| `'release' runs only where role is 'server' (this node: 'client')` | Releases are made on the server |
| `prod: environment-modules is not installed …` | `dnf install environment-modules` (root) |
| `prod: local vlists (~/.prod-local) are not supported yet` | Local vlists are not implemented yet |

### Not done yet

- Per-user local vlists (`~/.prod-local`).
- `setup_prod.csh` has not been tested on a node with csh.
