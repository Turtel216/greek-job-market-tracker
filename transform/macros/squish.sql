-- Text for display: runs of whitespace become one space, the ends are
-- trimmed, and empty text becomes null.
{% macro squish(column) -%}
    nullif(btrim(regexp_replace({{ column }}, '\s+', ' ', 'g')), '')
{%- endmacro %}
