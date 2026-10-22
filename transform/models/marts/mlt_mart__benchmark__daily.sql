{{
  config(
    alias=mlt_mart_table_name('benchmark__daily'),
    tags=['gold', 'str', 'str_community'],
    partition_by={'field': 'stay_date', 'data_type': 'date', 'granularity': 'month'},
  )
}}

{% set metrics = ['occupancy', 'adr', 'revpar'] %}

-- One row per stay date: what actually happened, for the hotel, its comp set and the industry.
-- This is the outcome source for Morning Desk flags: join on stay_date to grade a recommendation.
WITH daily AS (
  SELECT *
  FROM {{ ref('stg_mlt_str__series') }}
  WHERE source_view = 'daily'
    AND `window` = 'period'
    AND key_date IS NOT NULL
    AND weekday IS NULL
    AND segment IS NULL
),

comp_set AS (
  SELECT
    key_date AS stay_date,
    comp_set_id,
    {% for m in metrics %}
    {{ str_pick('subject_property', m) }} AS subject_{{ m }},
    {{ str_pick('subject_property', m, 'change_yoy') }} AS subject_{{ m }}_yoy,
    {{ str_pick('comp_set', m) }} AS comp_set_{{ m }},
    {{ str_pick('comp_set', m, 'change_yoy') }} AS comp_set_{{ m }}_yoy,
    {{ str_pick('index', m, scale=100) }} AS {{ str_index_name(m) }},
    {{ str_pick('index', m, 'change_yoy') }} AS {{ str_index_name(m) }}_yoy,
    {{ str_pick('comp_set', m, 'rank') }} AS {{ m }}_rank,
    {% endfor %}
    {{ str_pick('comp_set', none, 'rank_property_count') }} AS rank_property_count,
    {{ str_checks_ok(['comp_set', 'index']) }} AS comp_set_data_ok,
    {{ str_checks_ok(['comp_set', 'index'], yoy=true) }} AS comp_set_yoy_data_ok,
    MAX(data_as_of) AS data_as_of
  FROM daily
  WHERE block = 'DailyTableData.compSetDaily'
  GROUP BY stay_date, comp_set_id
),

industry AS (
  SELECT
    key_date AS stay_date,
    comp_set_id,
    MAX(IF(entity = 'industry', label, NULL)) AS industry_label,
    {% for m in metrics %}
    {{ str_pick('industry', m) }} AS industry_{{ m }},
    {{ str_pick('industry', m, 'change_yoy') }} AS industry_{{ m }}_yoy,
    {{ str_pick('index', m, scale=100) }} AS industry_{{ str_index_name(m) }},
    {% endfor %}
    {{ str_checks_ok(['industry', 'index']) }} AS industry_data_ok
  FROM daily
  WHERE block = 'DailyTableData.industryDaily'
  GROUP BY stay_date, comp_set_id
),

subject_rooms AS (
  SELECT comp_set_id, num_rooms
  FROM {{ ref('stg_mlt_str__comp_set_members') }}
  WHERE is_subject AND is_current
)

SELECT
  '{{ var("property_id") }}' AS property_id,
  '{{ var("costar_property_id") }}' AS costar_property_id,
  c.comp_set_id,
  c.stay_date,
  FORMAT_DATE('%A', c.stay_date) AS weekday,
  DATE_TRUNC(c.stay_date, WEEK(SUNDAY)) AS str_week_start,
  DATE_TRUNC(c.stay_date, MONTH) AS month_start,
  {% for m in metrics %}
  c.subject_{{ m }},
  c.subject_{{ m }}_yoy,
  c.comp_set_{{ m }},
  c.comp_set_{{ m }}_yoy,
  c.{{ str_index_name(m) }},
  c.{{ str_index_name(m) }}_yoy,
  c.{{ m }}_rank,
  {% endfor %}
  c.rank_property_count,
  i.industry_label,
  {% for m in metrics %}
  i.industry_{{ m }},
  i.industry_{{ m }}_yoy,
  i.industry_{{ str_index_name(m) }},
  {% endfor %}
  r.num_rooms,
  c.subject_revpar * r.num_rooms AS subject_room_revenue_est,
  -- Positive: room revenue the hotel would have added by matching comp set RevPAR that night.
  (c.comp_set_revpar - c.subject_revpar) * r.num_rooms AS revenue_to_fair_share,
  c.comp_set_data_ok,
  c.comp_set_yoy_data_ok,
  i.industry_data_ok,
  -- STR restates recent weeks; the newest seven nights may still change.
  c.stay_date > DATE_SUB(MAX(c.stay_date) OVER (), INTERVAL 7 DAY) AS is_provisional,
  c.data_as_of
FROM comp_set AS c
LEFT JOIN industry AS i USING (stay_date, comp_set_id)
LEFT JOIN subject_rooms AS r USING (comp_set_id)
