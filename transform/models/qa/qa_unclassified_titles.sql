-- Titles that matched no role family rule, so the role family fell back to
-- other. Add a rule for the common ones: a tech rule, or a row in
-- non_tech_rules for roles such as sales, which staging then drops. One row
-- per normalized title, the most common first.

with

postings as (
    select * from {{ ref('stg_postings') }}
),

newest_run as (
    select max(last_run_date) as run_date from postings
),

unclassified as (
    select
        postings.title,
        postings.title_normalized,
        postings.last_run_date = newest_run.run_date as is_today
    from postings
    cross join newest_run
    where not postings.role_family_matched and postings.title_normalized is not null
),

final as (
    select
        title_normalized,
        min(title) as example_title,
        count(*) as postings,
        count(*) filter (where is_today) as postings_today
    from unclassified
    group by title_normalized
)

select * from final
order by postings desc, title_normalized asc
