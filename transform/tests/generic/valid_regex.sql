-- Fails when a value is not a valid Postgres regular expression. Postgres
-- only compiles a pattern when it is used, so each one is matched against
-- an empty string, which raises an error for a bad pattern. The match
-- itself is never null, so a good pattern returns no rows.
{% test valid_regex(model, column_name) %}

select {{ column_name }}
from {{ model }}
where
    {{ column_name }} is not null
    and ('' ~ {{ column_name }}) is null

{% endtest %}
