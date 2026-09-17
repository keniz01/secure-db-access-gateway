"""Unit tests for the LLM-driven result presentation planner."""

import asyncio
import json
from collections.abc import Awaitable, Callable

import httpx
import pytest

from services.result_presentation_service import ResultPresentationService


def _service(
    http_post: Callable[..., Awaitable[object]] | None, **kwargs: object
) -> ResultPresentationService:
    """Build a service wired to a fake HTTP layer for hermetic tests."""
    defaults = {
        "http_post": http_post,
        "enabled": True,
        "api_key": "test-key",
        "retries": 1,
        "backoff_base": 0.01,
        "request_timeout": 0.5,
        "max_sample_rows": 20,
        "max_cell_chars": 80,
        "max_payload_bytes": 20000,
    }
    defaults.update(kwargs)
    return ResultPresentationService(**defaults)


def _llm_response(decision: dict) -> httpx.Response:
    """Wrap a decision dict in an OpenAI-compatible chat completion response."""
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(decision)}}]})


async def _assert_decision_and_deliver(
    service: ResultPresentationService, payload: dict, decision: dict
) -> dict:
    captured: dict[str, object] = {}

    async def fake_post(url: str, headers: dict, body: dict):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = body
        user_prompt = body["messages"][1]["content"]
        captured["prompt"] = user_prompt
        return _llm_response(decision)

    result = await service.decide(**payload)
    return result, captured


class TestFormatSelection:
    async def test_count_aggregate_choice_paragraph(self):
        response = _llm_response(
            {"format": "paragraph", "content": "There are 12,438 users registered.", "reason": "single scalar"}
        )
        service = _service(lambda *a: response)
        decision = await service.decide(
            question="How many users are registered?",
            sql="SELECT COUNT(*) AS user_count FROM users",
            rows=[{"user_count": 12438}],
        )
        assert decision is not None
        assert decision.format == "paragraph"
        assert decision.content == "There are 12,438 users registered."
        assert decision.reason == "single scalar"

    @pytest.mark.parametrize(
        ("sql", "sentence"),
        [
            ("SELECT SUM(duration) AS value FROM tracks", "The total duration is 45 minutes."),
            ("SELECT AVG(duration) AS value FROM tracks", "The average duration is 45 minutes."),
            ("SELECT MIN(duration) AS value FROM tracks", "The shortest duration is 45 minutes."),
            ("SELECT MAX(duration) AS value FROM tracks", "The longest duration is 45 minutes."),
        ],
    )
    async def test_aggregates_plan_as_paragraph(self, sql, sentence):
        decision = {
            "format": "paragraph",
            "content": sentence,
            "reason": "single applicable aggregate",
        }
        service = _service(lambda *a: _llm_response(decision))
        result = await service.decide(sql=sql, rows=[{"value": 45}])
        assert result is not None
        assert result.format == "paragraph"

    async def test_multi_row_relational_stays_table(self):
        rows = [
            {"id": i, "title": f"Album {i}", "artist": "The Beatles", "release_year": 1964 + i}
            for i in range(1, 5)
        ]
        response = _llm_response({"format": "table", "content": "", "reason": "multi-column relational data"})
        service = _service(lambda *a: response)
        decision = await service.decide(
            question="List the albums",
            sql="SELECT id, title, artist, release_year FROM albums",
            rows=rows,
        )
        assert decision is not None
        assert decision.format == "table"
        assert decision.content is None

    async def test_small_categorical_result_plans_as_list(self):
        rows = [{"genre": "Rock"}, {"genre": "Jazz"}, {"genre": "Funk"}]
        response = _llm_response({"format": "list", "content": "Rock, Jazz, Funk", "reason": "small categorical set"})
        service = _service(lambda *a: response)
        decision = await service.decide(
            question="Which genres exist?",
            sql="SELECT DISTINCT genre FROM tracks",
            rows=rows,
        )
        assert decision is not None
        assert decision.format == "list"
        assert decision.content == "Rock, Jazz, Funk"

    async def test_table_with_summary_content_is_accepted(self):
        rows = [
            {"department": "Electronics", "order_count": 412},
            {"department": "Apparel", "order_count": 318},
        ]
        response = _llm_response(
            {
                "format": "table",
                "content": "2 departments are shown; Electronics leads with 412 orders.",
                "reason": "relational data with an interpreting summary",
            }
        )
        service = _service(lambda *a: response)
        decision = await service.decide(
            question="How many orders per department?",
            sql="SELECT department, COUNT(*) AS order_count FROM orders GROUP BY department",
            rows=rows,
        )
        assert decision is not None
        assert decision.format == "table"
        assert decision.content == "2 departments are shown; Electronics leads with 412 orders."

    async def test_table_summary_with_invented_number_is_rejected(self):
        response = _llm_response(
            {
                "format": "table",
                "content": "1,000 departments total across the org.",
                "reason": "summary",
            }
        )
        service = _service(lambda *a: response)
        decision = await service.decide(
            sql="SELECT department FROM departments",
            rows=[{"department": "Electronics"}, {"department": "Apparel"}],
        )
        assert decision.format == "table"
        assert decision.content is None

    async def test_chart_plan_for_grouped_numeric_rows(self):
        rows = [
            {"department": "Electronics", "order_count": 412},
            {"department": "Apparel", "order_count": 318},
            {"department": "Grocery", "order_count": 290},
        ]
        response = _llm_response(
            {"format": "chart", "content": "Order volume by department.", "reason": "grouped category + measure"}
        )
        service = _service(lambda *a: response)
        decision = await service.decide(
            question="How many orders does each department have?",
            sql="SELECT department, COUNT(*) AS order_count FROM orders GROUP BY department",
            rows=rows,
        )
        assert decision is not None
        assert decision.format == "chart"
        assert decision.content == "Order volume by department."

    async def test_chart_plan_without_numeric_column_falls_back_to_table(self):
        response = _llm_response({"format": "chart", "content": "", "reason": "wants a chart"})
        service = _service(lambda *a: response)
        decision = await service.decide(
            sql="SELECT name, email FROM customers LIMIT 5",
            rows=[{"name": "Ada", "email": "ada@example.com"}],
        )
        assert decision is not None
        assert decision.format == "table"

    async def test_chart_caption_with_invented_number_is_rejected(self):
        response = _llm_response(
            {"format": "chart", "content": "412 is the top figure, 999 the rest.", "reason": "caption"}
        )
        service = _service(lambda *a: response)
        decision = await service.decide(
            sql="SELECT department, COUNT(*) AS order_count FROM orders GROUP BY department",
            rows=[{"department": "Electronics", "order_count": 412}],
        )
        assert decision.format == "table"
        assert decision.content is None


