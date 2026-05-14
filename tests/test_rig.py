from cwsigfind.rig import normalize_mode_for_hamlib, parse_rigctl_list


SAMPLE_RIGCTL_LIST = """ Rig #  Mfg                    Model                   Version         Status      Macro
     1  Hamlib                 Dummy                   20240709.0      Stable      RIG_MODEL_DUMMY
     2  Hamlib                 NET rigctl              20250211.0      Stable      RIG_MODEL_NETRIGCTL
     6  Hamlib                 Dummy No VFO            20240409.0      Stable      RIG_MODEL_DUMMY_NOVFO
    10  N2ADR James Ahlstrom   Quisk                   20230709.0      Stable      RIG_MODEL_QUISK
  1004  Yaesu                  FT-1000MP MARK-V        20241105.1      Stable      RIG_MODEL_FT1000MPMKV
  3023  Icom                   IC-7300                 20240226.0      Stable      RIG_MODEL_IC7300
"""


def test_parse_rigctl_list_finds_models():
    models = parse_rigctl_list(SAMPLE_RIGCTL_LIST)
    ids = {m.model_id for m in models}
    assert ids == {1, 2, 6, 10, 1004, 3023}


def test_parse_rigctl_list_keeps_multiword_columns():
    models = {m.model_id: m for m in parse_rigctl_list(SAMPLE_RIGCTL_LIST)}
    # Model with internal spaces.
    assert models[6].mfg == "Hamlib"
    assert models[6].model == "Dummy No VFO"
    # Multi-word manufacturer.
    assert models[10].mfg == "N2ADR James Ahlstrom"
    assert models[10].model == "Quisk"
    # Hyphens + spaces.
    assert models[1004].model == "FT-1000MP MARK-V"


def test_parse_rigctl_list_skips_garbage():
    assert parse_rigctl_list("") == []
    assert parse_rigctl_list("not a real listing\nblah blah") == []


def test_parse_rigctl_list_sorted_by_mfg_then_model():
    models = parse_rigctl_list(SAMPLE_RIGCTL_LIST)
    # Hamlib entries appear before Icom appears before N2ADR appears before Yaesu.
    mfgs_in_order = [m.mfg for m in models]
    assert mfgs_in_order == sorted(mfgs_in_order, key=str.lower)


def test_mode_mapping_basic():
    assert normalize_mode_for_hamlib("CW") == "CW"
    assert normalize_mode_for_hamlib("AM") == "AM"
    assert normalize_mode_for_hamlib("FM") == "FM"
    assert normalize_mode_for_hamlib("USB") == "USB"
    assert normalize_mode_for_hamlib("LSB") == "LSB"


def test_mode_mapping_ssb_by_frequency():
    # HF convention: USB above 10 MHz, LSB below.
    assert normalize_mode_for_hamlib("SSB", freq_khz=14250.0) == "USB"
    assert normalize_mode_for_hamlib("SSB", freq_khz=7200.0) == "LSB"
    # No frequency hint → can't decide → None (caller skips).
    assert normalize_mode_for_hamlib("SSB", freq_khz=None) is None


def test_mode_mapping_digital_runs_on_usb():
    assert normalize_mode_for_hamlib("FT8") == "USB"
    assert normalize_mode_for_hamlib("FT4") == "USB"


def test_mode_mapping_unknown_returns_none():
    assert normalize_mode_for_hamlib("") is None
    assert normalize_mode_for_hamlib("WSPRX_GARBAGE") is None
