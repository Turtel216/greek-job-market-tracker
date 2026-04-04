-- Wraps a regular expression so it only matches whole words of normalized
-- text: the match must start and end at the edge of the text or next to
-- something that is not a letter or digit. Takes a SQL expression, such
-- as a column or a quoted string, and returns one.
{% macro whole_words(pattern) -%}
    ('(^|[^a-z0-9α-ω])(' || {{ pattern }} || ')($|[^a-z0-9α-ω])')
{%- endmacro %}
