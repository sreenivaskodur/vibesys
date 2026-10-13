source $::env(TPCDIR)/common.tcl
dbset bm TPC-C
diset tpcc pg_superuser $::env(PGSU)
diset tpcc pg_defaultdbase postgres
diset tpcc pg_count_ware $::env(WAREHOUSES)
diset tpcc pg_num_vu [expr {min(16, $::env(WAREHOUSES))}]
diset tpcc pg_storedprocs true
buildschema
