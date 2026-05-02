-- Skills of each job: every skill found in any of its postings, with title
-- when any posting has it in the title

with

posting_skills as (
    select * from {{ ref('int_posting_skills') }}
),

job_matches as (
    select * from {{ ref('int_job_matches') }}
),

final as (
    select
        job_matches.job_id,
        posting_skills.skill_id,
        case
            when bool_or(posting_skills.matched_in = 'title') then 'title' else 'description'
        end as matched_in
    from posting_skills
    inner join job_matches on posting_skills.posting_key = job_matches.posting_key
    group by job_matches.job_id, posting_skills.skill_id
)

select * from final
