import shutil
from contextlib import contextmanager
from pathlib import Path

from invoke import Context, task

DOCS_PATH = Path("docs")
DOCS_SRC_PATH = DOCS_PATH / "src"


def get_allowed_doc_languages():
    """Detect languages as subfolders in docs/src/

    Ensure `en` is always first.
    """
    return ["en"] + [f.name for f in DOCS_SRC_PATH.iterdir() if f.is_dir() and f.name != "en"]


ALLOWED_DOC_LANGUAGES = get_allowed_doc_languages()


@contextmanager
def docs_rendered(language: str):
    """Copies language-agnostic assets from ``en`` into non-en folders and
    returns a rendered mkdocs config copy path."""
    config_template_path = DOCS_PATH / "mkdocs.yml"
    common_path = DOCS_PATH / "common"
    src_path = DOCS_SRC_PATH / language

    build_docs_path = Path("build") / "docs"
    build_config_path = build_docs_path / "mkdocs.yml"
    build_src_path = build_docs_path / "src" / language
    site_dir = Path("site") if language == "en" else Path("site") / language

    config = config_template_path.read_text()
    config = config.replace("LANGUAGE", language)
    config = config.replace("SITE_DIR", str(site_dir))

    build_docs_path.mkdir(parents=True, exist_ok=True)
    build_config_path.write_text(config)
    shutil.rmtree(build_src_path, ignore_errors=True)
    shutil.copytree(src_path, build_src_path)
    if common_path.is_dir():
        shutil.copytree(common_path, build_src_path, dirs_exist_ok=True)
    yield build_config_path


def docs_task_factory(language: str):
    @task
    def docs(c: Context):
        """Docs preview for the language specified."""
        with docs_rendered(language) as config_copy_path:
            port = 8001
            c.run(f"open -a 'Google Chrome' http://127.0.0.1:{port}")
            c.run(f"zensical serve --config-file {config_copy_path} --dev-addr localhost:{port}")

    return docs


@task
def build_docs(c: Context):
    """Build docs in docs/site/."""
    for language in ALLOWED_DOC_LANGUAGES:
        with docs_rendered(language) as config_copy_path:
            c.run(f"zensical build --config-file {config_copy_path}")
