#%Module
@HEADER@
# CAEN HV wrapper library. CAENHVWRAPPER_HOME is the home variable.
module-whatis "CAEN HV wrapper @VERSION@"
conflict CAENHVWRAPPER
@DEPENDS@
setenv       CAENHVWRAPPER_HOME @PREFIX@
prepend-path LD_LIBRARY_PATH    @PREFIX@/lib
