{{
  config(
    alias=mlt_stream_table_name('series'),
    tags=['silver', 'str', 'str_community'],
    partition_by={'field': 'key_date', 'data_type': 'date', 'granularity': 'month'},
    cluster_by=['source_view', 'block', 'entity'],
  )
}}

-- Latest value per STR identity. Weekly runs re-fetch overlapping windows, and STR restates
-- recent weeks, so the newest data_as_of wins. Weekly views key their day columns relative to
-- the selected week, so for those the selected week stays part of the identity.
WITH ranked AS (
  SELECT *
  FROM {{ source('mlt_str', 'str_series') }}
  WHERE value IS NOT NULL OR value_text IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY
      comp_set_id,
      source_view,
      block,
      slice_type,
      calc_type,
      key,
      IF(source_view IN ('add_rev_weekly', 'seg_weekly', 'combined_summary_weekly'), selected_date, NULL)
    ORDER BY data_as_of DESC, selected_date DESC, _sdc_extracted_at DESC, _sdc_batched_at DESC
  ) = 1
)

SELECT
  '{{ var("property_id") }}' AS property_id,
  CAST(property_id AS STRING) AS costar_property_id,
  comp_set_id,
  source_view,
  operation,
  block,
  mode,
  selected_date,
  key,
  key_date,
  key_grain,
  slice_type,
  label,
  entity,
  segment,
  calc_type,
  metric,
  measure,
  `window`,
  weekday,
  value,
  value_text,
  data_as_of,
  extracted_at
FROM ranked
