-- The most common locations no city alias matches. Add them to
-- city_aliases.csv.
select
    location_normalized,
    example_location,
    postings,
    postings_today
from qa.qa_unmapped_locations
order by postings desc, location_normalized asc
limit 20
