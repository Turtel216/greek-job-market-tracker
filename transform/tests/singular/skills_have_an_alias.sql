-- A skill without an alias can never be found in a posting
select skills.skill_id
from {{ ref('skills') }} as skills
left join {{ ref('skill_aliases') }} as aliases
    on skills.skill_id = aliases.skill_id
where aliases.skill_id is null
