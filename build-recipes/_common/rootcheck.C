// Run by build-common.sh (check_root_classes) as
//   root -l -b -q -n 'rootcheck.C("<libdir>")'
// Interactive ROOT must find every class of a product's libraries by name alone: the rootmap files in <libdir>
// (which is on LD_LIBRARY_PATH) load the library, and its dictionary (the _rdict.pcm next to it) must be found.

#include <fstream>
#include <string>

#include <TClass.h>
#include <TList.h>
#include <TSystemDirectory.h>

void rootcheck(const char* libdir)
{
    const std::string ext = ".rootmap";
    TSystemDirectory d("lib", libdir);
    TList* files = d.GetListOfFiles();
    int nmaps = 0, nclasses = 0, bad = 0;
    for (auto* f : *files) {
        std::string name = f->GetName();
        if (name.size() <= ext.size() || name.compare(name.size() - ext.size(), ext.size(), ext) != 0) continue;
        ++nmaps;
        std::ifstream in(std::string(libdir) + "/" + name);
        std::string word, cls;
        while (in >> word) {
            if (word != "class") continue;
            in >> cls;
            ++nclasses;
            TClass* c = TClass::GetClass(cls.c_str());
            if (!c || !c->IsLoaded() || !c->HasDictionary()) {
                printf("rootcheck: class %s from %s cannot be loaded\n", cls.c_str(), name.c_str());
                ++bad;
            }
        }
    }
    if (nmaps == 0 || nclasses == 0) printf("rootcheck: no rootmap files with classes in %s\n", libdir);
    else if (bad == 0) printf("rootcheck: OK, all %d classes of %d rootmap files load by name\n", nclasses, nmaps);
}
