source $::env(TPCDIR)/common.tcl
dbset bm TPC-C
diset tpcc pg_superuser $::env(PGSU)
diset tpcc pg_count_ware $::env(WAREHOUSES)
diset tpcc pg_storedprocs true
diset tpcc pg_driver timed
diset tpcc pg_rampup $::env(RAMPUP)
diset tpcc pg_duration $::env(DURATION)
diset tpcc pg_allwarehouse false
diset tpcc pg_timeprofile true
loadscript
vuset vu $::env(VUS)
vuset logtotemp 1
vucreate
set jobid [vurun]
vudestroy
puts "RESULT_JOB $jobid"
jobs $jobid result
