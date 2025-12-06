# jobtracker-ingest

Fetches job postings from the sources in `config/sources.yml`, stores raw
payloads in the object store and loads parsed rows into `raw.raw_postings`.
