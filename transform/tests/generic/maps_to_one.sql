-- Fails when one value of the tested column goes with more than one value
-- of another column, for example a city found in two regions.
{% test maps_to_one(model, column_name, column) %}

select {{ column_name }}
from {{ model }}
group by {{ column_name }}
having count(distinct {{ column }}) > 1

{% endtest %}