class TestPayloadBounding:
    async def test_large_result_set_bounds_llm_payload(self):
        rows = [{"id": i, "name": f"value-{i}", "blob": "x" * 500} for i in range(1, 201)]
        captured = {}

        async def fake_post(url, headers, body):
            captured["prompt"] = body["messages"][1]["content"]
            return _llm_response({"format": "table", "content": "", "reason": "large dataset"})

        service = _service(fake_post, max_sample_rows=20, max_cell_chars=80, max_payload_bytes=20000)
        decision = await service.decide(sql="SELECT id, name, blob FROM big_table", rows=rows)
        assert decision is not None
        prompt = captured["prompt"]
        assert "shown: 20 of 200" in prompt
        assert "truncated: yes" in prompt
        assert "x" * 500 not in prompt
        sample_start = prompt.index("ROWS")
        sample_json = prompt[sample_start:]
        payload_bytes = len(sample_json.encode("utf-8"))
        assert payload_bytes <= 20000

    async def test_long_cell_values_truncated(self):
        rows = [{"id": 1, "body": "y" * 5000}]
        captured = {}

        async def fake_post(url, headers, body):
            captured["prompt"] = body["messages"][1]["content"]
            return _llm_response({"format": "table", "content": "", "reason": "tabular"})

        service = _service(fake_post)
        await service.decide(sql="SELECT id, body FROM notes", rows=rows)
        assert "y" * 5000 not in captured["prompt"]
        assert "y" * 79 in captured["prompt"]

    async def test_null_values_serialized_as_null(self):
        rows = [{"id": 1, "nickname": None}]
        captured = {}

        async def fake_post(url, headers, body):
            captured["prompt"] = body["messages"][1]["content"]
            return _llm_response({"format": "table", "content": "", "reason": "mixed values"})

        service = _service(fake_post)
        await service.decide(sql="SELECT id, nickname FROM users", rows=rows)
        assert '"nickname":null' in captured["prompt"]

    async def test_empty_result_set_never_calls_llm(self):
        called = False

        async def fake_post(url, headers, body):
            nonlocal called
            called = True
            return _llm_response({"format": "table", "content": "", "reason": "unused"})

        service = _service(fake_post)
        decision = await service.decide(sql="SELECT id FROM tracks WHERE 1=0", rows=[])
        assert decision is None
        assert called is False


