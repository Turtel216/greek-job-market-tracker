-- dim_jobs must have one row for every job that int_job_matches gives
select matches.job_id
from {{ ref('int_job_matches') }} as matches
left join {{ ref('dim_jobs') }} as jobs
    on matches.job_id = jobs.job_id
where jobs.job_id is null
