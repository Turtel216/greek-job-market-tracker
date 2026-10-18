-- Jobs first seen in the 7 days up to the newest run
select coalesce(sum(new_postings), 0) as new_jobs
from marts.fct_daily_postings
where
    date > (select max(date) from marts.fct_daily_postings) - 7
    [[and role_family = {{role_family}}]]
