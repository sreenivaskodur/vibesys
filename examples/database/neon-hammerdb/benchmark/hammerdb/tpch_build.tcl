source $::env(TPCDIR)/common.tcl
dbset bm TPC-H
diset tpch pg_tpch_superuser $::env(PGSU)
diset tpch pg_tpch_defaultdbase postgres
diset tpch pg_scale_fact $::env(SF)
diset tpch pg_num_tpch_threads 16
buildschema
