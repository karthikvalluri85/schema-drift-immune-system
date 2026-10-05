from sdis.config import load_dotenv


def test_load_dotenv_fills_only_unset(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\r\nSDIS_T_A=from_file\r\nSDIS_T_B=from_file\r\nSDIS_T_EMPTY=\r\nSDIS_T_Q=\"quoted\"\r\n",
                   encoding="utf-8")
    monkeypatch.setenv("SDIS_T_B", "real_env")
    for k in ("SDIS_T_A", "SDIS_T_EMPTY", "SDIS_T_Q"):
        monkeypatch.delenv(k, raising=False)
    loaded = load_dotenv(env)
    import os
    assert os.environ["SDIS_T_A"] == "from_file"
    assert os.environ["SDIS_T_B"] == "real_env"      # never overrides the real environment
    assert "SDIS_T_EMPTY" not in os.environ          # blank lines in .env stay unset
    assert os.environ["SDIS_T_Q"] == "quoted"
    assert sorted(loaded) == ["SDIS_T_A", "SDIS_T_Q"]
    for k in loaded:
        monkeypatch.delenv(k)


def test_load_dotenv_missing_file(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == []
