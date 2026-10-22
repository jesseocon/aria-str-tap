{{
  config(
    alias=mlt_mart_table_name('benchmark__period'),
    tags=['gold', 'str', 'str_community'],
  )
}}

{% set rate_metrics = ['occupancy', 'adr', 'revpar'] %}
{% set yoy_metrics = ['occupancy', 'adr', 'revpar', 'supply', 'demand', 'revenue'] %}
{% set count_metrics = ['open_property_count', 'open_room_count', 'sample_property_count', 'sample_room_count', 'participation_pct'] %}

-- One row per month, window (month, 3mo, 12mo, ytd) and benchmark (comp set, market, market
-- class, submarket, submarket class): the hotel against each benchmark, with indexes and the
-- room revenue gap to fair share. STR's Summary view supplies values; indexes are computed
-- (STR gives none for market-level benchmarks), and comp set ranks come from the Monthly view.
WITH summary AS (
  SELECT *
  FROM {{ ref('stg_mlt_str__series') }}
  WHERE source_view = 'summary'
    AND key_date IS NOT NULL
    AND weekday IS NULL
    AND segment IS NULL
),

subject AS (
  SELECT
    key_date AS month_end,
    {{ str_window_name('`window`') }} AS window_name,
    comp_set_id,
    {% for m in rate_metrics %}
    {{ str_pick('subject_property', m) }} AS subject_{{ m }},
    {% endfor %}
    {% for m in yoy_metrics %}
    {{ str_pick('subject_property', m, 'change_yoy') }} AS subject_{{ m }}_yoy{% if not loop.last %},{% endif %}
    {% endfor %}
  FROM summary
  WHERE entity = 'subject_property'
  GROUP BY month_end, window_name, comp_set_id
),

benchmark AS (
  SELECT
    key_date AS month_end,
    {{ str_window_name('`window`') }} AS window_name,
    comp_set_id,
    entity AS benchmark,
    MAX(IF(measure = 'value' AND metric = 'occupancy', label, NULL)) AS benchmark_label,
    {% for m in rate_metrics %}
    MAX(IF(metric = '{{ m }}' AND measure = 'value', value, NULL)) AS benchmark_{{ m }},
    {% endfor %}
    {% for m in yoy_metrics %}
    MAX(IF(metric = '{{ m }}' AND measure = 'change_yoy', value, NULL)) AS benchmark_{{ m }}_yoy,
    {% endfor %}
    {% for m in count_metrics %}
    MAX(IF(metric = '{{ m }}' AND measure = 'value', value, NULL)) AS benchmark_{{ m }},
    {% endfor %}
    MIN(IF(measure = 'check' AND NOT (calc_type LIKE '%GrowthYoy%' OR calc_type LIKE '%Change%'), value, NULL)) = 1
      AS data_ok,
    MIN(IF(measure = 'check' AND (calc_type LIKE '%GrowthYoy%' OR calc_type LIKE '%Change%'), value, NULL)) = 1
      AS yoy_data_ok,
    MAX(data_as_of) AS data_as_of
  FROM summary
  WHERE entity IN ('comp_set', 'market', 'market_class', 'submarket', 'submarket_class')
  GROUP BY month_end, window_name, comp_set_id, benchmark
),

comp_set_ranks AS (
  SELECT
    key_date AS month_end,
    'month' AS window_name,
    comp_set_id,
    'comp_set' AS benchmark,
    {% for m in rate_metrics %}
    {{ str_pick('comp_set', m, 'rank') }} AS {{ m }}_rank,
    {% endfor %}
    {{ str_pick('comp_set', none, 'rank_property_count') }} AS rank_property_count
  FROM {{ ref('stg_mlt_str__series') }}
  WHERE source_view = 'monthly' AND block = 'MonthlyTableData.compSet' AND key_date IS NOT NULL
  GROUP BY month_end, comp_set_id
),

subject_rooms AS (
  SELECT comp_set_id, num_rooms
  FROM {{ ref('stg_mlt_str__comp_set_members') }}
  WHERE is_subject AND is_current
),

joined AS (
  SELECT
    b.month_end,
    b.window_name,
    {{ str_window_start('b.month_end', 'b.window_name') }} AS window_start,
    b.comp_set_id,
    b.benchmark,
    b.benchmark_label,
    {% for m in rate_metrics %}
    s.subject_{{ m }},
    b.benchmark_{{ m }},
    SAFE_DIVIDE(s.subject_{{ m }}, b.benchmark_{{ m }}) * 100 AS {{ str_index_name(m) }},
    -- Index change year over year, from the two growth rates.
    SAFE_DIVIDE(1 + s.subject_{{ m }}_yoy, 1 + b.benchmark_{{ m }}_yoy) - 1 AS {{ str_index_name(m) }}_yoy,
    {% endfor %}
    {% for m in yoy_metrics %}
    s.subject_{{ m }}_yoy,
    b.benchmark_{{ m }}_yoy,
    {% endfor %}
    r.occupancy_rank,
    r.adr_rank,
    r.revpar_rank,
    r.rank_property_count,
    {% for m in count_metrics %}
    b.benchmark_{{ m }},
    {% endfor %}
    b.data_ok,
    b.yoy_data_ok,
    rooms.num_rooms,
    b.data_as_of
  FROM benchmark AS b
  JOIN subject AS s USING (month_end, window_name, comp_set_id)
  LEFT JOIN comp_set_ranks AS r USING (month_end, window_name, comp_set_id, benchmark)
  LEFT JOIN subject_rooms AS rooms USING (comp_set_id)
)

SELECT
  '{{ var("property_id") }}' AS property_id,
  '{{ var("costar_property_id") }}' AS costar_property_id,
  joined.* EXCEPT (num_rooms, data_as_of),
  DATE_TRUNC(month_end, MONTH) AS month_start,
  DATE_DIFF(month_end, window_start, DAY) + 1 AS days_in_window,
  num_rooms,
  -- Estimates: current room count times days. STR does not publish the hotel's own supply here.
  num_rooms * (DATE_DIFF(month_end, window_start, DAY) + 1) AS subject_supply_est,
  subject_revpar * num_rooms * (DATE_DIFF(month_end, window_start, DAY) + 1) AS subject_room_revenue_est,
  -- Positive: room revenue the hotel would have added by matching the benchmark's RevPAR.
  (benchmark_revpar - subject_revpar) * num_rooms * (DATE_DIFF(month_end, window_start, DAY) + 1)
    AS revenue_to_fair_share,
  data_as_of
FROM joined
