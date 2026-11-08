import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from biocore.copy.check import check_table, format_problems
from biocore.report.terms import TERMS, TERMS_PATH, load_table, terms_html, term_link, term_anchor

FIXTURE = Path(__file__).parent / "fixtures" / "terms_pre_move.json"


def test_terms_equal_the_dict_before_it_became_data():
    # The 16 entries exactly as terms.py held them before the move to terms.json.
    before = {k: tuple(v) for k, v in json.loads(FIXTURE.read_text()).items()}
    assert TERMS == before
    assert list(TERMS) == list(before)
    assert len(TERMS) == 16


def test_terms_table_passes_the_shared_validator():
    problems = check_table("terms", load_table())
    assert problems == [], format_problems(problems)


def test_html_only_lists_requested_terms_and_links_resolve():
    html = terms_html(["het", "stars", "nope", "het"])
    assert html == ("<section id='terms'><h2>Terms used in this report</h2><dl class='terms'>"
                    "<dt id='term-het'>One altered copy</dt><dd>You carry two copies of most genes, "
                    "one from each parent. One altered copy means the other copy is unchanged.</dd>"
                    "<dt id='term-stars'>Review stars</dt><dd>ClinVar&#x27;s 0 to 4 scale for how "
                    "well laboratories agree. 2 stars means several laboratories agree with the same "
                    "criteria. 4 means a practice guideline.</dd></dl></section>")
    assert "One altered copy" in html and "Review stars" in html and "Odds ratio" not in html
    assert html.count("<dt") == 2
    assert term_anchor("het") in term_link("het", "one altered copy")
    assert terms_html([]) == "" and terms_html(["nope"]) == ""


def test_terms_json_ships_in_a_non_editable_build(tmp_path):
    # An editable install reads data from the checkout, so CI passes without the
    # package-data entry while every image (pip install .) would lack the file.
    # Build from a clean copy: a leftover build/ directory would hide the gap.
    root = Path(__file__).resolve().parents[1]
    src = tmp_path / "src"
    shutil.copytree(root, src, ignore=shutil.ignore_patterns(
        ".git", "build", "dist", "*.egg-info", "__pycache__", ".pytest_cache", "tests", ".venv"))
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w",
                    str(tmp_path / "dist"), str(src)], check=True)
    wheel = next((tmp_path / "dist").glob("biocore-*.whl"))
    rel = TERMS_PATH.relative_to(root).as_posix()
    assert rel in zipfile.ZipFile(wheel).namelist()
