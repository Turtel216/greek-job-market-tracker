-- A posting has a closed_date exactly when it is closed, it closes after
-- it was last seen, which is not before it was first seen, and it opened
-- on or before the day it was first seen
select
    posting_key,
    opened_date,
    first_seen,
    last_seen,
    is_open,
    closed_date
from {{ ref('int_posting_lifecycle') }}
where
    is_open = (closed_date is not null)
    or closed_date <= last_seen
    or last_seen < first_seen
    or opened_date > first_seen
