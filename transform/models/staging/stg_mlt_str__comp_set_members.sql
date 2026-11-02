{{
  config(
    alias=mlt_stream_table_name('comp_set_members'),
    tags=['silver', 'str'],
  )
}}

-- Latest details per comp set hotel, plus when it was first and last seen in the set, so a
-- hotel leaving or joining the comp set is visible (is_current = in the most recent run).
-- A run is its load batch: each row's _sdc_extracted_at differs by microseconds within one run,
-- so comparing those would leave only the last-extracted hotel current.
WITH runs AS (
  SELECT
    *,
    MIN(_sdc_extracted_at) OVER (PARTITION BY comp_set_id, property_id) AS first_seen_at,
    MAX(_sdc_extracted_at) OVER (PARTITION BY comp_set_id, property_id) AS last_seen_at,
    MAX(_sdc_batched_at) OVER (PARTITION BY comp_set_id, property_id) AS last_seen_run,
    MAX(_sdc_batched_at) OVER (PARTITION BY comp_set_id) AS latest_run
  FROM {{ source('mlt_str', 'comp_set_members') }}
),

latest AS (
  SELECT *
  FROM runs
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY comp_set_id, property_id
    ORDER BY _sdc_extracted_at DESC, _sdc_batched_at DESC
  ) = 1
)

SELECT
  '{{ var("property_id") }}' AS aria_property_id,
  '{{ var("costar_property_id") }}' AS subject_costar_property_id,
  comp_set_id,
  comp_set_name,
  comp_set_type,
  property_id AS costar_property_id,
  is_subject,
  str_id,
  hotel_name,
  brand_name,
  parent_company,
  owners,
  operators,
  num_rooms,
  hotel_class,
  market,
  submarket,
  address,
  city,
  state,
  postal_code,
  country_code,
  latitude,
  longitude,
  open_date,
  operating_status,
  first_seen_at,
  last_seen_at,
  last_seen_run = latest_run AS is_current
FROM latest
