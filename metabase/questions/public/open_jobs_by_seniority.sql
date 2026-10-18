-- Jobs open now by seniority. unknown means the title gives no signal.
select
    seniority,
    count(*) as jobs
from marts.dim_jobs
where
    is_open
    [[and role_family = {{role_family}}]]
group by seniority
order by array_position(array['intern', 'junior', 'mid', 'senior', 'lead', 'unknown'], seniority)
