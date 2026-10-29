-- Pairs of postings that look like the same job. Postings are only
-- compared within one company (company_key), and a pair needs titles with
-- the same words, first sightings close together, and a city and a
-- seniority that do not disagree. Titles are compared by title_key, which
-- has already dropped noise such as city names and "(m/f)" and mapped
-- synonyms such as developer and engineer, so a word left over on either
-- side names a different job, as in "backend engineer" and "backend
-- engineer payments". int_job_matches joins the pairs into jobs.

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
        postings.city,
        postings.work_mode,
        postings.seniority,
        lifecycle.first_seen,
        -- The words of the title in a fixed order, so word order does not count
        (
            select string_agg(distinct words.word, ' ' order by words.word)
            from unnest(string_to_array(postings.title_key, ' ')) as words (word)
        ) as title_words
    from postings
    inner join lifecycle on postings.posting_key = lifecycle.posting_key
    where postings.company_key is not null and postings.title_key is not null
),

final as (
    select
        posting_a.posting_key as posting_key_a,
        posting_b.posting_key as posting_key_b,
        posting_a.company_key,
        posting_a.title_words
    from matchable as posting_a
    inner join matchable as posting_b
        on
            posting_a.company_key = posting_b.company_key
            and posting_a.title_words = posting_b.title_words
            -- Each pair once, with the smaller key first
            and posting_a.posting_key < posting_b.posting_key
    where {{ dedup_pair_rules('posting_a', 'posting_b') }}
)

select * from final
