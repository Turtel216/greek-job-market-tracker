-- Pairs of postings to label by hand for dedup_labels. Most are pairs that
-- deduplication merged, so dedup_precision has enough merges to score.
-- The rest are near misses: pairs that meet the rules in dedup_pair_rules
-- with a title similarity under the threshold but at least
-- similarity_floor, so the threshold can be tuned both ways. Pairs that
-- are already labeled are left out. The order is random but stable, so the
-- same data gives the same sample.
--
-- Run it with: scripts/dedup-label-sample.sh, which writes a CSV to label.

{%- set sample_size = 200 %}
{%- set near_misses = 50 %}
{%- set similarity_floor = 0.3 %}

with

postings as (
    select * from {{ ref('stg_postings') }}
),

lifecycle as (
    select * from {{ ref('int_posting_lifecycle') }}
),

matches as (
    select * from {{ ref('int_job_matches') }}
),

labels as (
    select * from {{ ref('dedup_labels') }}
),

described as (
    select
        postings.posting_key,
        postings.source,
        postings.source_job_id,
        postings.company_key,
        postings.company_name,
        postings.title,
        postings.title_key,
        postings.city,
        postings.work_mode,
        postings.seniority,
        postings.url,
        lifecycle.first_seen,
        matches.job_id
    from postings
    inner join lifecycle on postings.posting_key = lifecycle.posting_key
    inner join matches on postings.posting_key = matches.posting_key
),

-- Every pair within a company. Merged pairs always share a company.
pairs as (
    select
        posting_a.source as source_a,
        posting_a.source_job_id as source_job_id_a,
        posting_b.source as source_b,
        posting_b.source_job_id as source_job_id_b,
        posting_a.job_id = posting_b.job_id as is_merged,
        round(
            similarity(posting_a.title_key, posting_b.title_key)::numeric, 3
        ) as title_similarity,
        posting_a.title as title_a,
        posting_b.title as title_b,
        posting_a.title_key as title_key_a,
        posting_b.title_key as title_key_b,
        posting_a.company_name as company_a,
        posting_b.company_name as company_b,
        posting_a.city as city_a,
        posting_b.city as city_b,
        posting_a.work_mode as work_mode_a,
        posting_b.work_mode as work_mode_b,
        posting_a.seniority as seniority_a,
        posting_b.seniority as seniority_b,
        posting_a.first_seen as first_seen_a,
        posting_b.first_seen as first_seen_b,
        posting_a.url as url_a,
        posting_b.url as url_b,
        {{ dedup_pair_rules('posting_a', 'posting_b') }} as meets_rules,
        md5(posting_a.posting_key || posting_b.posting_key) as sample_order
    from described as posting_a
    inner join described as posting_b
        on
            posting_a.company_key = posting_b.company_key
            and posting_a.posting_key < posting_b.posting_key
),

unlabeled as (
    select pairs.*
    from pairs
    left join labels
        on
            (
                pairs.source_a = labels.source_a
                and pairs.source_job_id_a = labels.source_job_id_a
                and pairs.source_b = labels.source_b
                and pairs.source_job_id_b = labels.source_job_id_b
            )
            or (
                pairs.source_a = labels.source_b
                and pairs.source_job_id_a = labels.source_job_id_b
                and pairs.source_b = labels.source_a
                and pairs.source_job_id_b = labels.source_job_id_a
            )
    where labels.source_a is null
),

ranked as (
    select
        *,
        row_number() over (partition by is_merged order by sample_order) as sample_rank
    from unlabeled
    where is_merged or (meets_rules and title_similarity >= {{ similarity_floor }})
),

final as (
    select
        source_a,
        source_job_id_a,
        source_b,
        source_job_id_b,
        is_merged,
        title_similarity,
        title_a,
        title_b,
        title_key_a,
        title_key_b,
        company_a,
        company_b,
        city_a,
        city_b,
        work_mode_a,
        work_mode_b,
        seniority_a,
        seniority_b,
        first_seen_a,
        first_seen_b,
        url_a,
        url_b
    from ranked
    where
        (is_merged and sample_rank <= {{ sample_size - near_misses }})
        or (not is_merged and sample_rank <= {{ near_misses }})
    order by is_merged desc, sample_order asc
)

select * from final
