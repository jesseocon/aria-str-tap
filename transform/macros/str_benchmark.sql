{# Helpers for pivoting the long STR series (one row per value) into wide benchmark marts. #}

{# Index names STR uses for occupancy, ADR and RevPAR. #}
{% macro str_index_name(metric) -%}
  {{ {'occupancy': 'mpi', 'adr': 'ari', 'revpar': 'rgi'}[metric] }}
{%- endmacro %}

{# One value from the current GROUP BY: entity + metric + measure. metric=none matches rows without a metric. #}
{% macro str_pick(entity, metric, measure='value', scale=1) -%}
  MAX(IF(
    entity = '{{ entity }}'
    AND {% if metric is none %}metric IS NULL{% else %}metric = '{{ metric }}'{% endif %}
    AND measure = '{{ measure }}',
    value{% if scale != 1 %} * {{ scale }}{% endif %},
    NULL
  ))
{%- endmacro %}

{# 1 when every STR sufficiency/isolation check for the entity passed. yoy=true checks the year-over-year ones. #}
{% macro str_checks_ok(entities, yoy=false) -%}
  MIN(IF(
    entity IN ({% for e in entities %}'{{ e }}'{% if not loop.last %}, {% endif %}{% endfor %})
    AND measure = 'check'
    AND {% if not yoy %}NOT {% endif %}(calc_type LIKE '%GrowthYoy%' OR calc_type LIKE '%Change%'),
    value,
    NULL
  )) = 1
{%- endmacro %}

{# First day covered by an STR window ending on month_end (a month's last day). #}
{% macro str_window_start(month_end, window) -%}
  CASE {{ window }}
    WHEN 'month' THEN DATE_TRUNC({{ month_end }}, MONTH)
    WHEN '3mo' THEN DATE_SUB(DATE_TRUNC({{ month_end }}, MONTH), INTERVAL 2 MONTH)
    WHEN '12mo' THEN DATE_SUB(DATE_TRUNC({{ month_end }}, MONTH), INTERVAL 11 MONTH)
    WHEN 'ytd' THEN DATE(
      EXTRACT(YEAR FROM {{ month_end }})
        - IF(EXTRACT(MONTH FROM {{ month_end }}) < {{ var('fiscal_year_start_month') }}, 1, 0),
      {{ var('fiscal_year_start_month') }},
      1
    )
  END
{%- endmacro %}

{# STR calls a single month's figures "period"; marts call it "month". #}
{% macro str_window_name(column) -%}
  IF({{ column }} = 'period', 'month', {{ column }})
{%- endmacro %}
