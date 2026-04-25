-- The job each posting belongs to. Deduplication comes in Phase 4; until
-- then every posting is a job of its own, so job_id is its posting_key.
-- The marts read job_id from here only, so dedup can change this model
-- without touching them. See "Deduplication" in the design doc.

with

postings as (
    select * from {{ ref('stg_postings') }}
),

final as (
    select
        posting_key,
        posting_key as job_id
    from postings
)

select * from final
