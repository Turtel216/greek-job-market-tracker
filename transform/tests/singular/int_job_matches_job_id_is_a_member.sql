-- A job_id is the posting_key of one of the job's own postings, so the
-- posting it names belongs to that same job
select
    matches.posting_key,
    matches.job_id
from {{ ref('int_job_matches') }} as matches
inner join {{ ref('int_job_matches') }} as id_posting
    on matches.job_id = id_posting.posting_key
where id_posting.job_id != matches.job_id
