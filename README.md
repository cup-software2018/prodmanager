# prodmanager

`prod` is the tool that manages CUP's products (Python, ROOT, Geant4, HDF5, CUPDAQ, ...).
One products server builds and releases them. Every node installs **the same bytes at the same path**
(`/home/cupsoft/Products`).

- **In this repository:** the tool (`prod`, `setup_prod.sh`), modulefile templates, build recipes and system package
  lists.
- **Not in this repository:** the product binaries, the released vlists and the manifests. They live on the products
  server.

> **Status:** in use on the products server (cupdev0) and on cupdev1. The other nodes still use the old
> `install_products.sh` until they are moved ([client manual, section 9](docs/manual-client.md#9-moving-a-node-from-the-old-system)).

## Documentation

| Document | For |
|---|---|
| [docs/manual-client.md](docs/manual-client.md) | Users (set up the environment) and node admins (install, sync, check) |
| [docs/manual-server.md](docs/manual-server.md) | The products server admin (build, release, withdraw, check) |

## How it works

```
 GitHub prodmanager ── git checkout <tag>; ./install.sh ──▶ /home/cupsoft/Products/.prod/   (the tool, on every node)

 products server (cupdev0)                                client (every node)
 /home/cupsoft/Products/                                  /home/cupsoft/Products/
   ROOT/V6.40.04_3.14.9/ ...   ── prod sync <vlist> ──▶     same layout, only the vlists this node uses
   vlist/4.0.yml                  (rsync + sha256 check)
   modulefiles/                                             . ~/setup_prod.sh 4.0     ← users
```

- A **vlist** is a named set of product versions (`4.0`, `daq_2.1`). Once released it never changes: a name means
  the same bytes on every node.
- **Users** run `. ~/setup_prod.sh 4.0`, as before. Inside, environment-modules sets up the environment.
- **`prod verify`** checks with sha256 that the installed files are exactly what was released.

## Quick start

**User**

```bash
. ~/setup_prod.sh 4.0                     # other accounts: . /home/cupsoft/setup_prod.sh 4.0
```

**Node admin (cupsoft)**

```bash
git clone https://github.com/cup-software2018/prodmanager.git && cd prodmanager && git checkout vX.Y
./install.sh --role client --server cupsoft@192.168.0.150:/home/cupsoft/Products
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh     # system packages, as root
prod sync 4.0 daq_2.1
prod verify
```

**Server admin:** see [docs/manual-server.md](docs/manual-server.md).

## Repository layout

| Path | Contents |
|---|---|
| `install.sh` | Installs the tool into `<Products>/.prod` (run as the Products owner) |
| `postinstall.sh` | Installs the system packages (run as root) |
| `bin/prod`, `lib/prod/` | The tool (system python3 3.9 + PyYAML) |
| `shell/` | `setup_prod.sh`, `setup_prod.csh` |
| `modulefiles/<NAME>/` | Modulefile template (`template.tcl`, optional `generate`); `_common/` has shared helpers |
| `recipes/<NAME>/` | Per version: `V<version>.yml` (dependencies, notes), and for products we build: `V<version>.options` (cmake) or `V<version>.requirements.txt` (pip). Per product: `build.sh`, `buildinfo.sh` |
| `recipes/_common/` | Helpers shared by the `build.sh` scripts |
| `recipes/<NAME>/check/` | Test programs that `build.sh` runs against a new installation (HDF5, CUPDATAMODEL, CUPDAQ) |
| `packages/` | System package lists, by group |
| `tests/` | Unit tests, sandbox test, real-data test |

## Tests

```bash
/usr/bin/python3 -I tests/run_unit.py                    # unit tests (seconds)
/usr/bin/python3 -I tests/sandbox/run.py                 # end to end: fake server and client, real install.sh,
                                                         # prod, rsync, ssh to localhost, environment-modules
/usr/bin/python3 -I tests/realdata/env_equivalence.py    # real products: old setup vs. new modulefiles
```

- The first two work only in temporary directories. They never touch the real Products, the server or `$HOME`.
  The sandbox needs password-less `ssh localhost` (skip it with `--no-ssh`). It keeps its directory when it fails
  (or with `--keep`).
- The third only reads. For each old vlist on this node it compares the environment of the old
  `~/setup_prod.sh <vlist>` with the new modulefiles, and reports differences that are not intended.

## Development

- People commit, tag and push. Nodes install tags (`vX.Y`) only.
- Everything is written in English: code, comments, messages and documentation.
