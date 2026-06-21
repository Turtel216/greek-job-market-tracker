-- Fails when the tested column adds up to more than max_share of the
-- postings seen in the newest run. The column counts, for each row of a qa
-- view, the postings from that run with the row's value. Meant to be used
-- with severity warn, to show when a seed falls behind real data.
{% test share_of_todays_postings(model, column_name, max_share=0.1) %}

with

postings as (
    select * from {{ ref('stg_postings') }}
),

todays as (
    select count(*) as todays_postings
    from postings
    where last_run_date = (select max(last_run_date) from postings)
),

flagged as (
    select coalesce(sum({{ column_name }}), 0) as flagged_postings
    from {{ model }}
)

select
    flagged.flagged_postings,
    todays.todays_postings
from flagged
cross join todays
where flagged.flagged_postings > {{ max_share }} * todays.todays_postings

{% endtest %}
