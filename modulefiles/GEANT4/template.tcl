#%Module
@HEADER@
# Geant4. Everything geant4make.sh sets (data sets, build options, G4WORKDIR) is captured once at release
# time by 'generate'. G4BASE (the version directory) is the home variable.
module-whatis "Geant4 @VERSION@"
conflict GEANT4
@DEPENDS@
setenv G4BASE @PREFIX@
@GENERATED@
