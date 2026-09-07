"""Security boundaries for untrusted finding metadata and PDF resources."""
from html.parser import HTMLParser
import sys
import types
from types import SimpleNamespace

import pytest

from biocore.providers.base import Category, ChainLink, Finding, Interpretation, Tier
from biocore.report.render import render_html, to_pdf


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attrs = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.append((tag, dict(attrs)))


def _finding(**kwargs):
    return Finding(
        "1-10-A-G", "unknown", "Synthetic", Tier.ROBUST, [Category.CLINICAL], **kwargs
    )


def test_finding_metadata_cannot_inject_report_markup_or_active_urls():
    payload = "'><link rel='attachment' href='file:///private/genotypes.vcf'><span data-x='"
    interpreted = _finding(
        interpretation=Interpretation("Found", "Meaning", "Evidence"),
        link="javascript:alert(3)",
        evidence_chain=[ChainLink("record", "local", url="file:///private/record.txt")],
    )
    report = render_html(
        [_finding(detail={"topic": payload}, link="javascript:alert(1)"), interpreted],
        [],
        tool_version="<script>alert(2)</script>",
        outcomes=[SimpleNamespace(key="bad", label="Bad", kind="<img src=x>", findings=[])],
    )
    parsed = _Tags()
    parsed.feed(report)
    assert "link" not in parsed.tags
    assert "img" not in parsed.tags
    assert parsed.tags.count("script") == 1  # only the renderer's fixed inline script
    assert not any(
        attrs.get("href", "").lower().startswith(("javascript:", "file:"))
        for _, attrs in parsed.attrs
    )


def test_renderer_bounds_finding_metadata_attributes():
    report = render_html([_finding(detail={"topic": "x" * 10_000})], [])
    parsed = _Tags()
    parsed.feed(report)
    topic_values = [
        attrs[key]
        for _, attrs in parsed.attrs
        for key in ("data-topic", "data-topics")
        if key in attrs
    ]
    assert topic_values
    assert max(map(len, topic_values)) <= 512


def test_pdf_renderer_refuses_local_file_resources(monkeypatch, tmp_path):
    attempted = tmp_path / "private-genotypes.txt"
    attempted.write_text("SYNTHETIC_ALICE 1-10-A-G A/G")

    class FakeHTML:
        def __init__(self, *, string, url_fetcher=None):
            assert url_fetcher is not None
            self.url_fetcher = url_fetcher

        def write_pdf(self, out_path):
            with pytest.raises(ValueError, match="resource URL"):
                self.url_fetcher(attempted.as_uri())

    monkeypatch.setitem(sys.modules, "weasyprint", types.SimpleNamespace(HTML=FakeHTML))
    to_pdf("<p>safe</p>", str(tmp_path / "report.pdf"))
