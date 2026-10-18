-- The most common salary texts that gave no monthly amount, with why
select
    salary_text,
    reason,
    postings,
    postings_today
from qa.qa_salary_parse_failures
order by postings desc, salary_text asc
limit 20
