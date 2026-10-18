-- Work mode of the jobs open now in the 8 cities with the most of them.
-- The rest are grouped as Other.
with

open_jobs as (
    select
        city,
        work_mode
    from marts.dim_jobs
    where
        is_open
        [[and role_family = {{role_family}}]]
),

top_cities as (
    select city
    from open_jobs
    group by city
    order by count(*) desc, city asc
    limit 8
),

grouped as (
    select
        case when city in (select city from top_cities) then city else 'Other' end as city,
        work_mode
    from open_jobs
)

select
    city,
    work_mode,
    count(*) as jobs
from grouped
group by city, work_mode
order by sum(count(*)) over (partition by city) desc, city asc, work_mode asc
