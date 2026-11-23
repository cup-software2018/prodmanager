#!/usr/bin/python3 -I
"""End-to-end sandbox test.

    /usr/bin/python3 -I tests/sandbox/run.py [--keep] [--no-ssh]

Everything happens under a new temporary directory; the real Products, server, home and GitHub are
never touched. The sandbox contains:

  repo/              a copy of this repository plus fake modulefile sources and recipes, made into a
                     throw-away git repository with release tags (so install.sh names versions by git)
  server/            a fake products server (role server) with legacy-style products, legacy .vlist files
                     and legacy setup scripts, as on cupdev0 today
  clientA/           a client that reads the server through a local path
  clientB/           a client that reads the server through `localhost:` (real ssh + rsync)
  clientC/           a fresh client used to see what a new node would get
  legacy/            a node that keeps using the legacy install_products.sh and setup_prod.sh

Each node has its own HOME and Products. Steps run in order and stop at the first failure; with --keep
(or on failure) the sandbox is left for inspection.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GIT_ID = ["-c", "user.name=prod sandbox", "-c", "user.email=sandbox@localhost"]


class Failed(Exception):
    pass


class Sandbox:
    def __init__(self, root, use_ssh):
        self.root = root
        self.use_ssh = use_ssh
        self.repo = os.path.join(root, "repo")
        self.steps = 0

    # ------------------------------------------------------------------ helpers

    def node(self, name):
        return os.path.join(self.root, name, "Products"), os.path.join(self.root, name, "home")

    def write(self, path, text, mode=0o644):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(textwrap.dedent(text).lstrip("\n"))
        os.chmod(path, mode)

    def run(self, node, cmd, *, expect=0, contains=(), absent=(), shell=False, interactive=False):
        """Run a command as node (its HOME and ~/.local/bin on PATH) and check exit code and output."""
        _, home = self.node(node)
        os.makedirs(home, exist_ok=True)
        env = {"HOME": home, "PATH": f"{home}/.local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
               "USER": os.environ.get("USER", ""), "LOGNAME": os.environ.get("LOGNAME", "")}
        if "SSH_AUTH_SOCK" in os.environ:
            env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
        if shell:
            argv = ["bash", "--noprofile", "--norc"] + (["-i"] if interactive else []) + ["-c", cmd]
        else:
            argv = cmd
        out = subprocess.run(argv, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=home)
        text = out.stdout + out.stderr
        flat = " ".join(text.split())
        shown = cmd if shell else " ".join(cmd)
        if expect is not None and out.returncode != expect:
            raise Failed(f"[{node}] {shown}\n  exit {out.returncode}, expected {expect}\n{text}")
        for c in contains:
            if c not in text and c not in flat:
                raise Failed(f"[{node}] {shown}\n  output lacks {c!r}\n{text}")
        for a in absent:
            if a in text:
                raise Failed(f"[{node}] {shown}\n  output has {a!r}\n{text}")
        return text

    def prod(self, node, *args, **kw):
        return self.run(node, ["prod"] + list(args), **kw)

    def step(self, title):
        self.steps += 1
        print(f"[{self.steps:2d}] {title}", flush=True)

    # ------------------------------------------------------------------ setup

    def make_repo(self):
        os.makedirs(self.repo)
        for d in ("bin", "lib", "shell", "modulefiles", "recipes", "packages"):
            shutil.copytree(os.path.join(REPO, d), os.path.join(self.repo, d),
                            ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(os.path.join(REPO, "install.sh"), self.repo)
        m, r = os.path.join(self.repo, "modulefiles"), os.path.join(self.repo, "recipes")
        for name in ("PYTHON", "ROOT", "HDF5", "GEANT4"):  # replaced by fakes below
            shutil.rmtree(os.path.join(m, name), ignore_errors=True)
            shutil.rmtree(os.path.join(r, name), ignore_errors=True)
        for name, home, extra in (
            ("PYTHON", "PYTHON_HOME", "prepend-path PATH @PREFIX@/bin"),
            ("ROOT", "ROOTSYS", "prepend-path PATH @PREFIX@/bin\nprepend-path LD_LIBRARY_PATH @PREFIX@/lib\n"
                                "prepend-path PYTHONPATH @PREFIX@/lib"),
            ("HDF5", "HDF5SYS", "prepend-path PATH @PREFIX@/bin\nprepend-path LD_LIBRARY_PATH @PREFIX@/lib"),
            ("GEANT4", "G4BASE", "prepend-path PATH @PREFIX@/bin\nsetenv G4WORKDIR $::env(HOME)/geant4_workdir\n"
                                 "@GENERATED@"),
        ):
            self.write(f"{m}/{name}/template.tcl", f"""
                #%Module
                @HEADER@
                module-whatis "{name} @VERSION@ (sandbox)"
                conflict {name}
                @DEPENDS@
                setenv {home} @PREFIX@
                """ + extra.replace("\n", "\n                ") + "\n")
        # GEANT4 data directories differ by version: generate asks geant4-config, as the real one will.
        self.write(f"{m}/GEANT4/generate", """
            #!/bin/sh
            "$1/bin/geant4-config" --datasets | awk '{print "setenv " $2 " " $3}'
            """, 0o755)
        self.write(f"{r}/ROOT/buildinfo.sh", """
            #!/bin/sh
            printf 'root-config --version\\t%s\\n' "$("$1/bin/root-config" --version)"
            """, 0o755)
        recipes = [("PYTHON", "V3.11.14", []), ("ROOT", "V6.32.20_3.11.14", [("PYTHON", "V3.11.14")]),
                   ("HDF5", "V1.14.1", []), ("HDF5", "V1.14.2", []), ("GEANT4", "V11.1.3", [])]
        for name, ver, deps in recipes:
            dep = "".join(f'\n  - {{name: "{d}", version: "{v}"}}' for d, v in deps)
            self.write(f"{r}/{name}/{ver}.yml",
                       f'format: "1"\nproduct: "{name}"\nversion: "{ver}"\n' + (f"depends:{dep}\n" if deps else ""))
        git = ["git", "-C", self.repo] + GIT_ID
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "sandbox v0.1"], check=True)
        subprocess.run(git + ["tag", "v0.1"], check=True)

    def tag(self, name, change=None):
        if change:
            change()
        git = ["git", "-C", self.repo] + GIT_ID
        subprocess.run(git + ["commit", "-q", "-a", "-m", f"sandbox {name}"], check=True)
        subprocess.run(git + ["tag", name], check=True)

    def make_server(self):
        p, home = self.node("server")
        os.makedirs(home)
        # As on cupdev0 and cupdev1: Products is a symlink to a local disk (with a trailing //).
        disk = os.path.join(self.root, "server-disk", "Products")
        os.makedirs(disk)
        os.symlink(disk + "//", p)
        files = {
            "PYTHON/V3.11.14/bin/python3": ("#!/bin/sh\necho fake python 3.11.14\n", 0o755),
            "PYTHON/V3.11.14/lib/python3.11/os.py": ("# fake\n", 0o644),
            "ROOT/V6.32.20_3.11.14/bin/root": ('#!/bin/sh\necho "fake ROOT 6.32.20 ROOTSYS=$ROOTSYS"\n', 0o755),
            "ROOT/V6.32.20_3.11.14/bin/root-config": ("#!/bin/sh\necho 6.32.20\n", 0o755),
            "ROOT/V6.32.20_3.11.14/bin/thisroot.sh": ('ROOTSYS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; '
                                                      'export ROOTSYS; export PATH="$ROOTSYS/bin:$PATH"\n', 0o644),
            "ROOT/V6.32.20_3.11.14/lib/libCore.so": ("core\n", 0o644),
            "HDF5/V1.14.1/bin/h5dump": ("#!/bin/sh\necho h5dump 1.14.1\n", 0o755),
            "HDF5/V1.14.2/bin/h5dump": ("#!/bin/sh\necho h5dump 1.14.2\n", 0o755),
            "GEANT4/V11.1.3/bin/geant4-config": (
                '#!/bin/sh\nd="$(cd "$(dirname "$0")/.." && pwd)/share/Geant4/data"\n'
                'echo "G4EMLOW G4LEDATA $d/G4EMLOW8.2"\necho "PhotonEvaporation G4LEVELGAMMADATA $d/PhotonEvaporation5.7"\n',
                0o755),
            "GEANT4/V11.1.3/share/Geant4/data/G4EMLOW8.2/data.txt": ("em\n", 0o644),
            "GEANT4/V11.1.3/share/Geant4/data/PhotonEvaporation5.7/data.txt": ("pe\n", 0o644),
            # legacy system, as on cupdev0 today
            "vlist/3.0.vlist": ("#\n# Products \n#\nPYTHON\t\tV3.11.14\nROOT\t\tV6.32.20_3.11.14\n"
                                "HDF5\t\tV1.14.1\n#GEANT4          V11.1.3\n", 0o644),
            "PYTHON/setup_PYTHON.sh": ('export PYTHON_HOME="${PRODUCTS_DIR}/PYTHON/$1"; '
                                       'export PATH="${PYTHON_HOME}/bin:$PATH"\n', 0o644),
            "ROOT/setup_ROOT.sh": ('. "${PRODUCTS_DIR}/ROOT/$1/bin/thisroot.sh"\n', 0o644),
            "HDF5/setup_HDF5.sh": ('export HDF5SYS="${PRODUCTS_DIR}/HDF5/$1"; export PATH="$HDF5SYS/bin:$PATH"\n', 0o644),
            "CERNLIB.tgz": ("not managed\n", 0o644),
        }
        for rel, (text, mode) in files.items():
            path = os.path.join(p, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
            os.chmod(path, mode)
        # Legacy setup_prod.sh (simplified copy of the real one). The real one hard-codes
        # /home/cupsoft/Products; here each node's Products is next to its home.
        self.write(os.path.join(home, "setup_prod.sh"), """
            # legacy setup_prod.sh (simplified copy of the real one)
            export PRODUCTS_DIR="$(cd "$HOME/../Products" && pwd)"
            VLIST_FILE=$PRODUCTS_DIR/vlist/$1.vlist
            while read line; do
                if [ -z "$line" ] || [[ "$line" == \\#* ]]; then continue; fi
                read -ra prod <<< "$line"
                . "$PRODUCTS_DIR/${prod[0]}/setup_${prod[0]}.sh" "${prod[1]}"
            done < "$VLIST_FILE"
            """)

    def legacy_install(self, node, relnum):
        """What the legacy install_products.sh does on a node, against the sandbox server."""
        sp, shome = self.node("server")
        lp, lhome = self.node(node)
        os.makedirs(os.path.join(lp, "vlist"), exist_ok=True)
        cmd = f"""set -e
            cp -L {shome}/setup_prod* {lhome}/                            # copy "/home/cupsoft/setup_prod*"
            cp -L {sp}/vlist/{relnum}.vlist {lp}/vlist/                   # copy ".../vlist/RELNUM.vlist"
            grep -v '^#' {lp}/vlist/{relnum}.vlist | while read name version; do
                [ -n "$name" ] || continue
                mkdir -p {lp}/$name
                cp -L {sp}/$name/setup_$name* {lp}/$name/                 # copy ".../setup_NAME*"
                if [ ! -d {lp}/$name/$version ]; then                     # tarcopy only if missing
                    tar -C {sp} -cf - $name/$version | tar -C {lp} -xf -
                fi
            done"""
        return self.run(node, cmd, shell=True)

    def install(self, node, *args):
        p, home = self.node(node)
        os.makedirs(p, exist_ok=True)
        os.makedirs(home, exist_ok=True)
        # each node's Products is next to its home, not ~/Products: link HOME as a real node would
        return self.run(node, [os.path.join(self.repo, "install.sh"), "--products", p, "--link-home"] + list(args))

    def server_file(self, rel):
        return os.path.join(self.node("server")[0], rel)

    def unlock(self, path):
        for d, _, _ in os.walk(os.path.dirname(path)):
            os.chmod(d, os.lstat(d).st_mode | 0o200)
        os.chmod(os.path.dirname(path), 0o755)
        os.chmod(path, 0o644)

    # ------------------------------------------------------------------ scenario

    def scenario(self):
        sp, shome = self.node("server")

        self.step("prepare: repository copy tagged v0.1, fake server with legacy products")
        self.make_repo()
        self.make_server()

        self.step("server: install.sh --keep-legacy-setup (git version name; legacy ~/setup_prod.sh untouched)")
        with open(os.path.join(shome, "setup_prod.sh"), "rb") as f:
            legacy_setup = f.read()
        self.install("server", "--role", "server")  # keeping the legacy setup is the default on a server
        self.prod("server", "--version", contains=["prod v0.1"])
        with open(os.path.join(shome, "setup_prod.sh"), "rb") as f:
            if os.path.islink(os.path.join(shome, "setup_prod.sh")) or f.read() != legacy_setup:
                raise Failed("install.sh changed the legacy ~/setup_prod.sh on the server")
        if sorted(n for n in os.listdir(shome) if n.startswith("setup_prod")) != ["setup_prod.sh"]:
            raise Failed("install.sh added files that the legacy install_products.sh would copy (setup_prod*)")

        self.step("server: migrate legacy 3.0 (check first), then release 3.1 from a draft")
        self.prod("server", "release", "--migrate", "3.0", "--check", contains=["check only", "staging -> frozen"])
        self.prod("server", "release", "--migrate", "3.0", contains=["vlist hash sha256:"])
        self.write(os.path.join(sp, "vlist", "drafts", "3.1.yml"), """
            format: "1"
            description: "PYTHON 3.11 + ROOT 6.32 + HDF5 1.14.2 + Geant4 11.1 (sandbox)"
            products:
              - {name: "PYTHON", version: "V3.11.14"}
              - {name: "ROOT", version: "V6.32.20_3.11.14"}
              - {name: "HDF5", version: "V1.14.2"}
              - {name: "GEANT4", version: "V11.1.3"}
            """)
        self.prod("server", "release", "3.1", contains=["frozen, verified", "GEANT4"])
        # Products is a symlink: modulefiles must carry the symlink path, never the disk behind it
        disk = os.path.realpath(sp)
        for rel in ("modulefiles/ROOT/V6.32.20_3.11.14", "modulefiles/vlist/3.1"):
            with open(os.path.join(sp, rel)) as f:
                text = f.read()
            if disk in text or (rel.startswith("modulefiles/ROOT") and sp not in text):
                raise Failed(f"{rel} does not use the Products symlink path {sp}:\n{text}")
        self.prod("server", "doctor", "--quick", expect=None,  # packages depend on this node
                  contains=["ok    tool"], absent=["does not point", disk])
        self.prod("server", "verify", contains=["3.0", "3.1"], absent=["error"])
        self.prod("server", "list", contains=["CERNLIB.tgz", "vlist/3.0.vlist", "ROOT/setup_ROOT.sh"])

        self.step("legacy: on the migrated (locked) server the legacy way still works, on the server and on a "
                  "node installed by install_products.sh")
        self.run("server", ". ~/setup_prod.sh 3.0 && root && h5dump", shell=True,
                 contains=["fake ROOT 6.32.20", "h5dump 1.14.1"])
        self.run("server", f". {sp}/.prod/current/share/setup_prod.sh 3.1 && h5dump", shell=True,
                 contains=["h5dump 1.14.2"])  # the new way on the server, by full path
        self.legacy_install("legacy", "3.0")
        _, lhome = self.node("legacy")
        with open(os.path.join(lhome, "setup_prod.sh"), "rb") as f:
            if f.read() != legacy_setup:
                raise Failed("the legacy node received a setup_prod.sh that is not the legacy one")
        self.run("legacy", ". ~/setup_prod.sh 3.0 && root && h5dump && echo PYTHON_HOME=$PYTHON_HOME", shell=True,
                 contains=["fake ROOT 6.32.20", "h5dump 1.14.1", "/legacy/Products/PYTHON/V3.11.14"])

        clients = [("clientA", sp)]
        if self.use_ssh:
            clients.append(("clientB", f"localhost:{sp}"))
        for node, spec in clients:
            self.step(f"{node}: install (server {spec}), sync 3.0 and 3.1, verify, list")
            self.install(node, "--role", "client", "--server", spec)
            self.prod(node, "sync", "3.0", "3.1", contains=["first sync", "transferred"])
            self.prod(node, "verify", contains=["3.0", "3.1"], absent=["error"])
            self.prod(node, "list", contains=["PYTHON", "3.0, 3.1"])
        if len(clients) > 1:
            a = self.prod("clientA", "verify")
            b = self.prod("clientB", "verify")
            if [l.split()[-1] for l in a.splitlines()] != [l.split()[-1] for l in b.splitlines()]:
                raise Failed(f"clientA and clientB report different vlist hashes:\n{a}\n{b}")

        user = clients[-1][0]
        self.step(f"{user}: a user script with set -eu sources ~/setup_prod.sh, switches vlists")
        self.run(user, """set -eu
            . ~/setup_prod.sh 3.1
            root; h5dump; echo "G4LEDATA=$G4LEDATA"; echo "G4WORKDIR=$G4WORKDIR"
            . ~/setup_prod.sh 3.0
            h5dump; echo "G4BASE=${G4BASE:-unset}"
            """, shell=True, contains=["fake ROOT 6.32.20", "h5dump 1.14.2", "G4EMLOW8.2", "geant4_workdir",
                                       "h5dump 1.14.1", "G4BASE=unset"], absent=["Loading", "prod:"])
        self.run(user, ". ~/setup_prod.sh 3.1", shell=True, interactive=True,
                 contains=["prod: vlist 3.1: PYTHON/V3.11.14 ROOT/V6.32.20_3.11.14 HDF5/V1.14.2 GEANT4/V11.1.3"])

        self.step("clientA: damage a file, verify fails, sync --installed repairs it")
        cp, _ = self.node("clientA")
        f = os.path.join(cp, "ROOT", "V6.32.20_3.11.14", "lib", "libCore.so")
        self.unlock(f)
        with open(f, "w") as fh:
            fh.write("CORE\n")
        self.prod("clientA", "verify", expect=1, contains=["content differs"])
        self.prod("clientA", "sync", "--installed", contains=["transferred"])
        self.prod("clientA", "verify", absent=["error"])

        self.step("server: a frozen file changes on the server; server verify and a new client catch it")
        sf = self.server_file("ROOT/V6.32.20_3.11.14/lib/libCore.so")
        self.unlock(sf)
        with open(sf, "w") as fh:
            fh.write("CORE\n")
        self.prod("server", "verify", expect=1, contains=["content differs"])
        self.install("clientC", "--role", "client", "--server", sp)
        self.prod("clientC", "sync", "3.1", expect=1, contains=["even after a full re-transfer"])
        with open(sf, "w") as fh:
            fh.write("core\n")
        self.prod("server", "verify", "--fix-perms")
        self.prod("server", "verify", absent=["error", "warning"])
        self.prod("clientC", "sync", "3.1", contains=["transferred"])

        self.step("server: a released vlist is edited; clients refuse it, and continue once it is restored")
        vf = self.server_file("vlist/3.1.yml")
        with open(vf) as fh:
            original = fh.read()
        os.chmod(vf, 0o644)
        with open(vf, "a") as fh:
            fh.write("# edited\n")
        self.prod("clientA", "sync", "3.1", expect=1, contains=["differs from the one first synced"])
        self.prod("clientA", "verify", "--remote", "3.1", expect=1, contains=["server's vlist differs"])
        with open(vf, "w") as fh:
            fh.write(original)
        os.chmod(vf, 0o444)
        self.prod("clientA", "sync", "3.1", contains=["in place"])

        self.step("server: withdraw 3.0 in favour of 3.1; clients are told, --force still installs")
        self.prod("server", "withdraw", "3.0", "--reason", "HDF5 1.14.1 is too old", "--replaced-by", "3.1")
        self.prod("clientC", "sync", "3.0", expect=1, contains=["withdrawn", "use 3.1 instead"])
        self.prod("clientC", "sync", "--force", "3.0", contains=["WITHDRAWN"])
        self.prod("clientC", "verify", "3.0", contains=["withdrawn"])
        self.run("clientC", ". ~/setup_prod.sh 3.0", shell=True, interactive=True, contains=["was withdrawn"])

        self.step("clientA: remove 3.0 and gc: only HDF5 V1.14.1 goes; unmanaged files stay")
        with open(os.path.join(cp, "ROOT", "setup_ROOT.sh"), "w") as fh:
            fh.write("# legacy file\n")
        self.prod("clientA", "remove", "3.0")
        self.prod("clientA", "gc", contains=["HDF5/V1.14.1", "would be deleted"], absent=["ROOT/setup_ROOT.sh"])
        self.prod("clientA", "gc", "--yes", contains=["deleted"])
        if os.path.exists(os.path.join(cp, "HDF5", "V1.14.1")) or not os.path.exists(os.path.join(cp, "ROOT", "setup_ROOT.sh")):
            raise Failed("gc deleted the wrong things")
        self.prod("clientA", "verify", absent=["error"])

        self.step("clientA: a second prod does not wait: exit code 4 while the lock is held")
        lock = os.path.join(cp, ".prod", "state", "lock")
        self.run("clientA", f"flock -x {lock} prod sync 3.1", shell=True, expect=4, contains=["another prod"])

        self.step("server: a vlist in a newer format makes clients ask for a newer tool (exit 5)")
        self.write(self.server_file("vlist/9.0.yml"), 'format: "2"\ndescription: "from the future"\n')
        self.prod("clientA", "sync", "9.0", expect=5, contains=["update the tool"])
        os.unlink(self.server_file("vlist/9.0.yml"))

        self.step("server: a new version released with its saved recipe, without a tool upgrade; clients get it")
        self.write(self.server_file("HDF5/V1.14.3/bin/h5dump"), "#!/bin/sh\necho h5dump 1.14.3\n", 0o755)
        recipe = os.path.join(self.repo, "recipes", "HDF5", "V1.14.3.yml")  # in the clone only, not in tool v0.1
        self.write(recipe, 'format: "1"\nproduct: "HDF5"\nversion: "V1.14.3"\n')
        self.write(os.path.join(sp, "vlist", "drafts", "3.2.yml"), """
            format: "1"
            description: "HDF5 1.14.3"
            products:
              - {name: "HDF5", version: "V1.14.3"}
            """)
        self.prod("server", "release", "3.2", "--check", expect=1, contains=["no recipe: neither"])
        self.run("server", [os.path.join(self.repo, "recipes", "_common", "save-recipe.py"), recipe, "--products", sp],
                 contains=["V1.14.3.recipe.yml", "with uncommitted changes in recipes/HDF5/"])
        self.prod("server", "release", "3.2", contains=["recipe HDF5/V1.14.3.recipe.yml (recipes/HDF5/V1.14.3.yml at "
                                                       "commit", "vlist hash sha256:"])
        self.run("server", [os.path.join(self.repo, "recipes", "_common", "save-recipe.py"), recipe, "--products", sp],
                 expect=1, contains=["already released"])
        with open(self.server_file("modulefiles/HDF5/V1.14.3")) as fh:
            if "# recipe: HDF5/V1.14.3.recipe.yml (recipes/HDF5/V1.14.3.yml at commit" not in fh.read():
                raise Failed("the modulefile header does not name the saved recipe")
        self.prod("clientA", "sync", "3.2")
        if not os.path.exists(os.path.join(cp, "HDF5", "V1.14.3.recipe.yml")):
            raise Failed("sync did not bring the saved recipe")
        self.run("clientA", "set -eu; . ~/setup_prod.sh 3.2; h5dump", shell=True, contains=["h5dump 1.14.3"])
        self.prod("clientA", "verify", "3.2", absent=["error"])

        self.step("tool upgrade v0.2 (module revision for ROOT), then roll the client back to v0.1")
        tpl = os.path.join(self.repo, "modulefiles", "ROOT", "template.tcl")
        self.tag("v0.2", lambda: open(tpl, "a").write("setenv ROOT_SANDBOX_FIX 1\n"))
        self.install("server")
        self.prod("server", "--version", contains=["prod v0.2"])
        self.write(os.path.join(sp, "vlist", "drafts", "3.1.1.yml"), """
            format: "1"
            description: "3.1 with the fixed ROOT modulefile"
            products:
              - {name: "PYTHON", version: "V3.11.14"}
              - {name: "ROOT", version: "V6.32.20_3.11.14"}
            """)
        self.prod("server", "release", "3.1.1", "--module-rev", "ROOT/V6.32.20_3.11.14",
                  contains=["ROOT/V6.32.20_3.11.14-m2 (new)"])
        conf = open(os.path.join(cp, ".prod", "etc", "prod.conf")).read()
        self.install("clientA")
        self.prod("clientA", "--version", contains=["prod v0.2"])
        if open(os.path.join(cp, ".prod", "etc", "prod.conf")).read() != conf:
            raise Failed("install.sh changed prod.conf")
        self.prod("clientA", "sync", "3.1.1")
        self.run("clientA", 'set -eu; . ~/setup_prod.sh 3.1.1; echo "fix=$ROOT_SANDBOX_FIX"', shell=True,
                 contains=["fix=1"])
        self.run("clientA", 'set -eu; . ~/setup_prod.sh 3.1; echo "fix=${ROOT_SANDBOX_FIX:-none}"', shell=True,
                 contains=["fix=none"])
        cur = os.path.join(cp, ".prod", "current")
        os.symlink("versions/v0.1", cur + ".tmp")
        os.replace(cur + ".tmp", cur)
        self.prod("clientA", "--version", contains=["prod v0.1"])
        self.prod("clientA", "verify", absent=["error"])
        with open(os.path.join(shome, "setup_prod.sh"), "rb") as f:
            if os.path.islink(os.path.join(shome, "setup_prod.sh")) or f.read() != legacy_setup:
                raise Failed("upgrading the tool on the server replaced the legacy ~/setup_prod.sh")


def cleanup(root):
    for d, _, _ in os.walk(root):
        try:
            os.chmod(d, os.lstat(d).st_mode | 0o700)
        except OSError:
            pass
    shutil.rmtree(root, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="store_true", help="keep the sandbox directory")
    ap.add_argument("--no-ssh", action="store_true", help="skip the client that uses localhost: over ssh")
    ns = ap.parse_args()
    use_ssh = not ns.no_ssh
    if use_ssh:
        probe = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "localhost", "true"],
                               capture_output=True, stdin=subprocess.DEVNULL)
        if probe.returncode != 0:
            print("note: ssh to localhost without a password is not possible; skipping the ssh client")
            use_ssh = False
    root = tempfile.mkdtemp(prefix="prod-sandbox-")
    print(f"sandbox: {root}")
    started = time.monotonic()
    sb = Sandbox(root, use_ssh)
    try:
        sb.scenario()
    except (Failed, subprocess.CalledProcessError) as e:
        print(f"\nFAILED at step {sb.steps}:\n{e}\n\nsandbox kept for inspection: {root}")
        return 1
    print(f"\nall {sb.steps} steps passed in {time.monotonic() - started:.1f} s"
          + ("" if use_ssh else " (without the ssh client)"))
    if ns.keep:
        print(f"sandbox kept: {root}")
    else:
        cleanup(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
