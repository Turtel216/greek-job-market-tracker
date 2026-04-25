-- Deduplication must not lose postings: every posting in stg_postings
-- belongs to exactly one job
select postings.posting_key
from {{ ref('stg_postings') }} as postings
left join {{ ref('int_job_matches') }} as matches
    on postings.posting_key = matches.posting_key
where matches.posting_key is null
