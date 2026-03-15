-- A company_key must not be an alias of another key. Otherwise one lookup
-- would not be enough and the result would depend on the order of joins.
select aliases.alias
from {{ ref('company_aliases') }} as aliases
inner join {{ ref('company_aliases') }} as mapped
    on aliases.company_key = mapped.alias
