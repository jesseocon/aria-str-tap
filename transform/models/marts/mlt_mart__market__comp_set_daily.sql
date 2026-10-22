{{
  config(
    alias=mlt_mart_table_name('market__comp_set_daily'),
    tags=['gold', 'str', 'str_full'],
  )
}}

SELECT
  property_id,
  costar_property_id,
  stay_date,
  `window`,
  hotel_name,
  str_id,
  num_rooms,
  brand_name,
  market,
  submarket,
  comp_set_id,
  comp_set_name,
  supply,
  demand,
  revenue,
  occupancy,
  adr,
  revpar,
  comp_set_supply,
  comp_set_demand,
  comp_set_revenue,
  comp_set_occupancy,
  comp_set_adr,
  comp_set_revpar,
  occupancy_index AS mpi,
  adr_index AS ari,
  revpar_index AS rgi
FROM {{ ref('stg_mlt_str__daily') }}
