#%Module
@HEADER@
# CUP data model. Same variables as the CMake-generated setup_cupdm.sh in the version directory.
module-whatis "CUPDataModel @VERSION@"
conflict CUPDATAMODEL
@DEPENDS@
setenv       CUPDM_DIR         @PREFIX@
prepend-path LD_LIBRARY_PATH   @PREFIX@/lib64
prepend-path ROOT_INCLUDE_PATH @PREFIX@/include
prepend-path CMAKE_PREFIX_PATH @PREFIX@
