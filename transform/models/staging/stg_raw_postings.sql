-- Every raw row with its posting_key and tidy text, without postings that
-- are not jobs, such as events and academic positions, and without jobs
-- that are only abroad. This view sees every day of every posting, so it
-- only does cheap work: one regex on the title and one on each place. The
-- other matching and parsing happen in stg_postings, on the latest row of
-- each posting.

with

raw_postings as (
    select * from {{ source('raw', 'raw_postings') }}
),

-- All patterns joined into one. Null when the seed is empty.
non_job_rules as (
    select string_agg('(?:' || pattern || ')', '|' order by pattern) as pattern
    from {{ ref('non_job_rules') }}
),

-- All countries joined into one pattern for the end of a place. Null when
-- the seed is empty.
abroad_countries as (
    select '(^| )(' || string_agg(country, '|' order by country) || ')$' as pattern
    from {{ ref('abroad_countries') }}
),

-- A reparse after a parser fix adds rows with a new parser_version next to
-- the old ones. Only the newest load of each posting and day is kept.
newest_parse as (
    select distinct on (source, source_job_id, run_date) *
    from raw_postings
    order by source asc, source_job_id asc, run_date asc, raw_posting_id desc
),

cleaned as (
    select
        md5(source || ':' || source_job_id) as posting_key,
        raw_posting_id,
        source,
        source_job_id,
        run_date,
        scraped_at,
        url,
        {{ squish('title_raw') }} as title,
        {{ squish('company_raw') }} as company_name,
        {{ squish('location_raw') }} as location_text,
        {{ squish('workplace_raw') }} as workplace_text,
        {{ squish('employment_type_raw') }} as employment_type_text,
        {{ squish('salary_raw') }} as salary_text,
        {{ squish('department_raw') }} as department,
        -- Line breaks are kept for now; normalize_text removes them later
        nullif(btrim(description_raw), '') as description,
        content_hash,
        parser_version,
        load_id
    from newest_parse
),

-- Dropped here, so lifecycle, dedup, the marts and the daily summary never
-- see them. A posting with no title or no location is kept, and so is one
-- with any place that is not abroad.
jobs as (
    select cleaned.*
    from cleaned
    cross join non_job_rules
    cross join abroad_countries
    where
        not coalesce(
            {{ normalize_text('cleaned.title') }} ~ {{ whole_words('non_job_rules.pattern') }},
            false
        )
        and (
            cleaned.location_text is null
            or exists (
                select 1
                from unnest(string_to_array(cleaned.location_text, ';')) as places (place)
                where
                    not coalesce(
                        {{ normalize_text('places.place') }} ~ abroad_countries.pattern,
                        false
                    )
            )
        )
)

select * from jobs
