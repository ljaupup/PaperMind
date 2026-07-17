from frontend.app import format_search_results, format_sources


def test_format_search_results_shows_title_text_and_link() -> None:
    rendered = format_search_results(
        [
            {
                "title": "Test Paper",
                "url": "https://example.com/paper",
                "text": "A retrieved abstract excerpt.",
                "score": 0.12,
            }
        ]
    )

    assert "Test Paper" in rendered
    assert "A retrieved abstract excerpt." in rendered
    assert "https://example.com/paper" in rendered


def test_format_sources_shows_empty_state() -> None:
    assert format_sources([]) == "暂无可展示的来源。"
