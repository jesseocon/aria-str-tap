{{
  config(
    alias=mlt_mart_table_name('benchmark__segment'),
    tags=['gold', 'str'],
  )
}}

{% set metrics = ['occupancy', 'adr', 'revpar'] %}

-- One row per month, window, segment (total, transient, group, contract) and benchmark
-- (comp set or industry). Answers "is it us or the market?" for a segment: compare the hotel's
-- year-over-year change with the benchmark's, and read the index.
-- Comp set rows come from STR's Seg Index view (it carries indexes and ranks); industry rows from
-- the Seg view, with the index computed.
WITH seg AS (
  SELECT
    *,
    IF(source_view = 'seg_index', 'comp_set', 'industry') AS benchmark
  FROM {{ ref('stg_mlt_str__series') }}
  WHERE source_view IN ('seg_index', 'seg_industry')
    AND key_date IS NOT NULL
    AND weekday IS NULL
    AND segment IS NOT NULL
),

pivot AS (
  SELECT
    key_date AS month_end,
    {{ str_window_name('`window`') }} AS window_name,
    comp_set_id,
    segment,
    benchmark,
    MAX(IF(entity IN ('comp_set', 'industry') AND measure = 'value', label, NULL)) AS benchmark_label,
    {% for m in metrics %}
    {{ str_pick('subject_property', m) }} AS subject_{{ m }},
    {{ str_pick('subject_property', m, 'change_yoy') }} AS subject_{{ m }}_yoy,
    MAX(IF(entity IN ('comp_set', 'industry') AND metric = '{{ m }}' AND measure = 'value', value, NULL))
      AS benchmark_{{ m }},
    MAX(IF(entity IN ('comp_set', 'industry') AND metric = '{{ m }}' AND measure = 'change_yoy', value, NULL))
      AS benchmark_{{ m }}_yoy,
    {{ str_pick('index', m, scale=100) }} AS reported_{{ str_index_name(m) }},
    {{ str_pick('index', m, 'change_yoy') }} AS reported_{{ str_index_name(m) }}_yoy,
    {{ str_pick('comp_set', m, 'rank') }} AS {{ m }}_rank,
    {% endfor %}
    {{ str_pick('comp_set', none, 'rank_property_count') }} AS rank_property_count,
    {{ str_checks_ok(['comp_set', 'industry', 'index']) }} AS data_ok,
    {{ str_checks_ok(['comp_set', 'industry', 'index'], yoy=true) }} AS yoy_data_ok,
    MAX(data_as_of) AS data_as_of
  FROM seg
  GROUP BY month_end, window_name, comp_set_id, segment, benchmark
)

SELECT
  '{{ var("property_id") }}' AS property_id,
  '{{ var("costar_property_id") }}' AS costar_property_id,
  comp_set_id,
  month_end,
  DATE_TRUNC(month_end, MONTH) AS month_start,
  window_name,
  {{ str_window_start('month_end', 'window_name') }} AS window_start,
  segment,
  benchmark,
  benchmark_label,
  {% for m in metrics %}
  subject_{{ m }},
  subject_{{ m }}_yoy,
  benchmark_{{ m }},
  benchmark_{{ m }}_yoy,
  COALESCE(reported_{{ str_index_name(m) }}, SAFE_DIVIDE(subject_{{ m }}, benchmark_{{ m }}) * 100)
    AS {{ str_index_name(m) }},
  COALESCE(
    reported_{{ str_index_name(m) }}_yoy,
    SAFE_DIVIDE(1 + subject_{{ m }}_yoy, 1 + benchmark_{{ m }}_yoy) - 1
  ) AS {{ str_index_name(m) }}_yoy,
  {{ m }}_rank,
  {% endfor %}
  rank_property_count,
  -- STR withholds comp set segment figures when too few comp set hotels report segmentation;
  -- then only the industry benchmark answers "us or the market?" for that segment.
  benchmark_revpar IS NOT NULL AS benchmark_available,
  -- Share read in one word: did the hotel's segment grow faster or slower than the benchmark's?
  CASE
    WHEN subject_revpar_yoy IS NULL OR benchmark_revpar_yoy IS NULL THEN NULL
    WHEN subject_revpar_yoy >= benchmark_revpar_yoy THEN 'gaining'
    ELSE 'losing'
  END AS revpar_share_direction,
  data_ok,
  yoy_data_ok,
  data_as_of
FROM pivot
