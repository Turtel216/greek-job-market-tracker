-- Each posting has exactly one current version in the snapshot
select posting_key
from {{ ref('snap_postings') }}
group by posting_key
having count(*) filter (where dbt_valid_to is null) <> 1
