-- The job each posting belongs to. Candidate pairs are joined into
-- clusters, so if A pairs with B and B with C, all three are one job. The
-- job_id is the posting_key of the member first seen, with posting_key
-- breaking ties, so a full refresh gives the same ids. A posting with no
-- pair is a job of its own.

with recursive

postings as (
    select * from {{ ref('stg_postings') }}
),

lifecycle as (
    select * from {{ ref('int_posting_lifecycle') }}
),

candidate_pairs as (
    select * from {{ ref('int_job_candidate_pairs') }}
),

-- Each pair in both directions
edges as (
    select
        posting_key_a as posting_key,
        posting_key_b as other_key
    from candidate_pairs
    union all
    select
        posting_key_b as posting_key,
        posting_key_a as other_key
    from candidate_pairs
),

-- Every posting each posting can reach through pairs, itself included.
-- union drops rows already found, so the recursion stops on cycles.
reachable as (
    select
        posting_key,
        posting_key as member_key
    from postings
    union
    select
        reachable.posting_key,
        edges.other_key as member_key
    from reachable
    inner join edges on reachable.member_key = edges.posting_key
),

final as (
    select
        reachable.posting_key,
        (array_agg(
            reachable.member_key order by lifecycle.first_seen asc, reachable.member_key asc
        ))[1] as job_id
    from reachable
    inner join lifecycle on reachable.member_key = lifecycle.posting_key
    group by reachable.posting_key
)

select * from final
