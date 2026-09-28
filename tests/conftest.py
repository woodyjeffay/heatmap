import pytest
from PIL import Image

from heatmap import cli


@pytest.fixture(autouse=True)
def offline_basemap(monkeypatch):
    """Never download real map tiles in tests: hand the CLI a plain dark map."""
    calls = []

    def fake_fetch(bbox, width, height, provider="carto-dark", **kwargs):
        calls.append(provider)
        return Image.new("RGB", (width, height), (20, 20, 22))

    monkeypatch.setattr(cli, "fetch_basemap", fake_fetch)
    return calls
