-- One row per skill that skill extraction can find, from the skills seed

with

skills as (
    select * from {{ ref('skills') }}
),

final as (
    select
        skill_id,
        name,
        category
    from skills
)

select * from final
