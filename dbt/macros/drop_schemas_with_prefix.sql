{# Slim CI cleanup: drop every schema created for one PR (ANALYTICS_CI_<pr>, ANALYTICS_CI_<pr>_STAGING, …). #}
{% macro drop_schemas_with_prefix(prefix) %}
  {% if not prefix.upper().startswith('ANALYTICS_CI_') %}
    {{ exceptions.raise_compiler_error("refusing to drop schemas outside ANALYTICS_CI_*") }}
  {% endif %}
  {% set rows = run_query("select schema_name from " ~ target.database ~ ".information_schema.schemata where schema_name ilike '" ~ prefix ~ "%'") %}
  {% for r in rows %}
    {% do run_query("drop schema if exists " ~ target.database ~ "." ~ r[0] ~ " cascade") %}
    {{ log("dropped " ~ r[0], info=True) }}
  {% endfor %}
{% endmacro %}
