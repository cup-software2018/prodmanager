#!/bin/sh
# Build record of a Python installation: version, configure arguments, installed packages.
# Prints "key<TAB>value" lines. Writes nothing: no bytecode, no pip cache.
set -e
py="$1/bin/python3"
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
printf 'python --version\t%s\n' "$("$py" -B -I -c 'import sys; print(sys.version.split()[0])')"
printf 'CONFIG_ARGS\t%s\n' "$("$py" -B -I -c 'import sysconfig; print(sysconfig.get_config_var("CONFIG_ARGS") or "")')"
"$py" -B -I -m pip list --format=freeze 2>/dev/null | sed -n 's/^\([^=]*\)==\(.*\)$/pip \1\t\2/p'
