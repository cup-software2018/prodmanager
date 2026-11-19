#%Module
@HEADER@
# Python built from source as a full installation (not a venv). Libraries are found by rpath.
# Legacy: PYTHON/setup_PYTHON.sh.
module-whatis "Python @VERSION@"
conflict PYTHON
@DEPENDS@
setenv       PYTHON_HOME @PREFIX@
prepend-path PATH        @PREFIX@/bin
