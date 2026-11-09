-- The date a posting was published, from the text the source gives. The
-- sources send either a plain date (2026-11-02) or a timestamp with a time
-- zone (2026-11-02T10:48:08.322Z or 2026-10-22T03:05:51-04:00). A timestamp
-- becomes its Europe/Athens date, the same calendar as run_date. A
-- timestamp without a zone keeps the date it shows. Anything else, or a
-- date that does not exist, gives null instead of failing the build.
{% macro parse_posted_date(column) -%}
    case
        when
            {{ column }} ~ '^\d{4}-\d{2}-\d{2}T.*(Z|[+-]\d{2}(:?\d{2})?)$'
            and pg_input_is_valid({{ column }}, 'timestamptz')
            then ({{ column }}::timestamptz at time zone 'Europe/Athens')::date
        when
            {{ column }} ~ '^\d{4}-\d{2}-\d{2}($|[T ])'
            and pg_input_is_valid(left({{ column }}, 10), 'date')
            then left({{ column }}, 10)::date
    end
{%- endmacro %}
