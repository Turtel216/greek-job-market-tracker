-- Fails for each group whose newest day has fewer rows than min_ratio times
-- its average over the days days before it, so a scraper that suddenly
-- returns partial data stops the build. The tested column is the date. Days
-- with no rows, such as a failed run, are left out of the average, and a
-- group with no earlier days in the window passes.
{% test volume_vs_7d_avg(model, column_name, group_by, min_ratio=0.5, days=7) %}

with

-- Only the window before the newest day of any group is needed
recent as (
    select
        {{ group_by }} as group_value,
        {{ column_name }} as day
    from {{ model }}
    where {{ column_name }} >= (select max({{ column_name }}) from {{ model }}) - {{ days }}
),

daily as (
    select
        group_value,
        day,
        count(*) as row_count
    from recent
    group by group_value, day
),

newest as (
    select distinct on (group_value)
        group_value,
        day,
        row_count
    from daily
    order by group_value asc, day desc
),

compared as (
    select
        newest.group_value,
        newest.day,
        newest.row_count,
        avg(earlier.row_count) as average_rows
    from newest
    inner join daily as earlier
        on
            newest.group_value = earlier.group_value
            and earlier.day < newest.day
            and earlier.day >= newest.day - {{ days }}
    group by newest.group_value, newest.day, newest.row_count
)

select * from compared
where row_count < {{ min_ratio }} * average_rows

{% endtest %}
