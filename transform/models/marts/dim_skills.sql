-- One row per skill that skill extraction can find, from the skills seed.
-- Skills in the it_support_skills seed are flagged, so the dashboard can
-- leave them out unless an IT role family is picked.

with

skills as (
    select * from {{ ref('skills') }}
),

it_support_skills as (
    select * from {{ ref('it_support_skills') }}
),

final as (
    select
        skills.skill_id,
        skills.name,
        skills.category,
        it_support_skills.skill_id is not null as is_it_support
    from skills
    left join it_support_skills on skills.skill_id = it_support_skills.skill_id
)

select * from final
