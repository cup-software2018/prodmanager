#%Module
@HEADER@
# ROOT. Everything bin/thisroot.sh sets is captured once at release time by 'generate'
#; ROOTSYS is set here because it is the home variable.
module-whatis "ROOT @VERSION@"
conflict ROOT
@DEPENDS@
setenv ROOTSYS @PREFIX@
@GENERATED@
