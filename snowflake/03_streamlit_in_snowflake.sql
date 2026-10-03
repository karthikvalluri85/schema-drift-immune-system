-- =============================================================================
-- Optional: host the SDIS dashboard inside Snowflake (no server, no DigitalOcean).
-- Easiest path: Snowsight → Projects → Streamlit → + Streamlit App →
--   database SDIS_DB, schema DRIFT, warehouse SDIS_WH → paste streamlit/streamlit_app.py → Run.
-- (The "Scenario lab" tab needs the sdis package and only works when run from the repo.)
-- Scripted path below (upload the file to the stage with Snowsight or SnowSQL `PUT`).
-- =============================================================================
use role SDIS_AGENT_ROLE;
use warehouse SDIS_WH;
use schema SDIS_DB.DRIFT;

create stage if not exists SDIS_APP directory = (enable = true);
-- PUT file://streamlit/streamlit_app.py @SDIS_DB.DRIFT.SDIS_APP overwrite = true auto_compress = false;

create streamlit if not exists SDIS_COMMAND_CENTER
  root_location = '@SDIS_DB.DRIFT.SDIS_APP'
  main_file = 'streamlit_app.py'
  query_warehouse = SDIS_WH
  title = 'SDIS Command Center';
