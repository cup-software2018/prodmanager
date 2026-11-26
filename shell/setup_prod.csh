# Set up the environment of a released vlist. Source it, do not run it:
#
#     source ~/setup_prod.csh <vlist>
#
# The csh counterpart of setup_prod.sh: loads the vlist bundle modulefile `vlist/<vlist>`.
# Without <vlist> it lists the releases on this node; in an interactive shell on a terminal it then asks which one
# to load (Enter: none).
# @PRODUCTS@ is replaced with the Products directory by install.sh.

set _prod_products = "@PRODUCTS@"
set _prod_name = ""
if ( $#argv >= 1 ) set _prod_name = "$argv[1]"

# Run module with MODULES_VERBOSITY=concise (errors and warnings only), restoring any previous value.
set _prod_verbosity = ""
if ( $?MODULES_VERBOSITY ) set _prod_verbosity = "$MODULES_VERBOSITY"
setenv MODULES_VERBOSITY concise

set _prod_alias = "`alias module`"
if ( "$_prod_alias" == "" ) then
    if ( -r /usr/share/Modules/init/csh ) then
        source /usr/share/Modules/init/csh
    else
        echo "prod: environment-modules is not installed (/usr/share/Modules/init/csh)" > /dev/stderr
        goto _prod_end
    endif
endif

if ( ! $?MODULEPATH ) setenv MODULEPATH ""
echo ":${MODULEPATH}:" | grep -qF ":${_prod_products}/modulefiles:"
if ( $status != 0 ) module use "${_prod_products}/modulefiles"

if ( "$_prod_name" == "" ) then
    set _prod_list = ( `ls "${_prod_products}/vlist" | sed -n 's/\.yml$//p'` )
    if ( $#_prod_list == 0 ) then
        echo "prod: no releases on this node; install one with 'prodman sync <vlist>'" > /dev/stderr
        goto _prod_end
    endif
    echo "releases on this node:" > /dev/stderr
    @ _prod_i = 0
    foreach _prod_n ( $_prod_list )
        @ _prod_i++
        set _prod_desc = "`sed -n 's/^description: .\(.*\).$/\1/p' ${_prod_products}/vlist/${_prod_n}.yml`"
        printf "  %d) %-12s %s\n" $_prod_i "$_prod_n" "$_prod_desc" > /dev/stderr
    end
    tty -s
    if ( $?prompt && $status == 0 ) then
        echo -n "choose a release (number or name, Enter: none): " > /dev/stderr
        set _prod_choice = "$<"
        if ( "$_prod_choice" == "" ) goto _prod_end
        echo "$_prod_choice" | grep -qE '^[0-9]+$'
        if ( $status == 0 ) then
            if ( $_prod_choice >= 1 && $_prod_choice <= $#_prod_list ) then
                set _prod_name = "$_prod_list[$_prod_choice]"
            else
                set _prod_name = "$_prod_choice"
            endif
        else
            set _prod_name = "$_prod_choice"
        endif
    else
        echo "usage: source ~/setup_prod.csh <vlist>" > /dev/stderr
        goto _prod_end
    endif
endif

if ( ! -f "${_prod_products}/vlist/${_prod_name}.yml" ) then
    echo "prod: vlist '${_prod_name}' is not installed on this node; run 'prodman sync ${_prod_name}'" > /dev/stderr
    goto _prod_end
endif

module is-loaded vlist >& /dev/null
if ( $status == 0 ) then
    module switch vlist "vlist/${_prod_name}"
else
    module load "vlist/${_prod_name}"
endif
if ( $status != 0 ) echo "prod: loading vlist ${_prod_name} failed" > /dev/stderr

_prod_end:
if ( "$_prod_verbosity" == "" ) then
    unsetenv MODULES_VERBOSITY
else
    setenv MODULES_VERBOSITY "$_prod_verbosity"
endif
unset _prod_products _prod_name _prod_alias _prod_verbosity _prod_list _prod_i _prod_n _prod_desc _prod_choice
