-- The latest cleaned version of each posting: normalized text, company
-- key, city, work mode, seniority, role family and monthly salary. The
-- rules are in "Transformation logic" in the design doc and in the seeds.

{{ config(materialized='table') }}

with

raw_postings as (
    select * from {{ ref('stg_raw_postings') }}
),

city_aliases as (
    select * from {{ ref('city_aliases') }}
),

company_suffixes as (
    select * from {{ ref('company_suffixes') }}
),

company_aliases as (
    select * from {{ ref('company_aliases') }}
),

-- The rule seeds with their patterns joined into one per value and
-- priority, which gives the same result. Postgres keeps only a few
-- compiled regexes at a time, so a handful of big patterns is several
-- times faster than one small pattern per rule.
work_mode_rules as (
    select
        value,
        priority,
        string_agg('(?:' || pattern || ')', '|' order by pattern) as pattern
    from {{ ref('work_mode_rules') }}
    group by value, priority
),

seniority_rules as (
    select
        value,
        priority,
        string_agg('(?:' || pattern || ')', '|' order by pattern) as pattern
    from {{ ref('seniority_rules') }}
    group by value, priority
),

role_family_rules as (
    select
        value,
        priority,
        string_agg('(?:' || pattern || ')', '|' order by pattern) as pattern
    from {{ ref('role_family_rules') }}
    group by value, priority
),

-- The newest row of each posting. There is at most one row per run_date.
latest as (
    select *
    from (
        select
            *,
            row_number() over (partition by posting_key order by run_date desc) as row_num
        from raw_postings
    ) as ranked
    where row_num = 1
),

normalized as (
    select
        posting_key,
        source,
        source_job_id,
        url,
        title,
        company_name,
        salary_text,
        content_hash,
        run_date as last_run_date,
        {{ normalize_text('title') }} as title_normalized,
        -- Dots go first, so the Greek "A.E." and "AE" both become one word
        {{ normalize_text("replace(company_name, '.', '')") }} as company_normalized,
        {{ normalize_text('location_text') }} as location_normalized,
        {{ normalize_text('workplace_text') }} as workplace_normalized,
        {{ normalize_text('department') }} as department_normalized,
        {{ normalize_text('description') }} as description_text
    from latest
),

-- One pattern for any run of legal suffixes at the end of a name, so
-- "single member sa" loses both parts. The space in front keeps a name
-- that is only a suffix as it is.
suffix_pattern as (
    select
        '( (' || string_agg(suffix, '|' order by length(suffix) desc, suffix) || '))+$'
            as pattern
    from company_suffixes
),

companies as (
    select
        stripped.posting_key,
        coalesce(company_aliases.company_key, stripped.company_stripped) as company_key
    from (
        select
            normalized.posting_key,
            coalesce(
                regexp_replace(normalized.company_normalized, suffix_pattern.pattern, ''),
                normalized.company_normalized
            ) as company_stripped
        from normalized
        cross join suffix_pattern
    ) as stripped
    left join company_aliases
        on stripped.company_stripped = company_aliases.alias
),

-- Location and title with everything but letters and digits turned into
-- single spaces, and a space at each end. City aliases hold only letters,
-- digits and spaces, so strpos finds them as whole words.
place_words as (
    select
        posting_key,
        ' ' || regexp_replace(location_normalized, '[^[:alnum:]]+', ' ', 'g') || ' '
            as location_words,
        ' ' || regexp_replace(title_normalized, '[^[:alnum:]]+', ' ', 'g') || ' '
            as title_words
    from normalized
),

-- The location, then the title. The first field with a known place wins;
-- within it the place that comes first, and of those the longest alias,
-- so Irakleio Attikis (a suburb of Athens) beats Irakleio.
cities as (
    select
        place_words.posting_key,
        coalesce(city_match.city, 'Unknown') as city,
        coalesce(city_match.region, 'Unknown') as region
    from place_words
    left join lateral (
        select
            city_aliases.city,
            city_aliases.region
        from city_aliases
        inner join (
            values (1, place_words.location_words), (2, place_words.title_words)
        ) as place_fields (field_order, words)
            on strpos(place_fields.words, ' ' || city_aliases.alias || ' ') > 0
        order by
            place_fields.field_order asc,
            strpos(place_fields.words, ' ' || city_aliases.alias || ' ') asc,
            length(city_aliases.alias) desc
        limit 1
    ) as city_match on true
),

classified as (
    select
        posting_key,
        coalesce(
            {{ match_rules('work_mode_rules', [
                'normalized.workplace_normalized',
                'normalized.location_normalized',
                'normalized.title_normalized',
                'normalized.description_text',
            ]) }},
            'unknown'
        ) as work_mode,
        coalesce(
            {{ match_rules('seniority_rules', ['normalized.title_normalized']) }},
            'unknown'
        ) as seniority,
        coalesce(
            {{ match_rules('role_family_rules', [
                'normalized.title_normalized',
                'normalized.department_normalized',
            ]) }},
            'other'
        ) as role_family
    from normalized
),

final as (
    select
        normalized.posting_key,
        normalized.source,
        normalized.source_job_id,
        normalized.url,
        normalized.title,
        normalized.title_normalized,
        normalized.company_name,
        companies.company_key,
        cities.city,
        cities.region,
        classified.work_mode,
        classified.seniority,
        classified.role_family,
        salary.salary_min_monthly,
        salary.salary_max_monthly,
        salary.salary_currency,
        salary.salary_basis,
        salary.salary_period,
        normalized.salary_text,
        normalized.description_text,
        normalized.content_hash,
        normalized.last_run_date
    from normalized
    inner join companies on normalized.posting_key = companies.posting_key
    inner join cities on normalized.posting_key = cities.posting_key
    inner join classified on normalized.posting_key = classified.posting_key
    -- The macro is a lateral subquery, so it cannot be a CTE
    left join lateral (  -- noqa: ST05
        {{ parse_salary('normalized.salary_text') }}
    ) as salary on true
)

select * from final
