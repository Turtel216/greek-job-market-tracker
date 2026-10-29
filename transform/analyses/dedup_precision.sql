-- How well deduplication agrees with the hand-labeled pairs in
-- dedup_labels. A pair is merged when both postings got the same job_id,
-- so pairs joined through a chain count too. Precision is the share of
-- merged pairs that really are one job; the target is at least 0.95.
-- Recall is the share of real duplicates that were merged.
--
-- Run it with: dbt show --select dedup_precision --output json

with

labels as (
    select * from {{ ref('dedup_labels') }}
),

postings as (
    select * from {{ ref('stg_postings') }}
),

matches as (
    select * from {{ ref('int_job_matches') }}
),

-- Labeled pairs whose postings are both in the warehouse
scored as (
    select
        labels.is_duplicate,
        match_a.job_id = match_b.job_id as is_merged
    from labels
    inner join postings as posting_a
        on
            labels.source_a = posting_a.source
            and labels.source_job_id_a = posting_a.source_job_id
    inner join postings as posting_b
        on
            labels.source_b = posting_b.source
            and labels.source_job_id_b = posting_b.source_job_id
    inner join matches as match_a on posting_a.posting_key = match_a.posting_key
    inner join matches as match_b on posting_b.posting_key = match_b.posting_key
),

labeled_count as (
    select count(*) as pairs_labeled from labels
),

counts as (
    select
        count(*) as pairs_scored,
        count(*) filter (where is_merged) as merged,
        count(*) filter (where is_merged and is_duplicate) as true_merges,
        count(*) filter (where is_merged and not is_duplicate) as false_merges,
        count(*) filter (where not is_merged and is_duplicate) as missed_duplicates,
        count(*) filter (where is_duplicate) as duplicates
    from scored
),

final as (
    select
        labeled_count.pairs_labeled,
        counts.pairs_scored,
        counts.merged,
        counts.true_merges,
        counts.false_merges,
        counts.missed_duplicates,
        round(counts.true_merges::numeric / nullif(counts.merged, 0), 3) as precision_score,
        round(counts.true_merges::numeric / nullif(counts.duplicates, 0), 3) as recall_score
    from labeled_count
    cross join counts
)

select * from final
