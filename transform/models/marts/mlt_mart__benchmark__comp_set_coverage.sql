{{
  config(
    alias=mlt_mart_table_name('benchmark__comp_set_coverage'),
    tags=['gold', 'str', 'str_community'],
  )
}}

-- One row per comp set and month: who is in the set and how many of them reported data.
-- Any index shown for a month whose coverage is incomplete should carry a warning.
WITH response AS (
  SELECT
    comp_set_id,
    period_date AS month_end,
    COUNT(DISTINCT IF(NOT is_subject, costar_property_id, NULL)) AS member_count,
    {% set series_columns = {
      'MonthlySubmitted': 'monthly_submitted_count',
      'DailySubmitted': 'daily_submitted_count',
      'MonthlySegSubmitted': 'monthly_seg_submitted_count',
      'DailySegSubmitted': 'daily_seg_submitted_count',
      'MonthlyAddRevSubmitted': 'monthly_add_rev_submitted_count',
      'DailyAddRevSubmitted': 'daily_add_rev_submitted_count',
    } %}
    {% for series, column in series_columns.items() %}
    COUNT(DISTINCT IF(NOT is_subject AND series_name = '{{ series }}' AND numeric_value = 1, costar_property_id, NULL))
      AS {{ column }},
    {% endfor %}
    LOGICAL_AND(IF(is_subject AND series_name = 'MonthlySubmitted', numeric_value = 1, NULL))
      AS subject_monthly_submitted,
    LOGICAL_AND(IF(is_subject AND series_name = 'DailySubmitted', numeric_value = 1, NULL))
      AS subject_daily_submitted
  FROM {{ ref('stg_mlt_str__comp_set_response') }}
  GROUP BY comp_set_id, month_end
),

sample AS (
  SELECT
    key_date AS month_end,
    comp_set_id,
    MAX(IF(metric = 'sample_property_count', value, NULL)) AS sample_property_count,
    MAX(IF(metric = 'open_property_count', value, NULL)) AS open_property_count,
    MAX(IF(metric = 'sample_room_count', value, NULL)) AS sample_room_count,
    MAX(IF(metric = 'open_room_count', value, NULL)) AS open_room_count,
    MAX(IF(metric = 'participation_pct', value, NULL)) AS participation_pct
  FROM {{ ref('stg_mlt_str__series') }}
  WHERE source_view = 'summary' AND entity = 'comp_set' AND `window` = 'period' AND measure = 'value'
  GROUP BY month_end, comp_set_id
)

SELECT
  '{{ var("property_id") }}' AS property_id,
  '{{ var("costar_property_id") }}' AS costar_property_id,
  r.comp_set_id,
  r.month_end,
  DATE_TRUNC(r.month_end, MONTH) AS month_start,
  r.member_count,
  r.monthly_submitted_count,
  r.daily_submitted_count,
  r.monthly_seg_submitted_count,
  r.daily_seg_submitted_count,
  r.monthly_add_rev_submitted_count,
  r.daily_add_rev_submitted_count,
  SAFE_DIVIDE(r.monthly_submitted_count, r.member_count) AS monthly_coverage,
  SAFE_DIVIDE(r.daily_submitted_count, r.member_count) AS daily_coverage,
  SAFE_DIVIDE(r.monthly_seg_submitted_count, r.member_count) AS segment_coverage,
  r.subject_monthly_submitted,
  r.subject_daily_submitted,
  s.sample_property_count,
  s.open_property_count,
  s.sample_room_count,
  s.open_room_count,
  s.participation_pct,
  r.member_count > 0 AND r.monthly_submitted_count = r.member_count AS is_complete
FROM response AS r
LEFT JOIN sample AS s USING (month_end, comp_set_id)
