{{
  config(
    alias=mlt_stream_table_name('comp_set_response'),
    tags=['silver', 'str'],
  )
}}

-- Latest submission status per comp set hotel, series and month. numeric_value 1 = submitted.
SELECT
  '{{ var("property_id") }}' AS aria_property_id,
  comp_set_id,
  property_id AS costar_property_id,
  property_id = '{{ var("costar_property_id") }}' AS is_subject,
  census_id,
  series_name,
  period_date,
  numeric_value,
  string_value,
  _sdc_extracted_at AS extracted_at
FROM {{ source('mlt_str', 'comp_set_response') }}
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY comp_set_id, property_id, series_name, period_date
  ORDER BY _sdc_extracted_at DESC, _sdc_batched_at DESC
) = 1