class TestSensitiveDataHandling:
    async def test_masked_values_are_what_the_llm_sees(self):
        # The governed pipeline masks sensitive columns before this service runs,
        # so the exact string the LLM receives must reflect post-masking data.
        rows = [{"email": "[MASKED]", "name": "Alice"}]
        captured = {}

        async def fake_post(url, headers, body):
            captured["prompt"] = body["messages"][1]["content"]
            return _llm_response({"format": "table", "content": "", "reason": "masked data"})

        service = _service(fake_post)
        await service.decide(sql="SELECT email, name FROM users", rows=rows)
        assert "[MASKED]" in captured["prompt"]
        assert "secret@example.com" not in captured["prompt"]

    async def test_no_credentials_in_payload(self):
        rows = [{"api_key_value": "sk-123secret", "label": "x"}]
        captured = {}

        async def fake_post(url, headers, body):
            captured["prompt"] = body["messages"][1]["content"]
            return _llm_response({"format": "table", "content": "", "reason": "n/a"})

        service = _service(fake_post)
        await service.decide(sql="SELECT api_key_value FROM config", rows=rows)
        assert "sk-123secret" in captured["prompt"]


class TestGracefulFallback:
    async def test_llm_failure_falls_back_to_table(self):
        def boom(url, headers, body):
            raise httpx.ConnectError("refused")

        service = _service(boom)
        decision = await service.decide(sql="SELECT COUNT(*) AS c FROM users", rows=[{"c": 7}])
        assert decision is not None
        assert decision.format == "table"
        assert decision.content is None

    async def test_llm_timeout_falls_back_to_table(self):
        async def slow(url, headers, body):
            await asyncio.sleep(5)
            return _llm_response({"format": "paragraph", "content": "late", "reason": "too slow"})

        service = _service(slow, retries=1, request_timeout=0.05)
        decision = await service.decide(sql="SELECT COUNT(*) AS c FROM users", rows=[{"c": 7}])
        assert decision is not None
        assert decision.format == "table"

    async def test_malformed_llm_response_falls_back_to_table(self):
        response = httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})
        service = _service(lambda *a: response)
        decision = await service.decide(sql="SELECT 1 AS one", rows=[{"one": 1}])
        assert decision.format == "table"

    async def test_fenced_json_response_is_parsed(self):
        fenced = '```json\n{"format": "paragraph", "content": "There are 5 songs.", "reason": "scalar"}\n```'
        service = _service(lambda *a: httpx.Response(200, json={"choices": [{"message": {"content": fenced}}]}))
        decision = await service.decide(sql="SELECT COUNT(*) AS n FROM tracks", rows=[{"n": 5}])
        assert decision is not None
        assert decision.format == "paragraph"

    async def test_unsupported_format_falls_back_to_table(self):
        service = _service(
            lambda *a: _llm_response({"format": "heatmap", "content": "", "reason": "would need heatmap"})
        )
        decision = await service.decide(sql="SELECT ts, value FROM metrics", rows=[{"ts": "2024-01-01", "value": 12}])
        assert decision is not None
        assert decision.format == "table"

    async def test_empty_paragraph_content_falls_back(self):
        service = _service(lambda *a: _llm_response({"format": "paragraph", "content": "", "reason": "empty"}))
        decision = await service.decide(sql="SELECT COUNT(*) AS n FROM tracks", rows=[{"n": 5}])
        assert decision.format == "table"

    async def test_invented_number_in_paragraph_is_rejected(self):
        # Content claims a figure that is not present in the result data.
        decision_dict = {"format": "paragraph", "content": "There are 99,999 users registered.", "reason": "n/a"}
        service = _service(lambda *a: _llm_response(decision_dict))
        decision = await service.decide(sql="SELECT COUNT(*) AS user_count FROM users", rows=[{"user_count": 12438}])
        assert decision is not None
        assert decision.format == "table"

    async def test_disabled_service_returns_none_without_calling_llm(self):
        called = False

        async def fake_post(url, headers, body):
            nonlocal called
            called = True
            return _llm_response({"format": "table", "content": "", "reason": "unused"})

        service = ResultPresentationService(enabled=False, api_key="k", http_post=fake_post)
        assert await service.decide(sql="SELECT 1 AS one", rows=[{"one": 1}]) is None
        assert called is False

    async def test_missing_api_key_returns_none(self):
        service = ResultPresentationService(enabled=True, api_key="", http_post=lambda *a: None)
        assert await service.decide(sql="SELECT 1 AS one", rows=[{"one": 1}]) is None


