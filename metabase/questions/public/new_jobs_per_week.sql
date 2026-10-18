-- Jobs first seen each week over the last 26 weeks, by role family. The
-- first week of tracking also counts every job that was already open.
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
