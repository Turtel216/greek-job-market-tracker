-- Every raw row with its posting_key and tidy text. This view sees every
-- day of every posting, so it only does cheap work. Matching and parsing
-- happen in stg_postings, on the latest row of each posting.

with

raw_postings as (
    select * from {{ source('raw', 'raw_postings') }}
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
    from raw_postings
)

select * from cleaned
