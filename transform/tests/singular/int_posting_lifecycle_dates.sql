-- A posting has a closed_date exactly when it is closed, and it closes
-- after it was last seen, which is not before it was first seen
select
    posting_key,
    first_seen,
    last_seen,
    is_open,
    closed_date
from {{ ref('int_posting_lifecycle') }}
where
    is_open = (closed_date is not null)
    or closed_date <= last_seen
    or last_seen < first_seen
