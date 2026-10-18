-- The most common titles no role family rule matches. Add rules for the
-- tech roles among them to role_family_rules.csv.
select
    title_normalized,
    example_title,
    postings,
    postings_today
from qa.qa_unclassified_titles
order by postings desc, title_normalized asc
limit 20
