{{
  config(
    alias=mlt_mart_table_name('benchmark__week'),
    tags=['gold', 'str'],
  )
}}

{% set rate_metrics = ['occupancy', 'adr', 'revpar'] %}

-- One row per STR week (Sunday to Saturday) and comp set: STR's own seven-day summary of the
-- hotel against its comp set, with STR's official ranks ("6th of 7") and year-over-year changes.
-- The weekly mirror of mlt_mart__benchmark__period (month window, comp set benchmark).
--
-- Source: the Combined Summary weekly view, block totalCompSet. STR keys it by day, and each
-- day's value covers the seven days ending that day. The Saturday row is the STR week, so only
-- Saturdays are kept. A day appears in two selected weeks (each pull covers nine days); the newest
-- data_as_of wins, because STR restates recent weeks. Indexes are computed as hotel / comp set x
-- 100 like the period mart (a test checks them against the index STR reports).
WITH seven_day AS (
  SELECT
    *,
    -- Vendor label "CompSet - Total": the period mart's label is "CompSet".
    REGEXP_REPLACE(label, r'\s*-\s*Total$', '') AS clean_label
  FROM {{ ref('stg_mlt_str__series') }}
  WHERE source_view = 'combined_summary_weekly'
    AND block = 'CombinedSummaryTableData.totalCompSet'
    AND key_date IS NOT NULL
    AND EXTRACT(DAYOFWEEK FROM key_date) = 7
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY comp_set_id, key_date, entity, metric, measure, calc_type, slice_type
    ORDER BY data_as_of DESC, selected_date DESC, extracted_at DESC
  ) = 1
),

week AS (
  SELECT
    DATE_SUB(key_date, INTERVAL 6 DAY) AS str_week_start,
    key_date AS str_week_end,
    comp_set_id,
    'comp_set' AS benchmark,
    MAX(IF(entity = 'comp_set', clean_label, NULL)) AS benchmark_label,
    {% for m in rate_metrics %}
    {{ str_pick('subject_property', m) }} AS subject_{{ m }},
    {{ str_pick('comp_set', m) }} AS benchmark_{{ m }},
    {{ str_pick('subject_property', m, 'change_yoy') }} AS subject_{{ m }}_yoy,
    {{ str_pick('comp_set', m, 'change_yoy') }} AS benchmark_{{ m }}_yoy,
    {{ str_pick('comp_set', m, 'rank') }} AS {{ m }}_rank,
    {{ str_pick('comp_set', m, 'rank_change_yoy') }} AS {{ m }}_yoy_rank,
    {% endfor %}
    {{ str_pick('comp_set', none, 'rank_property_count') }} AS rank_property_count,
    {{ str_pick('comp_set', none, 'rank_property_count_yoy') }} AS rank_property_count_yoy,
    {{ str_checks_ok(['comp_set', 'index']) }} AS data_ok,
    {{ str_checks_ok(['comp_set', 'index'], yoy=true) }} AS yoy_data_ok,
    MAX(data_as_of) AS data_as_of
  FROM seven_day
  GROUP BY str_week_start, str_week_end, comp_set_id
),

subject_rooms AS (
  SELECT comp_set_id, num_rooms
  FROM {{ ref('stg_mlt_str__comp_set_members') }}
  WHERE is_subject AND is_current
)

SELECT
  '{{ var("property_id") }}' AS property_id,
  '{{ var("costar_property_id") }}' AS costar_property_id,
  w.str_week_start,
  w.str_week_end,
  w.comp_set_id,
  w.benchmark,
  w.benchmark_label,
  {% for m in rate_metrics %}
  w.subject_{{ m }},
  w.benchmark_{{ m }},
  SAFE_DIVIDE(w.subject_{{ m }}, w.benchmark_{{ m }}) * 100 AS {{ str_index_name(m) }},
  -- Index change year over year, from the two growth rates.
  SAFE_DIVIDE(1 + w.subject_{{ m }}_yoy, 1 + w.benchmark_{{ m }}_yoy) - 1 AS {{ str_index_name(m) }}_yoy,
  {% endfor %}
  {% for m in rate_metrics %}
  w.subject_{{ m }}_yoy,
  w.benchmark_{{ m }}_yoy,
  {% endfor %}
  w.occupancy_rank,
  w.adr_rank,
  w.revpar_rank,
  w.rank_property_count,
  w.occupancy_yoy_rank,
  w.adr_yoy_rank,
  w.revpar_yoy_rank,
  w.rank_property_count_yoy,
  w.data_ok,
  w.yoy_data_ok,
  7 AS days_in_window,
  rooms.num_rooms,
  -- Estimates: current room count times seven nights. STR does not publish the hotel's own supply here.
  rooms.num_rooms * 7 AS subject_supply_est,
  w.subject_revpar * rooms.num_rooms * 7 AS subject_room_revenue_est,
  -- Positive: room revenue the hotel would have added by matching the comp set's RevPAR.
  (w.benchmark_revpar - w.subject_revpar) * rooms.num_rooms * 7 AS revenue_to_fair_share,
  w.data_as_of
FROM week AS w
LEFT JOIN subject_rooms AS rooms USING (comp_set_id)
