import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_missions  # noqa: E402


def test_copies_every_miz(tmp_path, capsys):
    assert install_missions.main(["--dest", str(tmp_path)]) == 0
    expected = sorted(p.name for p in install_missions.SOURCE_DIR.glob("*.miz"))
    assert expected
    assert sorted(p.name for p in tmp_path.iterdir()) == expected
    for name in expected:
        assert (tmp_path / name).read_bytes() == (install_missions.SOURCE_DIR / name).read_bytes()


def test_skips_missing_folder(tmp_path, capsys):
    missing = tmp_path / "nope"
    assert install_missions.main(["--dest", str(missing)]) == 0
    assert not missing.exists()
    assert "Skipped" in capsys.readouterr().out
