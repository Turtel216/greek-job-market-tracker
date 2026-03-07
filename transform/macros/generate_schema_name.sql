-- Use the schema set in the config as is, so a seed with schema staging
-- lands in staging and not in staging_staging. Without one, use the
-- target schema.
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
