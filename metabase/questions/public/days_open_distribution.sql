-- Closed jobs by how many days they stayed open
with

buckets as (
    select
        case
            when days_open <= 7 then 1
            when days_open <= 14 then 2
            when days_open <= 30 then 3
            when days_open <= 60 then 4
            else 5
        end as bucket
    from marts.dim_jobs
    where
        not is_open
        [[and role_family = {{role_family}}]]
)

select
    (array['1 to 7', '8 to 14', '15 to 30', '31 to 60', 'over 60'])[bucket] as days_open,
    count(*) as closed_jobs
from buckets
group by bucket
order by bucket
