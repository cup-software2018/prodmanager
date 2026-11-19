#!/bin/sh
# Build record of ROOT from root-config. Prints "key<TAB>value" lines.
rc="$1/bin/root-config"
[ -x "$rc" ] || { echo "no $rc" >&2; exit 1; }
for opt in --version --git-revision --python-version --cxx --cflags --features --config; do
    printf 'root-config %s\t%s\n' "$opt" "$("$rc" "$opt" 2>/dev/null | tr '\n' ' ' | sed 's/ *$//')"
done
