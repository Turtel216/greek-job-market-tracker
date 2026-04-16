-- Fails for each combination of the given columns that appears more than
-- once. Use it on a model to check its grain, such as posting and skill.
{% test unique_combination(model, columns) %}

select {{ columns | join(', ') }}
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1

{% endtest %}
