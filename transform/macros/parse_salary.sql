-- Turns salary text into a monthly range. See "Salary" in the design doc.
-- Use it in a lateral join on the column with the salary text, as in
-- "left join lateral (parse_salary(...)) as salary on true" with the
-- macro call in double curly braces.
--
-- It gives salary_min_monthly, salary_max_monthly, salary_currency,
-- salary_basis and salary_period, all null when the text has no amount.
-- The two amounts are also null when the period is weekly, daily or
-- hourly, since those cannot be turned into a month, and when either
-- monthly amount is outside the salary_monthly_min and _max vars.
--
-- Each step below is a lateral subquery that reads the ones before it.
{% macro parse_salary(column) -%}

    {%- set currencies = 'eur|usd|gbp|chf|pln|ron|bgn|czk|huf|sek|nok|dkk' -%}
    {#- One or more digits, with . or , between groups -#}
    {%- set amount = "'\\d+([.,]\\d+)*'" -%}
    {#- A dot or comma followed by exactly 3 digits separates thousands -#}
    {%- set thousands = "'[.,](?=\\d{3}(\\D|$))'" -%}

    select
        case when bounds.in_bounds then monthly.low end as salary_min_monthly,
        case when bounds.in_bounds then monthly.high end as salary_max_monthly,
        case when amounts.low is not null then labels.currency end as salary_currency,
        case when amounts.low is not null then labels.basis end as salary_basis,
        periods.period as salary_period
    from
        -- Only the first pay range when there are several, as in "a; b"
        (select split_part({{ column }}, ';', 1) as first_range) as texts
    cross join lateral (
        select
            {{ normalize_text('texts.first_range') }} as words,
            -- Amounts are read after the last colon, so the 2 in
            -- "Level 2: 40000 EUR" is skipped, unless nothing is left there
            lower(
                case
                    when regexp_replace(texts.first_range, '^.*:', '') ~ '\d'
                        then regexp_replace(texts.first_range, '^.*:', '')
                    else texts.first_range
                end
            ) as amount_text
    ) as normalized
    cross join lateral (
        select
            coalesce(
                upper(substring(
                    normalized.words
                    from '(?:^|[^a-z0-9α-ω])({{ currencies }})(?:$|[^a-z0-9α-ω])'
                )),
                case
                    when texts.first_range like '%€%' then 'EUR'
                    when normalized.words ~ {{ whole_words("'ευρω|euros?'") }} then 'EUR'
                    when texts.first_range like '%$%' then 'USD'
                    when texts.first_range like '%£%' then 'GBP'
                end,
                'EUR'
            ) as currency,
            case
                when normalized.words ~ {{ whole_words("'μικτ[α-ω]*|gross|brutto'") }} then 'gross'
                when normalized.words ~ {{ whole_words("'καθαρ[α-ω]*|net|netto'") }} then 'net'
                else 'unknown'
            end as basis
    ) as labels
    cross join lateral (
        -- Thousand separators dropped and a decimal comma made a dot
        select
            replace(regexp_replace(
                regexp_substr(normalized.amount_text, {{ amount }}, 1, 1), {{ thousands }}, '', 'g'
            ), ',', '.') as first_text,
            replace(regexp_replace(
                regexp_substr(normalized.amount_text, {{ amount }}, 1, 2), {{ thousands }}, '', 'g'
            ), ',', '.') as second_text,
            -- A k after any amount, as in 25-30k, means thousands
            normalized.amount_text ~ '\d ?k([^a-z]|$)' as in_thousands
    ) as found
    cross join lateral (
        select
            -- Anything still odd, such as 1.2.3, is not an amount
            case
                when found.first_text ~ '^\d+(\.\d+)?$' then found.first_text::numeric
            end as first_amount,
            case
                when found.second_text ~ '^\d+(\.\d+)?$' then found.second_text::numeric
            end as second_amount
    ) as numbers
    cross join lateral (
        -- least and greatest skip nulls, so one amount gives low = high
        select
            least(scaled.first_amount, scaled.second_amount) as low,
            greatest(scaled.first_amount, scaled.second_amount) as high
        from (
            select
                case
                    when found.in_thousands and numbers.first_amount < 1000
                        then numbers.first_amount * 1000
                    else numbers.first_amount
                end as first_amount,
                case
                    when found.in_thousands and numbers.second_amount < 1000
                        then numbers.second_amount * 1000
                    else numbers.second_amount
                end as second_amount
        ) as scaled
    ) as amounts
    cross join lateral (
        select
            case
                when amounts.low is null then null
                -- Period keywords, tried in this order
                when normalized.words ~ {{ whole_words(
                    "'ετησι[α-ω]*|ετοσ|annual(ly)?|year(ly)?|per annum|p\.a\.?'"
                ) }} then 'annual'
                when normalized.words ~ {{ whole_words(
                    "'μηνιαι[α-ω]*|μην(α|οσ|ασ)|monthly|month'"
                ) }} then 'monthly'
                when normalized.words ~ {{ whole_words("'εβδομαδ[α-ω]*|week(ly)?'") }} then 'weekly'
                when normalized.words ~ {{ whole_words(
                    "'ημερ(α|ασ|ησι[α-ω]*|ομισθι[α-ω]*)|day|daily'"
                ) }} then 'daily'
                when normalized.words ~ {{ whole_words("'ωρ(α|ασ|ιαι[α-ω]*)|hour(ly)?'") }} then 'hourly'
                -- With no keyword, the size of the amount decides
                when amounts.high >= {{ var('salary_annual_threshold') }} then 'annual'
                else 'monthly'
            end as period
    ) as periods
    cross join lateral (
        select
            round(amounts.low / divisors.divisor, 2) as low,
            round(amounts.high / divisors.divisor, 2) as high
        from (
            select
                case periods.period
                    when 'annual' then {{ var('salaries_per_year') }}
                    when 'monthly' then 1
                end as divisor
        ) as divisors
    ) as monthly
    cross join lateral (
        select
            monthly.low >= {{ var('salary_monthly_min') }}
            and monthly.high <= {{ var('salary_monthly_max') }} as in_bounds
    ) as bounds

{%- endmacro %}
