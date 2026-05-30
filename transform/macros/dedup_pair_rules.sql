-- The rules, other than title similarity, that two postings of one company
-- must meet to be the same job: first seen at most dedup_window_days apart,
-- cities that agree unless one is Unknown or remote, and seniorities that
-- agree unless one is unknown. int_job_candidate_pairs and the dedup
-- analyses all use it, so they always agree. Both arguments are table
-- aliases with first_seen, city, work_mode and seniority columns.
{% macro dedup_pair_rules(posting_a, posting_b) -%}
    abs({{ posting_a }}.first_seen - {{ posting_b }}.first_seen) <= {{ var('dedup_window_days') }}
    and (
        {{ posting_a }}.city = {{ posting_b }}.city
        or 'Unknown' in ({{ posting_a }}.city, {{ posting_b }}.city)
        or 'remote' in ({{ posting_a }}.work_mode, {{ posting_b }}.work_mode)
    )
    and (
        {{ posting_a }}.seniority = {{ posting_b }}.seniority
        or 'unknown' in ({{ posting_a }}.seniority, {{ posting_b }}.seniority)
    )
{%- endmacro %}
