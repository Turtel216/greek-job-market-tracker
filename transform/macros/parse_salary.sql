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
        case when salary_bounds.in_bounds then salary_monthly.low end as salary_min_monthly,
        case when salary_bounds.in_bounds then salary_monthly.high end as salary_max_monthly,
        case when salary_amounts.low is not null then salary_labels.currency end as salary_currency,
        case when salary_amounts.low is not null then salary_labels.basis end as salary_basis,
        salary_periods.period as salary_period
    from
        -- Only the first pay range when there are several, as in "a; b"
        (select split_part({{ column }}, ';', 1) as first_range) as salary_texts
    cross join lateral (
        select
            {{ normalize_text('salary_texts.first_range') }} as words,
            -- Amounts are read after the last colon, so the 2 in
            -- "Level 2: 40000 EUR" is skipped, unless nothing is left there
            lower(
                case
                    when regexp_replace(salary_texts.first_range, '^.*:', '') ~ '\d'
                        then regexp_replace(salary_texts.first_range, '^.*:', '')
                    else salary_texts.first_range
                end
            ) as amount_text
    ) as salary_normalized
    cross join lateral (
        select
            coalesce(
                upper(substring(
                    salary_normalized.words
                    from '(?:^|[^a-z0-9α-ω])({{ currencies }})(?:$|[^a-z0-9α-ω])'
                )),
                case
                    when salary_texts.first_range like '%€%' then 'EUR'
                    when salary_normalized.words ~ {{ whole_words("'ευρω|euros?'") }} then 'EUR'
                    when salary_texts.first_range like '%$%' then 'USD'
                    when salary_texts.first_range like '%£%' then 'GBP'
                end,
                'EUR'
            ) as currency,
            case
                when salary_normalized.words ~ {{ whole_words("'μικτ[α-ω]*|gross|brutto'") }} then 'gross'
                when salary_normalized.words ~ {{ whole_words("'καθαρ[α-ω]*|net|netto'") }} then 'net'
                else 'unknown'
            end as basis
    ) as salary_labels
    cross join lateral (
        -- Thousand separators dropped and a decimal comma made a dot
        select
            replace(regexp_replace(
                regexp_substr(salary_normalized.amount_text, {{ amount }}, 1, 1), {{ thousands }}, '', 'g'
            ), ',', '.') as first_text,
            replace(regexp_replace(
                regexp_substr(salary_normalized.amount_text, {{ amount }}, 1, 2), {{ thousands }}, '', 'g'
            ), ',', '.') as second_text,
            -- A k after any amount, as in 25-30k, means thousands
            salary_normalized.amount_text ~ '\d ?k([^a-z]|$)' as in_thousands
    ) as salary_found
    cross join lateral (
        select
            -- Anything still odd, such as 1.2.3, is not an amount
            case
                when salary_found.first_text ~ '^\d+(\.\d+)?$' then salary_found.first_text::numeric
            end as first_amount,
            case
                when salary_found.second_text ~ '^\d+(\.\d+)?$' then salary_found.second_text::numeric
            end as second_amount
    ) as salary_numbers
    cross join lateral (
        -- least and greatest skip nulls, so one amount gives low = high
        select
            least(salary_scaled.first_amount, salary_scaled.second_amount) as low,
            greatest(salary_scaled.first_amount, salary_scaled.second_amount) as high
        from (
            select
                case
                    when salary_found.in_thousands and salary_numbers.first_amount < 1000
                        then salary_numbers.first_amount * 1000
                    else salary_numbers.first_amount
                end as first_amount,
                case
                    when salary_found.in_thousands and salary_numbers.second_amount < 1000
                        then salary_numbers.second_amount * 1000
                    else salary_numbers.second_amount
                end as second_amount
        ) as salary_scaled
    ) as salary_amounts
    cross join lateral (
        select
            case
                when salary_amounts.low is null then null
                -- Period keywords, tried in this order
                when salary_normalized.words ~ {{ whole_words(
                    "'ετησι[α-ω]*|ετοσ|annual(ly)?|year(ly)?|per annum|p\.a\.?'"
                ) }} then 'annual'
                when salary_normalized.words ~ {{ whole_words(
                    "'μηνιαι[α-ω]*|μην(α|οσ|ασ)|monthly|month'"
                ) }} then 'monthly'
                when salary_normalized.words ~ {{ whole_words("'εβδομαδ[α-ω]*|week(ly)?'") }} then 'weekly'
                when salary_normalized.words ~ {{ whole_words(
                    "'ημερ(α|ασ|ησι[α-ω]*|ομισθι[α-ω]*)|day|daily'"
                ) }} then 'daily'
                when salary_normalized.words ~ {{ whole_words("'ωρ(α|ασ|ιαι[α-ω]*)|hour(ly)?'") }} then 'hourly'
                -- With no keyword, the size of the amount decides
                when salary_amounts.high >= {{ var('salary_annual_threshold') }} then 'annual'
                else 'monthly'
            end as period
    ) as salary_periods
    cross join lateral (
        select
            round(salary_amounts.low / salary_divisors.divisor, 2) as low,
            round(salary_amounts.high / salary_divisors.divisor, 2) as high
        from (
            select
                case salary_periods.period
                    when 'annual' then {{ var('salaries_per_year') }}
                    when 'monthly' then 1
                end as divisor
        ) as salary_divisors
    ) as salary_monthly
    cross join lateral (
        select
            salary_monthly.low >= {{ var('salary_monthly_min') }}
            and salary_monthly.high <= {{ var('salary_monthly_max') }} as in_bounds
    ) as salary_bounds

{%- endmacro %}
