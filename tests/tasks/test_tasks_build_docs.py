"""Tests for :mod:`tasks.devtools.build_docs`."""

import allure

from tasks.devtools.build_docs import docs_rendered


@allure.epic("Infrastructure")
@allure.feature("Docs")
class TestDocsRendered:
    def test_a_docs_build_never_reuses_a_page_rendered_from_an_older_include(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "docs" / "src" / "en").mkdir(parents=True)
        (tmp_path / "docs" / "mkdocs.yml").write_text("docs_dir: 'src/LANGUAGE'\n")
        stale = tmp_path / "build" / "docs" / ".cache" / "page"
        stale.parent.mkdir(parents=True)
        stale.write_text("rendered from the previous include")

        with docs_rendered("en"):
            assert not stale.parent.exists()
