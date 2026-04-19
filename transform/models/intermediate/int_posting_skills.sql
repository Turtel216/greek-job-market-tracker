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
        -- Text a posting must contain for the regex to match. An override
        -- has no single word to look for.
        case when nullif(pattern_override, '') is null then alias end as hint,
        coalesce(
            nullif(pattern_override, ''),
            '(^|[^a-z0-9+#.])'
            || regexp_replace(alias, '([.+*?^$(){}|\[\]\\])', '\\\1', 'g')
            || '($|[^a-z0-9+#])'
        ) as pattern
    from skill_aliases
),

-- Postgres keeps only a few compiled regexes, so running every alias regex
-- on every posting would compile them again and again. A cheap strpos
-- check runs first, and the regex only where the hint is found, whatever
-- order the planner picks. The case makes sure of that. Null means no match.
found as (
    select
        postings.posting_key,
        alias_patterns.skill_id,
        case
            when
                alias_patterns.hint is null
                or strpos(postings.title_normalized, alias_patterns.hint) > 0
                then postings.title_normalized ~ alias_patterns.pattern
        end as in_title,
        case
            when
                alias_patterns.hint is null
                or strpos(postings.description_text, alias_patterns.hint) > 0
                then postings.description_text ~ alias_patterns.pattern
        end as in_description
    from postings
    cross join alias_patterns
),

-- A skill can have several aliases in one posting. The title wins.
final as (
    select
        posting_key,
        skill_id,
        case when bool_or(in_title) then 'title' else 'description' end as matched_in
    from found
    where in_title or in_description
    group by posting_key, skill_id
)

select * from final
