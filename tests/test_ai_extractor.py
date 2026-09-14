"""
Tests for AI-optimized web content extractor and cleaner.
"""

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from api.main import app
from scrapers.ai_extractor import (
    AIMarkdownCleaner,
    clean_hyperlink,
    estimate_tokens,
)

client = TestClient(app)

SAMPLE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <title>Understanding Neural Networks | TechBlog</title>
    <meta name="description" content="A comprehensive guide to artificial neural networks and deep learning.">
    <meta name="author" content="Alan Turing">
    <meta property="article:published_time" content="2026-05-10T14:30:00Z">
</head>
<body>
    <header>
        <nav>
            <a href="/home">Home</a>
            <a href="/about">About Us</a>
        </nav>
    </header>

    <div class="cookie-banner">
        <p>We use cookies to enhance your experience. <button>Accept</button></p>
    </div>

    <main id="main-content">
        <h1>Understanding Neural Networks</h1>
        <p class="lead">Neural networks are the backbone of modern artificial intelligence.</p>

        <h2>Architecture Overview</h2>
        <p>They consist of multiple layers of interconnected neurons:</p>
        <ul>
            <li>Input Layer</li>
            <li>Hidden Layers (Feature extraction)</li>
            <li>Output Layer</li>
        </ul>

        <h2>Performance Metrics</h2>
        <table>
            <tr>
                <th>Model</th>
                <th>Accuracy</th>
                <th>Latency (ms)</th>
            </tr>
            <tr>
                <td>ResNet-50</td>
                <td>92.4%</td>
                <td>15</td>
            </tr>
            <tr>
                <td>Vision Transformer</td>
                <td>94.8%</td>
                <td>28</td>
            </tr>
        </table>

        <h2>Implementation</h2>
        <pre><code class="language-python">
import torch
model = torch.nn.Linear(10, 2)
print(model)
        </code></pre>

        <p>Read more at <a href="https://example.com/deep-learning?utm_source=twitter&utm_medium=social">Deep Learning Deep Dive</a>.</p>
    </main>

    <div class="ad-banner">
        <a href="https://ads.example.com">Buy Shoes Now!</a>
    </div>

    <footer>
        <p>&copy; 2026 TechBlog. All rights reserved.</p>
    </footer>
</body>
</html>
"""


def test_clean_hyperlink_removes_tracking():
    dirty_url = "https://example.com/page?utm_source=facebook&utm_campaign=winter&id=123&fbclid=abcxyz"
    cleaned = clean_hyperlink(dirty_url, "https://example.com")
    assert "utm_source" not in cleaned
    assert "fbclid" not in cleaned
    assert "id=123" in cleaned


def test_estimate_tokens():
    text = "Artificial intelligence is transforming every industry worldwide."
    tokens = estimate_tokens(text)
    assert tokens > 5
    assert tokens < len(text)


def test_ai_markdown_cleaner():
    soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
    cleaner = AIMarkdownCleaner(base_url="https://techblog.example.com/nn-guide")

    metadata = cleaner.extract_metadata(soup)
    assert metadata["title"] == "Understanding Neural Networks | TechBlog"
    assert metadata["author"] == "Alan Turing"
    assert "deep learning" in metadata["description"].lower()

    clean_dom = cleaner.clean_dom(soup)
    markdown = cleaner.to_markdown(clean_dom)

    # Validate noise removal
    assert "Buy Shoes Now!" not in markdown
    assert "We use cookies" not in markdown
    assert "About Us" not in markdown

    # Validate semantic content preservation
    assert "# Understanding Neural Networks" in markdown
    assert "## Architecture Overview" in markdown
    assert "- Input Layer" in markdown
    assert "| Model | Accuracy | Latency (ms) |" in markdown
    assert "| ResNet-50 | 92.4% | 15 |" in markdown
    assert "```python" in markdown
    assert "import torch" in markdown

    # Validate link cleaning
    assert "utm_source" not in markdown
    assert "[Deep Learning Deep Dive](https://example.com/deep-learning)" in markdown


def test_jina_reader_endpoint_mock(monkeypatch):
    # Test that the /r/{url:path} endpoint formats clean markdown
    from scrapers.ai_extractor import AIExtractor

    def fake_extract(*args, **kwargs):
        return {
            "status": "success",
            "url": "https://example.com",
            "mode_used": "fast",
            "tokens_estimated": 100,
            "word_count": 50,
            "reading_time_minutes": 0.2,
            "metadata": {
                "title": "Mock Title",
                "description": "Mock Description",
                "author": "Mock Author",
                "domain": "example.com",
            },
            "content": "# Mock Title\n\nThis is AI-optimized content.",
            "links": [],
        }

    monkeypatch.setattr(AIExtractor, "extract", fake_extract)

    response = client.get("/r/https://example.com/test-article")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    body = response.text
    assert "# Mock Title" in body
    assert "Estimated Tokens: 100" in body
    assert "This is AI-optimized content." in body
