source $::env(TPCDIR)/common.tcl
dbset bm TPC-H
diset tpch pg_tpch_superuser $::env(PGSU)
diset tpch pg_scale_fact $::env(SF)
diset tpch pg_total_querysets 1
diset tpch pg_degree_of_parallel 8
loadscript
vuset vu 1
vuset logtotemp 1
vucreate
set jobid [vurun]
vudestroy
puts "RESULT_JOB $jobid"
jobs $jobid result
