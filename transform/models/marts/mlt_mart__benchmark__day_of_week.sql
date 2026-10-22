{{
  config(
    alias=mlt_mart_table_name('benchmark__day_of_week'),
    tags=['gold', 'str', 'str_community'],
  )
}}

{% set metrics = ['occupancy', 'adr', 'revpar'] %}

-- One row per month, window, weekday (monday..sunday, weekday, weekend, all) and benchmark
-- (comp set or industry). Finds structural weak days: a weekday whose index stays low over
-- the 3- and 12-month windows needs a strategy change, not a nightly rate move.
WITH dow AS (
  SELECT
    *,
    IF(source_view = 'day_of_week_comp_set', 'comp_set', 'industry') AS benchmark
  FROM {{ ref('stg_mlt_str__series') }}
  WHERE source_view IN ('day_of_week_comp_set', 'day_of_week_industry')
    AND key_date IS NOT NULL
    AND segment IS NULL
)

SELECT
  '{{ var("property_id") }}' AS property_id,
  '{{ var("costar_property_id") }}' AS costar_property_id,
  comp_set_id,
  key_date AS month_end,
  DATE_TRUNC(key_date, MONTH) AS month_start,
  {{ str_window_name('`window`') }} AS window_name,
  COALESCE(weekday, 'all') AS weekday,
  benchmark,
  MAX(IF(entity IN ('comp_set', 'industry') AND measure = 'value', label, NULL)) AS benchmark_label,
  {% for m in metrics %}
  {{ str_pick('subject_property', m) }} AS subject_{{ m }},
  {{ str_pick('subject_property', m, 'change_yoy') }} AS subject_{{ m }}_yoy,
  MAX(IF(entity IN ('comp_set', 'industry') AND metric = '{{ m }}' AND measure = 'value', value, NULL))
    AS benchmark_{{ m }},
  MAX(IF(entity IN ('comp_set', 'industry') AND metric = '{{ m }}' AND measure = 'change_yoy', value, NULL))
    AS benchmark_{{ m }}_yoy,
  {{ str_pick('index', m, scale=100) }} AS {{ str_index_name(m) }},
  {{ str_pick('index', m, 'change_yoy') }} AS {{ str_index_name(m) }}_yoy,
  {% endfor %}
  {{ str_checks_ok(['comp_set', 'industry', 'index']) }} AS data_ok,
  MAX(data_as_of) AS data_as_of
FROM dow
GROUP BY comp_set_id, month_end, month_start, window_name, weekday, benchmark
