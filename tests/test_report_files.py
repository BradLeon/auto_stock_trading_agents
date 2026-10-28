from ats.agents.report_files import unique_path


def test_the_first_file_keeps_the_plain_name(tmp_path):
    assert unique_path(tmp_path / "a.md") == tmp_path / "a.md"


def test_later_files_get_increasing_version_suffixes_and_nothing_is_replaced(tmp_path):
    names = []
    for _ in range(3):
        path = unique_path(tmp_path / "a.md")
        path.write_text(path.name, encoding="utf-8")
        names.append(path.name)
    assert names == ["a.md", "a-v2.md", "a-v3.md"]
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "a.md"
