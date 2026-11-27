# prodmanager

prodmanager gives every CUP node the same software: Python, ROOT, Geant4, HDF5, CUPDAQ and more. We call
them **products**. They are in `/home/cupsoft/Products` on every node.

This page is for **users**. If you install or release products, read the
[admin manual](docs/manual-admin.md).

## Words

| Word | Meaning |
|---|---|
| product | One piece of software, like ROOT |
| version | One build of a product, like `ROOT/V6.32.24_3.11.14` |
| release (vlist) | A named set of versions that work together, like `1.0` or `daq_1.0` |

A release never changes. `1.0` is the same on every node, today and next year.

## 1. Load a release

```bash
. ~/setup_prod.sh 1.0                      # as cupsoft
. /home/cupsoft/setup_prod.sh 1.0          # as another user
source /home/cupsoft/setup_prod.csh 1.0    # in csh or tcsh
```

This loads every product of the release. You see one line:

```
prod: vlist 1.0: PYTHON/V3.11.14 ROOT/V6.32.24_3.11.14 HDF5/V1.14.6 GEANT4/V11.1.3
```

- To change to another release, load it. The old one goes away.
- Loading the same release again changes nothing.
- `module list` shows what is loaded.

## 2. See what is here, or load one product

Run it without a release name:

```bash
. ~/setup_prod.sh
```

It loads nothing. It shows the releases on this node and how to use them:

```
releases on this node:
  1.0          PYTHON/V3.11.14 ROOT/V6.32.24_3.11.14 HDF5/V1.14.6 GEANT4/V11.1.3
  daq_1.0      PYTHON/V3.11.14 ROOT/V6.32.24_3.11.14 HDF5/V1.14.6 CUPDATAMODEL/V1.4 CUPDAQ/V4.4 CAENHVWRAPPER/V6.6
usage:
  . ~/setup_prod.sh <vlist>       load a release
  module load <NAME>/<VERSION>    load one product
  module avail                    list the products
```

After that you can use `module` to load single products:

```bash
module avail                               # list all products
module load ROOT/V6.32.24_3.11.14          # ROOT, and the PYTHON it needs
module unload ROOT                         # ROOT, and the PYTHON it loaded
```

You can load only one version of each product. If another PYTHON is already loaded, loading ROOT fails with
"needs PYTHON V3.11.14, but another PYTHON is loaded". Unload the other one first.

## 3. In scripts

```bash
#!/bin/bash
set -eu
. /home/cupsoft/setup_prod.sh 1.0          # the script stops here if this fails
root -b -q analysis.C
```

- In a script, it prints only errors and warnings.
- It works with `set -e` and `set -u`.
- It works where `module` is not set up, for example in cron or over ssh.

## 4. Add Python packages

The products are read-only, so you cannot install into the release's Python. `pip` installs into your home
instead:

```bash
. ~/setup_prod.sh 1.0
pip install iminuit                        # goes to ~/.local/lib/python3.11/
```

pip says "Defaulting to user installation because normal site-packages is not writeable". That is normal. Only
you see these packages.

To keep packages apart for each project, use a venv. It still sees the release's packages (numpy, ROOT, ...):

```bash
. ~/setup_prod.sh 1.0
python3 -m venv --system-site-packages ~/venvs/myana
. ~/venvs/myana/bin/activate
pip install iminuit
```

If everyone (or the DAQ) needs a package, ask the admin. It goes into a new release.

## 5. Problems

| You see | What to do |
|---|---|
| `prod: vlist '1.1' is not installed on this node; run 'prodman sync 1.1'` | Ask the node admin to install it |
| `prod: no releases on this node; ...` | Ask the node admin |
| `needs PYTHON V..., but another PYTHON is loaded` | `module unload PYTHON`, or load a whole release instead |
| `prod: environment-modules is not installed` | Ask the node admin |
