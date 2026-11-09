-- Jobs opened each week over the last 26 weeks, by role family. A job
-- opens on the date its source says it was posted, or the day it was first
-- seen when that is earlier. Jobs opened before tracking began are not new.
select
    date_trunc('week', date)::date as week,
    role_family,
    sum(new_postings) as new_jobs
from marts.fct_daily_postings
where
    date > (select max(date) from marts.fct_daily_postings) - interval '26 weeks'
    [[and role_family = {{role_family}}]]
group by 1, 2
order by 1, 2
