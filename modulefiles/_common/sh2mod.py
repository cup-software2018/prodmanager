"""Capture what a product's own setup script does, as explicit modulefile lines.

Used by the `generate` scripts of products whose setup script depends on the version or the build
(ROOT's thisroot.sh, Geant4's geant4make.sh). It runs once at release time, never at load time:

1. `modulecmd.tcl bash sh-to-mod bash <script>` in a clean environment (HOME set to a placeholder,
   PATH=/usr/bin:/bin), which reports the environment changes the script makes;
2. that report is evaluated by tclsh with stub commands, so quoting is handled by Tcl itself;
3. the changes are written back as `setenv` / `prepend-path` lines, with these rules:
   - variables in DROP (macOS/AIX/HP-UX ones) and those given by the caller are dropped;
   - path-list variables (PATH, LD_LIBRARY_PATH, ...) become `prepend-path`, even when the script set
     them (in a clean environment a prepend looks like a set);
   - the version directory reached through a symlink: Products is often a symlink to a bigger disk
     (/home/cupsoft/Products -> /scratch/cupsoft/Products), and scripts like ROOT 6.40's thisroot.sh resolve
     their own location physically. Such paths are written back under the prefix as given, which is the path
     every node has;
   - path entries outside the version directory (system directories such as /usr/lib64) are dropped,
     except entries under the HOME placeholder;
   - the HOME placeholder becomes `$::env(HOME)`, so per-user values are decided at load time;
   - if MANPATH gets an entry, a trailing empty element is appended too (`append-path MANPATH {}`):
     with only the product's directory in MANPATH, `man` would no longer find the system pages; an empty
     element makes it add its default search path there (scripts like thisroot.sh append the system
     directories themselves, which we drop);
   - shell functions and aliases are dropped;
   - a value containing a backslash stops the capture: sh-to-mod writes a `$` in a value as `\$` inside
     braces, which Modules then keeps literally, so such a value cannot be captured faithfully. Set that
     variable explicitly in template.tcl and drop it here instead.
   Every dropped item is listed in a comment, so the frozen modulefile shows what was left out.
"""

import os
import re
import subprocess

MODULECMD = "/usr/share/Modules/libexec/modulecmd.tcl"
HOME_PLACEHOLDER = "/prod-sh2mod-home"
DROP = {"DYLD_LIBRARY_PATH", "LIBPATH", "SHLIB_PATH"}
PATH_VARS = {"PATH", "LD_LIBRARY_PATH", "PYTHONPATH", "CMAKE_PREFIX_PATH", "MANPATH", "JUPYTER_PATH",
             "ROOT_INCLUDE_PATH", "CPATH", "LIBRARY_PATH", "PKG_CONFIG_PATH"}

# Stub commands: print one record per call; list values one per line after a header.
_TCL_STUBS = r"""
proc _out {kind var vals} { puts "@@ $kind $var [llength $vals]"; foreach v $vals { puts $v } }
proc _strip {args} {
    set a {}
    foreach x $args { if {![string match -* $x]} { lappend a $x } }
    return $a
}
proc setenv {var val} { _out setenv $var [list $val] }
proc unsetenv {var args} { _out unsetenv $var {} }
proc prepend-path {args} { set a [_strip {*}$args]; _out prepend [lindex $a 0] [lrange $a 1 end] }
proc append-path {args} { set a [_strip {*}$args]; _out append [lindex $a 0] [lrange $a 1 end] }
proc remove-path {args} { set a [_strip {*}$args]; _out remove [lindex $a 0] [lrange $a 1 end] }
proc set-function {name body} { _out skip-function $name {} }
proc set-alias {name body} { _out skip-alias $name {} }
proc complete {args} { _out skip-complete - {} }
proc uncomplete {args} { _out skip-complete - {} }
proc chdir {args} { _out skip-chdir - {} }
"""


def _tcl_word(value):
    """A Tcl word for value; the HOME placeholder becomes $::env(HOME), evaluated at load time."""
    esc = "".join({"\\": "\\\\", '"': '\\"', "$": "\\$", "[": "\\[", "]": "\\]"}.get(c, c) for c in value)
    esc = esc.replace(HOME_PLACEHOLDER, "$::env(HOME)")
    return f'"{esc}"'


def capture_records(script, args=(), modulecmd=MODULECMD):
    env = {"HOME": HOME_PLACEHOLDER, "PATH": "/usr/bin:/bin", "LANG": "C"}
    out = subprocess.run([modulecmd, "bash", "sh-to-mod", "bash", script] + list(args), env=env, cwd="/",
                         capture_output=True, text=True, check=False)
    report = out.stderr  # modulecmd writes the generated modulefile to stderr
    if out.returncode != 0 or not report.startswith("#%Module"):
        raise SystemExit(f"sh-to-mod of {script} failed ({out.returncode}): {report.strip()}")
    tcl = subprocess.run(["tclsh"], input=_TCL_STUBS + report, capture_output=True, text=True, check=False)
    if tcl.returncode != 0:
        raise SystemExit(f"cannot evaluate the sh-to-mod report of {script}: {tcl.stderr.strip()}")
    records, lines = [], tcl.stdout.split("\n")
    i = 0
    while i < len(lines):
        if lines[i].startswith("@@ "):
            _, kind, var, n = lines[i].split(" ", 3)
            n = int(n)
            records.append((kind, var, lines[i + 1:i + 1 + n]))
            i += 1 + n
        else:
            i += 1
    return records


def capture(prefix, script, args=(), drop=(), modulecmd=MODULECMD):
    """Modulefile lines for what script does to the environment (see the module docstring)."""
    prefix = prefix.rstrip("/")
    drop = DROP | set(drop)
    out, dropped = [], []
    real = os.path.realpath(prefix)
    real_re = re.compile(re.escape(real) + r"(?=/|:|\s|$)") if real != prefix else None

    def unresolve(v):
        return real_re.sub(lambda _: prefix, v) if real_re else v

    def keep_entry(v):
        return v == prefix or v.startswith(prefix + "/") or v.startswith(HOME_PLACEHOLDER)

    for kind, var, vals in capture_records(script, args, modulecmd):
        vals = [unresolve(v) for v in vals]
        if var not in drop and any("\\" in v for v in vals):
            raise SystemExit(f"{script}: the value of {var} contains a backslash, which sh-to-mod does not quote "
                             f"faithfully; set {var} explicitly in template.tcl and drop it from the capture")
        if kind.startswith("skip"):
            dropped.append(f"{kind[5:]} {var}")
            continue
        if var in drop:
            dropped.append(f"variable {var}")
            continue
        if kind == "unsetenv":
            out.append(f"unsetenv {var}")
            continue
        if kind == "remove":
            dropped.append(f"remove-path {var}")
            continue
        if var in PATH_VARS or kind in ("prepend", "append"):
            entries = [e for v in vals for e in v.split(":") if e]
            kept = [e for e in entries if keep_entry(e)]
            dropped += [f"{var} entry {e} (outside the version directory)" for e in entries if e not in kept]
            if kept:
                cmd = "append-path" if kind == "append" else "prepend-path"
                out.append(f"{cmd} {var} " + " ".join(_tcl_word(e) for e in kept))
                if var == "MANPATH":
                    out.append("# keep the system manual pages: a trailing empty element means man's default path")
                    out.append("append-path MANPATH {}")
            continue
        out.append(f"setenv {var} {_tcl_word(vals[0])}")
    head = [f"# captured from {os.path.relpath(script, prefix)} at release time (modulefiles/_common/sh2mod.py)"]
    head += [f"# dropped: {d}" for d in dropped]
    return "\n".join(head + out)
