-- The form all matching is done in: lowercase, no accents, final sigma
-- written as a normal sigma, punctuation other than + # . replaced by a
-- space, and single spaces only. Every alias and keyword in the seeds is
-- written this way. unaccent also strips the Greek tonos and dialytika, so
-- they need no map of their own. Text that is empty afterwards becomes null.
{% macro normalize_text(column) -%}
    nullif(
        btrim(
            regexp_replace(
                translate(lower(unaccent({{ column }})), 'ς', 'σ'),
                '[^[:alnum:]+#.]+', ' ', 'g'
            )
        ),
        ''
    )
{%- endmacro %}
