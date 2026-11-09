-- Every raw row with its posting_key and tidy text, without postings that
-- are not jobs, such as events and academic positions, without non-tech
-- jobs and without jobs that are only abroad. This view sees every day of
-- every posting, so it only does cheap work: a few regexes on the title,
-- the department and each place. The other matching and parsing happen in
-- stg_postings, on the latest row of each posting.

with

raw_postings as (
    select * from {{ source('raw', 'raw_postings') }}
),

-- All patterns joined into one. Null when the seed is empty.
non_job_rules as (
    select string_agg('(?:' || pattern || ')', '|' order by pattern) as pattern
    from {{ ref('non_job_rules') }}
),

-- Non-tech titles such as sales or legal. All patterns joined into one.
non_tech_rules as (
    select string_agg('(?:' || pattern || ')', '|' order by pattern) as pattern
    from {{ ref('non_tech_rules') }}
),

-- Every role family rule joined into one, since a tech rule beats a
-- non-tech one
tech_rules as (
    select string_agg('(?:' || pattern || ')', '|' order by pattern) as pattern
    from {{ ref('role_family_rules') }}
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
        -- The date the source says it was published. Some sources move it
        -- forward when a posting is reposted, so lifecycle takes the earliest.
        {{ parse_posted_date('posted_at_raw') }} as posted_at,
        content_hash,
        parser_version,
        load_id
    from newest_parse
),

-- Dropped here, so lifecycle, dedup, the marts and the daily summary never
-- see them. A posting with no title or no location is kept, and so is one
-- with any place that is not abroad. A non-tech title is only dropped when
-- no tech rule matches the title or the department, so a sales engineer
-- for security stays. Non-tech rules read the title only, since skywalker
-- departments often list sales next to the tech categories.
jobs as (
    select cleaned.*
    from cleaned
    cross join non_job_rules
    cross join non_tech_rules
    cross join tech_rules
    cross join abroad_countries
    where
        not coalesce(
            {{ normalize_text('cleaned.title') }} ~ {{ whole_words('non_job_rules.pattern') }},
            false
        )
        and (
            not coalesce(
                {{ normalize_text('cleaned.title') }} ~ {{ whole_words('non_tech_rules.pattern') }},
                false
            )
            or coalesce(
                {{ normalize_text('cleaned.title') }} ~ {{ whole_words('tech_rules.pattern') }},
                false
            )
            or coalesce(
                {{ normalize_text('cleaned.department') }}
                ~ {{ whole_words('tech_rules.pattern') }},
                false
            )
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
