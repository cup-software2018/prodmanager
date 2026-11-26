// Check of a CUPDAQ installation, run by build-recipes/CUPDAQ/build.sh ('check' stage) as
//   root -l -b -q -n 'configcheck.C("<config.yml>")'
// Reads a DAQ configuration with RunConfig in interactive ROOT, as a user macro would: the classes come from the
// rootmap files on LD_LIBRARY_PATH and the headers from ROOT_INCLUDE_PATH; yaml-cpp does the parsing.

void configcheck(const char* file)
{
    RunConfig rc;
    if (!rc.ReadConfig(file)) {
        printf("configcheck: cannot read %s\n", file);
        return;
    }
    AbsConfList* list = rc.GetConfigs();
    int n = list ? list->GetEntries() : 0;
    if (n == 0) {
        printf("configcheck: %s gives no configurations\n", file);
        return;
    }
    printf("configcheck: OK, %s gives %d configurations (TCB %s, DAQ %s)\n", gSystem->BaseName(file), n,
           list->GetTCBConfig() ? "yes" : "no", list->GetDAQConfig() ? "yes" : "no");
}
