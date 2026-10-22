{{
  config(
    alias=mlt_stream_table_name('daily'),
    tags=['silver', 'str', 'str_full'],
  )
}}

WITH raw AS (
  SELECT *
  FROM {{ source('mlt_str', 'daily') }}
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY property_id, stay_date, `window`
    ORDER BY _sdc_extracted_at DESC, _sdc_received_at DESC, _sdc_batched_at DESC
  ) = 1
)

SELECT
  '{{ var("property_id") }}' AS property_id,
  CAST(property_id AS STRING) AS costar_property_id,
  SAFE_CAST(stay_date AS DATE) AS stay_date,
  `window`,
  SAFE_CAST(begin_date AS DATE) AS begin_date,
  SAFE_CAST(end_date AS DATE) AS end_date,
  hotel_name,
  CAST(str_id AS STRING) AS str_id,
  SAFE_CAST(num_rooms AS INT64) AS num_rooms,
  brand_name,
  market,
  submarket,
  address,
  owner_name,
  operator_name,
  CAST(comp_set_id AS STRING) AS comp_set_id,
  comp_set_name,
  SAFE_CAST(supply AS FLOAT64) AS supply,
  SAFE_CAST(demand AS FLOAT64) AS demand,
  SAFE_CAST(revenue AS FLOAT64) AS revenue,
  SAFE_CAST(occupancy AS FLOAT64) AS occupancy,
  SAFE_CAST(adr AS FLOAT64) AS adr,
  SAFE_CAST(revpar AS FLOAT64) AS revpar,
  SAFE_CAST(comp_set_supply AS FLOAT64) AS comp_set_supply,
  SAFE_CAST(comp_set_demand AS FLOAT64) AS comp_set_demand,
  SAFE_CAST(comp_set_revenue AS FLOAT64) AS comp_set_revenue,
  SAFE_CAST(comp_set_occupancy AS FLOAT64) AS comp_set_occupancy,
  SAFE_CAST(comp_set_adr AS FLOAT64) AS comp_set_adr,
  SAFE_CAST(comp_set_revpar AS FLOAT64) AS comp_set_revpar,
  SAFE_CAST(occupancy_index AS FLOAT64) AS occupancy_index,
  SAFE_CAST(adr_index AS FLOAT64) AS adr_index,
  SAFE_CAST(revpar_index AS FLOAT64) AS revpar_index
FROM raw
