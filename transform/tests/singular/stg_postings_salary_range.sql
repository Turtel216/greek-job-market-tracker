-- The lower monthly salary must not be above the upper one
select
    posting_key,
    salary_min_monthly,
    salary_max_monthly
from {{ ref('stg_postings') }}
where salary_min_monthly > salary_max_monthly
