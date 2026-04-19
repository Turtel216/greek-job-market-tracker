-- Skills found in each posting, one row per posting and skill. Aliases
-- from the skill_aliases seed are matched on the normalized title and
-- description. See "Skill extraction" in the design doc.

with

postings as (
    select * from {{ ref('stg_postings') }}
),

skill_aliases as (
    select * from {{ ref('skill_aliases') }}
),

-- One regex per alias. Most aliases are a plain word with a boundary that
-- lets c++, c# and .net count as words, but keeps java out of javascript.
-- Postgres \m and \M would break on those. Regex characters in the alias
-- are escaped. Ambiguous names such as go and r bring their own pattern.
alias_patterns as (
    select
        skill_id,
        coalesce(
            nullif(pattern_override, ''),
            '(^|[^a-z0-9+#.])'
            || regexp_replace(alias, '([.+*?^$(){}|\[\]\\])', '\\\1', 'g')
            || '($|[^a-z0-9+#])'
        ) as pattern
    from skill_aliases
),

-- Each pattern is tried on every posting before the next one. Postgres
-- keeps only a few compiled regexes, so going pattern by pattern compiles
-- each one once. offset 0 stops the planner from turning the lateral
-- subquery into a plain join, which could go posting by posting instead.
found as (
    select
        alias_patterns.skill_id,
        alias_matches.posting_key,
        alias_matches.in_title
    from alias_patterns
    cross join lateral (
        select
            postings.posting_key,
            coalesce(postings.title_normalized ~ alias_patterns.pattern, false) as in_title
        from postings
        where
            postings.title_normalized ~ alias_patterns.pattern
            or postings.description_text ~ alias_patterns.pattern
        offset 0  -- noqa: AM09
    ) as alias_matches
),

-- A skill can have several aliases in one posting. The title wins.
final as (
    select
        posting_key,
        skill_id,
        case when bool_or(in_title) then 'title' else 'description' end as matched_in
    from found
    group by posting_key, skill_id
)

select * from final
