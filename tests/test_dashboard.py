import re
from pathlib import Path


STATIC = Path(__file__).parents[1] / "src" / "phono_console" / "static"


def test_dashboard_javascript_references_existing_unique_elements() -> None:
    html = (STATIC / "dashboard.html").read_text()
    javascript = (STATIC / "dashboard.js").read_text()
    element_ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(element_ids) == len(set(element_ids))

    direct_references = set(re.findall(r'byId\("([^"]+)"\)', javascript))
    assert not direct_references - set(element_ids)

    for prefix in ("in-l", "in-r", "out-l", "out-r"):
        for suffix in ("rms", "peak", "value", "rms-value", "max"):
            assert f'{prefix}-{suffix}' in element_ids


def test_dashboard_documents_output_meter_limit() -> None:
    javascript = (STATIC / "dashboard.js").read_text()
    assert "unity gain" in javascript
    assert "does not expose live PCM levels" in javascript


def test_dashboard_exposes_one_authoritative_output_selector() -> None:
    html = (STATIC / "dashboard.html").read_text()
    javascript = (STATIC / "dashboard.js").read_text()

    assert 'id="local-only"' not in html
    assert "OUTPUT DESTINATION" in html
    assert "async function setOutputMode(mode)" in javascript
    assert 'api("/v1/output-mode"' in javascript
    assert 'api("/v1/local-only"' not in javascript
    assert 'api("/v1/phono-output"' not in javascript
