-- The value of the best rule in a (pattern, value, priority) seed, or null
-- when no rule matches. Fields are normalized text columns, tried in the
-- order given: the first field with any match decides, and within it the
-- rule with the highest priority wins. Rules that share a priority give the
-- same value, so ties cannot change the result. Patterns match whole words.
{% macro match_rules(rules, fields) -%}
    {%- set rows = [] -%}
    {%- for field in fields -%}
        {%- do rows.append('(' ~ loop.index ~ ', ' ~ field ~ ')') -%}
    {%- endfor -%}
    (
        select rules.value
        from {{ rules }} as rules
        inner join (
            values {{ rows | join(', ') }}
        ) as rule_fields (field_order, field_text)
            on rule_fields.field_text ~ {{ whole_words('rules.pattern') }}
        order by rule_fields.field_order asc, rules.priority desc
        limit 1
    )
{%- endmacro %}
