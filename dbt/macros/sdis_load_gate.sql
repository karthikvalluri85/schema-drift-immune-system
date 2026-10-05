{#
  The SDIS circuit breaker.
  Staging models only read Bronze batches whose LOAD_GATE status is PASSED.
  A quarantined or pending load is invisible downstream — dashboards keep showing
  the last good data instead of wrong data. Bronze itself is never modified.
#}
{% macro sdis_load_gate(table_name) -%}
    _LOAD_ID in (
        select load_id
        from {{ source('drift', 'passed_loads') }}
        where table_fqn = '{{ var("raw_database") }}.{{ var("raw_schema") }}.{{ table_name | upper }}'
    )
{%- endmacro %}
