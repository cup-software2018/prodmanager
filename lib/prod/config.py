"""Paths and configuration.

The Products directory is not configured: it is derived from where the tool is installed,
`<Products>/.prod/versions/<version>/`. The configuration file is `<Products>/.prod/etc/prod.conf`.
"""

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from .errors import EnvError, FormatError
from .yamlutil import check_format, check_text, load_mapping, quote

FORMAT = 1
ROLES = ("client", "server")
_KEYS = ("format", "role", "server", "ssh_options")
_REMOTE_RE = re.compile(r"^(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+:(/.*)$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]*$")


class Paths:
    """Well-known paths under a Products directory."""

    def __init__(self, products):
        self.products = os.path.abspath(products)
        self.prod = os.path.join(self.products, ".prod")
        self.current = os.path.join(self.prod, "current")
        self.versions = os.path.join(self.prod, "versions")
        self.conf = os.path.join(self.prod, "etc", "prod.conf")
        self.state = os.path.join(self.prod, "state")
        self.trusted = os.path.join(self.state, "trusted")
        self.history = os.path.join(self.state, "history.jsonl")
        self.lock = os.path.join(self.state, "lock")
        self.tmp = os.path.join(self.state, "tmp")
        self.packages_record = os.path.join(self.state, "packages.yml")
        self.vlist = os.path.join(self.products, "vlist")
        self.drafts = os.path.join(self.vlist, "drafts")
        self.modulefiles = os.path.join(self.products, "modulefiles")

    def vlist_file(self, name):
        return os.path.join(self.vlist, name + ".yml")

    def withdrawn_file(self, name):
        return os.path.join(self.vlist, name + ".withdrawn")

    def legacy_vlist_file(self, name):
        return os.path.join(self.vlist, name + ".vlist")

    def version_dir(self, product, version):
        return os.path.join(self.products, product, version)

    def manifest_file(self, product, version):
        return os.path.join(self.products, product, version + ".manifest")

    def buildinfo_file(self, product, version):
        return os.path.join(self.products, product, version + ".buildinfo")

    def module_file(self, module):
        """`ROOT/V6.32.20_3.11.14[-m2]` -> <Products>/modulefiles/ROOT/V6.32.20_3.11.14[-m2]"""
        return os.path.join(self.modulefiles, *module.split("/"))

    def bundle_file(self, name):
        return os.path.join(self.modulefiles, "vlist", name)


def products_dir_of(tool_root):
    """Derive the Products directory from an installed tool root `<Products>/.prod/versions/<v>`."""
    tool_root = os.path.realpath(tool_root)
    versions = os.path.dirname(tool_root)
    prod = os.path.dirname(versions)
    if os.path.basename(versions) != "versions" or os.path.basename(prod) != ".prod":
        raise EnvError(f"prod is not installed (running from {tool_root}); run install.sh first")
    return os.path.dirname(prod)


@dataclass
class Server:
    """Where the products server's Products directory is: `[user@]host:/path` or a local `/path`."""

    spec: str
    host: Optional[str]  # '[user@]host', or None for a local path
    path: str

    @property
    def is_local(self):
        return self.host is None

    def rsync_url(self, rel=""):
        path = self.path.rstrip("/") + ("/" + rel if rel else "")
        return path if self.is_local else f"{self.host}:{path}"


def parse_server(spec):
    if not isinstance(spec, str) or not spec:
        raise FormatError("server: empty value")
    m = _REMOTE_RE.match(spec)
    if m:
        host, path = spec[: spec.index(":")], m.group(1)
    elif spec.startswith("/"):
        host, path = None, spec
    else:
        raise FormatError(f"server '{spec}' is neither '[user@]host:/path' nor '/path'")
    if not _PATH_RE.match(path):
        raise FormatError(f"server path '{path}' must be absolute and use only [A-Za-z0-9._/-]")
    return Server(spec=spec, host=host, path=os.path.normpath(path))


@dataclass
class Config:
    role: str
    server: Optional[Server] = None
    ssh_options: List[str] = field(default_factory=lambda: ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"])

    def require_role(self, role, command):
        if self.role != role:
            raise EnvError(f"'{command}' runs only where role is '{role}' (this node: '{self.role}')")

    def require_server(self):
        if self.server is None:
            raise EnvError("no products server configured (set 'server' in prod.conf or PROD_SERVER)")
        return self.server


def loads(data, env=None):
    """Parse prod.conf. PROD_SERVER in env overrides 'server'."""
    env = os.environ if env is None else env
    doc = load_mapping(data, "prod.conf")
    if "format" not in doc:
        raise FormatError("prod.conf: missing field 'format'")
    check_format(doc["format"], FORMAT, "prod.conf")
    unknown = [k for k in doc if k not in _KEYS]
    if unknown:
        raise FormatError(f"prod.conf: unknown fields: {', '.join(map(str, unknown))}")
    role = doc.get("role")
    if role not in ROLES:
        raise FormatError(f"prod.conf: role must be one of {', '.join(ROLES)}")
    cfg = Config(role=role)
    server = env.get("PROD_SERVER") or doc.get("server")
    if server:
        cfg.server = parse_server(check_text("server", server))
    if "ssh_options" in doc:
        opts = doc["ssh_options"]
        if not isinstance(opts, list) or not all(isinstance(o, str) for o in opts):
            raise FormatError("prod.conf: ssh_options must be a list of strings")
        cfg.ssh_options = [check_text("ssh_options", o) for o in opts]
    if cfg.role == "client" and cfg.server is None:
        raise FormatError("prod.conf: a client needs 'server' (or PROD_SERVER)")
    return cfg


def load(paths, env=None):
    try:
        with open(paths.conf, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        raise EnvError(f"no configuration file {paths.conf}; run install.sh") from None
    return loads(data, env=env)


def dumps(role, server=None, ssh_options=None):
    """Write a prod.conf (used by install.sh)."""
    lines = [f"format: {quote(str(FORMAT))}", f"role: {quote(role)}"]
    if server is not None:
        parse_server(server)
        lines.append(f"server: {quote(server)}")
    if ssh_options is not None:
        lines.append("ssh_options: [" + ", ".join(quote(o) for o in ssh_options) + "]")
    data = ("\n".join(lines) + "\n").encode("utf-8")
    loads(data, env={})
    return data