class TestContextBuilding:
    async def test_question_and_sql_included_in_payload(self):
        captured = {}

        async def fake_post(url, headers, body):
            captured["prompt"] = body["messages"][1]["content"]
            return _llm_response({"format": "table", "content": "", "reason": "n/a"})

        service = _service(fake_post)
        await service.decide(
            question="How many albums?",
            sql="SELECT COUNT(*) AS n FROM albums",
            rows=[{"n": 2}],
        )
        assert "How many albums?" in captured["prompt"]
        assert "SELECT COUNT(*) AS n FROM albums" in captured["prompt"]

    async def test_column_metadata_included(self):
        captured = {}

        async def fake_post(url, headers, body):
            captured["prompt"] = body["messages"][1]["content"]
            return _llm_response({"format": "table", "content": "", "reason": "n/a"})

        service = _service(fake_post)
        await service.decide(sql="SELECT id, genre FROM tracks", rows=[{"id": 1, "genre": "Rock"}])
        assert "name: id, type: integer, nullable: no" in captured["prompt"]
        assert "name: genre, type: string, nullable: no" in captured["prompt"]

    async def test_llm_request_uses_json_mode_and_api_key(self):
        captured = {}

        async def fake_post(url, headers, body):
            captured.update({"url": url, "headers": headers, "body": body})
            return _llm_response({"format": "table", "content": "", "reason": "n/a"})

        service = _service(fake_post)
        await service.decide(sql="SELECT 1 AS one", rows=[{"one": 1}])
        assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer test-key"
        assert captured["body"]["response_format"] == {"type": "json_object"}
        assert captured["body"]["temperature"] == 0
        assert captured["body"]["reasoning"] == {"enabled": False}

    async def test_question_answer_shape_adds_summary_directive(self):
        captured = {}

        async def fake_post(url, headers, body):
            captured["prompt"] = body["messages"][1]["content"]
            return _llm_response({"format": "paragraph", "content": "n/a", "reason": "n/a"})

        service = _service(fake_post)
        rows = [
            {"album_count": 235, "album_with_most_tracks": "Dancehall To The Core Vol. 3", "track_count": 25},
            {"album_count": 235, "album_with_most_tracks": "Just Ragga Limited Edition Box", "track_count": 25},
        ]
        await service.decide(
            question="How many albums does Capleton have and which album has the most tracks?",
            sql="SELECT album_count, album_with_most_tracks, track_count FROM capleton_albums",
            rows=rows,
        )
        assert "answer a single factual question" in captured["prompt"]

    async def test_relational_rows_without_question_get_no_directive(self):
        captured = {}

        async def fake_post(url, headers, body):
            captured["prompt"] = body["messages"][1]["content"]
            return _llm_response({"format": "table", "content": "", "reason": "n/a"})

        service = _service(fake_post)
        await service.decide(
            sql="SELECT id, title, release_year FROM albums",
            rows=[{"id": 1, "title": "Abbey Road", "release_year": 1969}],
        )
        assert "answer a single factual question" not in captured["prompt"]

    async def test_question_answer_paragraph_with_result_figures_is_accepted(self):
        content = (
            "Capleton has 235 albums. The album with the most tracks is "
            "Just Ragga Limited Edition Box with 25 tracks."
        )
        decision = {
            "format": "paragraph",
            "content": content,
            "reason": "quantity plus a max-tracks entity",
        }
        service = _service(lambda *a: _llm_response(decision))
        rows = [
            {"album_count": 235, "album_with_most_tracks": "Dancehall To The Core Vol. 3", "track_count": 25},
            {"album_count": 235, "album_with_most_tracks": "Just Ragga Limited Edition Box", "track_count": 25},
        ]
        result = await service.decide(
            question="How many albums does Capleton have and which album has the most tracks?",
            sql="SELECT album_count, album_with_most_tracks, track_count FROM capleton_albums",
            rows=rows,
        )
        assert result is not None
        assert result.format == "paragraph"
        assert result.content == content
