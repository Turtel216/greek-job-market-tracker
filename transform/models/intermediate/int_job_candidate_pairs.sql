-- Pairs of postings that look like the same job. Postings are only
-- compared within one company (company_key), and a pair needs similar
-- titles, first sightings close together, a city and a seniority that do
-- not disagree. Titles are compared by title_key, and two titles that each
-- have a word the other lacks, such as "backend engineer payments" and
-- "backend engineer risk", name different jobs. int_job_matches joins the
-- pairs into jobs. See "Deduplication" in the design doc.

with

postings as (
    select * from {{ ref('stg_postings') }}
),

lifecycle as (
    select * from {{ ref('int_posting_lifecycle') }}
),

-- A posting with no company or title key cannot be matched, so it stays
-- a job of its own
matchable as (
    select
        postings.posting_key,
        postings.company_key,
        postings.title_key,
        postings.city,
        postings.work_mode,
        postings.seniority,
        lifecycle.first_seen,
        string_to_array(postings.title_key, ' ') as title_words
    from postings
    inner join lifecycle on postings.posting_key = lifecycle.posting_key
    where postings.company_key is not null and postings.title_key is not null
),

pairs as (
    select
        posting_a.posting_key as posting_key_a,
        posting_b.posting_key as posting_key_b,
        posting_a.company_key,
        similarity(posting_a.title_key, posting_b.title_key) as title_similarity
    from matchable as posting_a
    inner join matchable as posting_b
        on
            posting_a.company_key = posting_b.company_key
            -- Each pair once, with the smaller key first
            and posting_a.posting_key < posting_b.posting_key
    where
        {{ dedup_pair_rules('posting_a', 'posting_b') }}
        -- One title has all the words of the other
        and (
            posting_a.title_words <@ posting_b.title_words
            or posting_b.title_words <@ posting_a.title_words
        )
),

final as (
    select * from pairs
    where title_similarity >= {{ var('dedup_title_threshold') }}
)

select * from final
