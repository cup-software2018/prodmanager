#%Module
@HEADER@
# CAEN HV wrapper library. The legacy setup only added lib/ to LD_LIBRARY_PATH;
# CAENHVWRAPPER_HOME is new (the home variable).
module-whatis "CAEN HV wrapper @VERSION@"
conflict CAENHVWRAPPER
@DEPENDS@
setenv       CAENHVWRAPPER_HOME @PREFIX@
prepend-path LD_LIBRARY_PATH    @PREFIX@/lib
