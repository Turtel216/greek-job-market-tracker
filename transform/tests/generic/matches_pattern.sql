-- Fails for each value that does not match a Postgres regular expression.
-- Nulls pass; use not_null for those.
{% test matches_pattern(model, column_name, pattern) %}

select {{ column_name }}
from {{ model }}
where
    {{ column_name }} is not null
    and {{ column_name }} !~ '{{ pattern }}'

{% endtest %}
