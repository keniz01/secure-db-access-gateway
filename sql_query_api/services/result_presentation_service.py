"""
LLM-driven result presentation planning.

After a governed SELECT has executed, this service asks an LLM (via an
OpenAI-compatible chat-completions endpoint) to decide how the result should be
displayed: a concise paragraph for simple aggregates, a list for small
categorical results, or a plain table for relational data.

The service is an *enhancement* to the existing table renderer, never a
dependency: if the LLM is unavailable, misconfigured, slow, or returns anything
the backend cannot safely use, the caller falls back to the default table
representation and the raw rows are always returned to the client unchanged.

Safety rules enforced here (in addition to the prompt):
  - Only the user's question, the SQL text, column metadata and a *bounded*
    sample of the result are ever sent to the LLM. Connection strings and other
    application secrets never reach this service.
  - The payload is capped (max sample rows, max cell length, max total bytes).
  - A numeric guard rejects paragraph wording that invents values that are not
    present in the actual result rows.
  - Column-label hints are restricted to real result columns and are display
    metadata only; the rows themselves are never rewritten.
"""

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from shared_secrets import read_secret

logger = logging.getLogger(__name__)

_PRESENTATION_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts" / "presentation"
_PRESENTATION_SYSTEM_PROMPT_PATH = _PRESENTATION_PROMPT_DIR / "system.txt"

SUPPORTED_FORMATS: frozenset[str] = frozenset({"paragraph", "list", "chart", "table"})

_INTEGER_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_DENSE_INTEGER_RE = re.compile(r"\d[\d,]*")
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# A result that directly answers one factual question can be a few rows wide.
_MAX_QUESTION_ANSWER_ROWS = 3
# Human-readable column labels are presentation metadata; keep them short.
_MAX_COLUMN_LABEL_CHARS = 60
_SUMMARY_COLUMN_PATTERN = re.compile(
    r"(^|[^a-z])(count|qty|quantity|total|sum|avg|average|min|max|most|num|number)([^a-z]|$)",
    re.IGNORECASE,
)
_QUESTION_SUMMARY_HINT = (
    "NOTE: These ROWS directly answer a single factual question, even though "
    "they span a few rows. Choose \"paragraph\" and fold the rows into one or two "
    "sentences. Only deviate if the rows clearly show unrelated records."
)


class PresentationServiceError(Exception):
    """Raised when the LLM request or its structured output cannot be used."""


class _RetryableResponseError(Exception):
    """Transient LLM response (rate limit or timed-out request) that may be retried."""


class _InvalidDecisionError(Exception):
    """The LLM returned a structured decision that fails validation or safety guards."""


@dataclass(frozen=True, slots=True)
class PresentationDecision:
    """
    A validated presentation decision for a query result.

    ``format`` is one of ``SUPPORTED_FORMATS``. ``content`` carries the
    natural-language wording: required for ``paragraph``, and an optional
    summary/caption for ``list``, ``chart``, and ``table``. ``column_labels``
    maps raw result column names to short human-readable headers; it is
    presentation metadata only and never a substitute for the authoritative
    ``rows``.
    """

    format: str
    content: str | None = None
    reason: str | None = None
    column_labels: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the decision as a plain dictionary for transport."""
        return {
            "format": self.format,
            "content": self.content,
            "reason": self.reason,
            "column_labels": dict(self.column_labels),
        }


class ResultPresentationService:
    """Plan how a SQL query result should be presented, without touching data."""

    def __init__(
        self,
        *,
        system_prompt: str = "",
        enabled: bool = True,
        api_key: str = "",
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "",
        max_tokens: int = 300,
        request_timeout: float = 30.0,
        retries: int = 3,
        backoff_base: float = 2.0,
        disable_reasoning: bool = True,
        max_sample_rows: int = 20,
        max_cell_chars: int = 80,
        max_payload_bytes: int = 20000,
        max_sql_chars: int = 2000,
        http_post: Callable[..., Awaitable[httpx.Response]] | None = None,
    ) -> None:
        """
        Initialize the presentation planner.

        Args:
            system_prompt: System prompt text instructing the presentation planner.
            enabled: Master switch; ``False`` disables all LLM work (table fallback).
            api_key: Bearer token for the OpenAI-compatible chat endpoint.
            base_url: Base URL of the OpenAI-compatible API (e.g. OpenRouter).
            model: Model identifier used for the decision request.
            max_tokens: Cap on generated decision tokens.
            request_timeout: Per-attempt HTTP timeout in seconds.
            retries: Number of attempts for transient failures (429/timeout).
            backoff_base: Exponential backoff base between retries.
            disable_reasoning: Send the OpenRouter ``reasoning.enabled=false`` plugin.
            max_sample_rows: Maximum number of result rows sent to the LLM.
            max_cell_chars: Maximum characters of any single cell value sent.
            max_payload_bytes: Hard cap on the serialized sample sent to the LLM.
            max_sql_chars: Truncation limit for the SQL text included in the payload.
            http_post: Injectable ``async (url, headers, json) -> httpx.Response``
                for hermetic testing. Defaults to a real HTTP POST.

        """
        self._system_prompt = system_prompt or self._load_system_prompt()
        self._enabled = enabled and bool(self._system_prompt)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model or "openai/gpt-4o"
        self._max_tokens = max(1, int(max_tokens))
        self._request_timeout = max(0.1, float(request_timeout))
        self._retries = max(1, int(retries))
        self._backoff_base = max(0.1, float(backoff_base))
        self._disable_reasoning = bool(disable_reasoning)
        self._max_sample_rows = max(1, int(max_sample_rows))
        self._max_cell_chars = max(10, int(max_cell_chars))
        self._max_payload_bytes = max(512, int(max_payload_bytes))
        self._max_sql_chars = max(100, int(max_sql_chars))
        self._http_post = http_post

    @classmethod
    def from_environment(cls) -> "ResultPresentationService":
        """Build the service from environment variables (repo convention)."""
        env = os.getenv
        enabled = env("SQL_PRESENTATION_ENABLED", "true").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        disable_reasoning = env("AI_DISABLE_REASONING", "true").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        return cls(
            enabled=enabled,
            api_key=read_secret("OPENROUTER_API_KEY"),
            base_url=env("AI_BASE_URL", "https://openrouter.ai/api/v1"),
            model=read_secret("AI_MODEL"),
            max_tokens=int(env("SQL_PRESENTATION_MAX_TOKENS", "300")),
            request_timeout=float(env("AI_REQUEST_TIMEOUT", "30.0")),
            retries=int(env("AI_RETRIES", "3")),
            backoff_base=float(env("AI_BACKOFF_BASE", "2.0")),
            disable_reasoning=disable_reasoning,
            max_sample_rows=int(env("SQL_PRESENTATION_MAX_SAMPLE_ROWS", "20")),
            max_cell_chars=int(env("SQL_PRESENTATION_MAX_CELL_CHARS", "80")),
            max_payload_bytes=int(env("SQL_PRESENTATION_MAX_PAYLOAD_BYTES", "20000")),
            max_sql_chars=int(env("SQL_PRESENTATION_MAX_SQL_CHARS", "2000")),
        )

    @staticmethod
    def _load_system_prompt() -> str:
        """Read the presentation-planner system prompt from disk."""
        try:
            return _PRESENTATION_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning(
                "Presentation system prompt not found at %s; disabling LLM planning",
                _PRESENTATION_SYSTEM_PROMPT_PATH,
            )
            return ""

    async def decide(
        self,
        *,
        question: str | None = None,
        sql: str = "",
        rows: list[dict[str, Any]] | None = None,
    ) -> PresentationDecision | None:
        """
        Decide how a query result should be presented.

        Args:
            question: The user's original question, if one is available.
            sql: The executed SQL statement.
            rows: The (post-masking) result rows. Empty rows produce no decision.

        Returns:
            A validated ``PresentationDecision``, or ``None`` when the service
            is disabled/unconfigured, the result is empty, or the LLM response
            could not be used safely (callers then fall back to the table).

        Raises:
            Only ``asyncio.CancelledError`` propagates; every other failure
            degrades to ``None`` so query results are never blocked.

        """
        if not self._enabled:
            return None
        if not self._api_key:
            logger.info("Presentation planning disabled: no OPENROUTER_API_KEY configured")
            return None
        rows = rows or []
        if not rows:
            return None

        context = self._build_context(question=question, sql=sql, rows=rows)
        total_timeout = self._request_timeout * max(1, self._retries)

        try:
            response = await asyncio.wait_for(self._complete(context["prompt"]), timeout=total_timeout)
        except asyncio.TimeoutError:
            logger.warning("Presentation decision timed out after %.0fs; falling back to table", total_timeout)
            return self._fallback_decision("llm request timed out")
        except PresentationServiceError as exc:
            logger.warning("Presentation decision failed; falling back to table: %s", exc)
            return self._fallback_decision(str(exc))

        content_text = self._extract_content_text(response)
        try:
            return self._validate_decision(content_text, rows=context["sample_rows"])
        except _InvalidDecisionError as exc:
            logger.warning("Presentation decision rejected; falling back to table: %s", exc)
            return self._fallback_decision(str(exc))
        except (ValueError, TypeError) as exc:
            logger.warning("Malformed presentation response; falling back to table: %s", exc)
            return self._fallback_decision("malformed llm response")

    # ------------------------------------------------------------------ #
    # Introspection payload helpers
    # ------------------------------------------------------------------ #

    def _build_context(
        self,
        *,
        question: str | None,
        sql: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the bounded prompt context and the truncated sample rows."""
        sample_limit = self._max_sample_rows
        cell_limit = self._max_cell_chars
        byte_limit = self._max_payload_bytes

        columns = self._infer_columns(rows, sample_limit)
        sample = [self._truncate_row(row, cell_limit) for row in rows[:sample_limit]]
        truncated = len(rows) > len(sample)

        while len(sample) > 0:
            encoded = json.dumps(sample, default=str, separators=(",", ":"))
            if len(encoded.encode("utf-8")) <= byte_limit:
                break
            sample.pop()
            truncated = True

        prompt = self._render_user_prompt(
            question=question,
            sql=sql,
            columns=columns,
            rows=sample,
            total_rows=len(rows),
            truncated=truncated,
        )
        return {"prompt": prompt, "sample_rows": sample, "columns": columns}

    def _infer_columns(
        self, rows: list[dict[str, Any]], sample_limit: int
    ) -> list[dict[str, Any]]:
        """Infer per-column metadata (name, JSON-ish type, nullability)."""
        keys: list[str] = []
        for row in rows[: max(1, sample_limit)]:
            for key in row:
                if key not in keys:
                    keys.append(key)

        columns: list[dict[str, Any]] = []
        for key in keys:
            values = [row.get(key) for row in rows[:sample_limit]]
            columns.append(
                {
                    "name": key,
                    "type": self._column_type(values),
                    "nullable": any(value is None for value in values),
                }
            )
        return columns

    @staticmethod
    def _column_type(values: list[Any]) -> str:
        """Classify a column by its non-null sample values."""
        non_null = [value for value in values if value is not None]
        if not non_null:
            return "unknown"
        if all(isinstance(value, bool) for value in non_null):
            return "boolean"
        if all(isinstance(value, int) for value in non_null):
            return "integer"
        if all(isinstance(value, (int, float)) for value in non_null):
            return "number"
        return "string"

    @staticmethod
    def _truncate_row(row: dict[str, Any], cell_limit: int) -> dict[str, Any]:
        """Truncate long string cells so the payload stays small and focused."""
        truncated: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, str) and len(value) > cell_limit:
                truncated[key] = value[: max(0, cell_limit - 1)] + "…"
            else:
                truncated[key] = value
        return truncated

    @classmethod
    def _looks_like_question_answer(
        cls, *, question: str | None, rows: list[dict[str, Any]]
    ) -> bool:
        """
        Detect the "question-answering" result shape.

        A handful of rows that carry an aggregate-named column (e.g. a ``*_count``
        next to an entity column) usually answer one factual question directly and
        read better as prose than as a table.
        """
        if not question or not rows or len(rows) > _MAX_QUESTION_ANSWER_ROWS:
            return False
        column_names = " ".join(key for row in rows for key in row)
        return bool(_SUMMARY_COLUMN_PATTERN.search(column_names))

    def _render_user_prompt(
        self,
        *,
        question: str | None,
        sql: str,
        columns: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        total_rows: int,
        truncated: bool,
    ) -> str:
        """Render the bounded decision payload (question/SQL/columns/sample)."""
        lines = ["QUESTION:"]
        lines.append((question or "(none set)").strip() or "(none set)")
        lines.append("")
        lines.append("SQL:")
        lines.append(sql[: self._max_sql_chars].strip())
        lines.append("")
        lines.append("COLUMNS:")
        if columns:
            lines.extend(
                " - name: {name}, type: {type}, nullable: {nullable}".format(
                    name=col["name"], type=col["type"], nullable="yes" if col["nullable"] else "no"
                )
                for col in columns
            )
        else:
            lines.append(" (columns could not be inferred)")
        lines.append("")
        shown = len(rows)
        lines.append(f"ROWS (shown: {shown} of {total_rows}; truncated: {'yes' if truncated else 'no'}):")
        lines.append(json.dumps(rows, default=str, separators=(",", ":")))
        if self._looks_like_question_answer(question=question, rows=rows):
            lines.append("")
            lines.append(_QUESTION_SUMMARY_HINT)
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # LLM request
    # ------------------------------------------------------------------ #

    async def _complete(self, user_prompt: str) -> dict[str, Any]:
        """Perform the chat-completion request with retry on transient failures."""
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": self._max_tokens,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        if self._disable_reasoning:
            body["reasoning"] = {"enabled": False}

        last_error: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                if self._http_post is not None:
                    result = self._http_post(url, headers, body)
                    response = await result if asyncio.iscoroutine(result) else result
                else:
                    async with httpx.AsyncClient(timeout=self._request_timeout) as client:
                        response = await client.post(url, headers=headers, json=body)
                if response.status_code in {408, 429}:
                    raise _RetryableResponseError(f"retryable status {response.status_code}")
                if response.status_code >= 400:
                    raise PresentationServiceError(f"llm http error: status {response.status_code}")
                data = response.json()
                content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                if not isinstance(content, str) or not content.strip():
                    raise _RetryableResponseError("empty completion content")
                return data
            except _RetryableResponseError as exc:
                last_error = exc
                if attempt < self._retries:
                    await asyncio.sleep(self._backoff_base**attempt)
            except (httpx.HTTPError, ValueError) as exc:
                raise PresentationServiceError(f"llm request failed: {exc}") from exc
            except asyncio.CancelledError:
                raise
        raise PresentationServiceError(
            f"llm request failed after {self._retries} attempts: {last_error}"
        )

    @staticmethod
    def _extract_content_text(response: dict[str, Any]) -> str:
        """Extract the assistant message text from a chat-completion response."""
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise PresentationServiceError("llm response missing choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        reasoning = message.get("reasoning")
        text = content if isinstance(content, str) and content.strip() else reasoning
        if not isinstance(text, str) or not text.strip():
            raise PresentationServiceError("llm response had no usable content")
        return text.strip()

    # ------------------------------------------------------------------ #
    # Structured decision parsing + safety guards
    # ------------------------------------------------------------------ #

    def _validate_decision(
        self, content_text: str, *, rows: list[dict[str, Any]]
    ) -> PresentationDecision:
        """Parse and validate the LLM's structured decision against guards."""
        data = self._parse_json_object(content_text)
        raw_format = data.get("format")
        if not isinstance(raw_format, str):
            raise _InvalidDecisionError("missing 'format'")

        fmt = raw_format.strip().lower()
        if fmt not in SUPPORTED_FORMATS:
            raise _InvalidDecisionError(f"unsupported format '{fmt}'")

        reason = data.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = None

        content: str | None = None
        if fmt == "paragraph":
            raw_content = data.get("content")
            if not isinstance(raw_content, str) or not raw_content.strip():
                raise _InvalidDecisionError("paragraph requires non-empty 'content'")
            content = raw_content.strip()
        else:
            raw_content = data.get("content")
            if isinstance(raw_content, str) and raw_content.strip():
                content = raw_content.strip()

        if fmt == "chart" and not self._has_numeric_column(rows):
            raise _InvalidDecisionError("'chart' plan has no numeric column to plot")

        if content and not self._integers_fit(content, rows):
            raise _InvalidDecisionError("content invents values not present in the result")

        column_labels = self._validated_column_labels(data.get("column_labels"), rows=rows)

        return PresentationDecision(
            format=fmt,
            content=content,
            reason=reason,
            column_labels=column_labels,
        )

    @classmethod
    def _validated_column_labels(
        cls, raw: object, *, rows: list[dict[str, Any]]
    ) -> dict[str, str]:
        """
        Keep only labels that name a real result column and are safe to display.

        Labels are a display hint, so anything unexpected (unknown columns,
        non-string or control-laden values, over-long text) is dropped rather
        than failing the whole decision. The raw column names remain the keys;
        the UI falls back to a deterministic humanization when a label is absent.
        """
        if not isinstance(raw, dict) or not rows:
            return {}
        valid_columns: set[str] = set().union(*(row.keys() for row in rows))
        labels: dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or key not in valid_columns:
                continue
            if not isinstance(value, str):
                continue
            cleaned = "".join(
                ch if ch.isprintable() else (" " if ch.isspace() else "")
                for ch in value
            )
            cleaned = " ".join(cleaned.split())
            if not cleaned:
                continue
            labels[key] = cleaned[:_MAX_COLUMN_LABEL_CHARS]
        return labels

    @staticmethod
    def _has_numeric_column(rows: list[dict[str, Any]]) -> bool:
        """Return whether any row (excluding booleans) contains a number."""
        for row in rows:
            for value in row.values():
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    return True
        return False

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any]:
        """Parse a JSON object, tolerating code fences around it."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned[3:].split("```", 1)[0]
            if cleaned.lstrip().startswith("json"):
                cleaned = cleaned.lstrip()[4:]
            cleaned = cleaned.strip()
        try:
            parsed = json.loads(cleaned)
        except ValueError:
            match = _JSON_OBJECT_RE.search(cleaned)
            if not match:
                raise _InvalidDecisionError("response is not JSON") from None
            try:
                parsed = json.loads(match.group(0))
            except ValueError as exc:
                raise _InvalidDecisionError("response is not JSON") from exc
        if not isinstance(parsed, dict):
            raise _InvalidDecisionError("response is not a JSON object")
        return parsed

    @classmethod
    def _integers_fit(cls, content: str, rows: list[dict[str, Any]]) -> bool:
        """
        Reject wording that invents numbers absent from the data.

        Only integer-like tokens are guard-checked (e.g. counts, sums, years);
        decimal tokens are ignored to avoid false positives from re-formatting.
        The number of rows shown is itself a fact of the result, so an integer
        equal to the row count is also permitted (e.g. "2 departments").
        """
        tokens = _INTEGER_TOKEN_RE.findall(content)
        integer_tokens = {
            normalized
            for token in tokens
            if "." not in token and (normalized := re.sub(r"[^0-9]", "", token))
        }
        if not integer_tokens:
            return True

        allowed = {str(len(rows))}
        for row in rows:
            for value in row.values():
                allowed.update(cls._numeric_forms(value))
        return integer_tokens.issubset(allowed)

    @staticmethod
    def _numeric_forms(value: object) -> set[str]:
        """Normalize a cell value into comparable integer string forms."""
        if isinstance(value, bool):
            return set()
        if isinstance(value, int):
            return {str(value)}
        if isinstance(value, float):
            return {str(int(value))} if float(value).is_integer() else set()
        if isinstance(value, str):
            stripped = value.strip()
            if re.fullmatch(r"\d[\d,]*", stripped):
                return {re.sub(r"[^0-9]", "", stripped)}
        return set()

    # ------------------------------------------------------------------ #
    # Fallbacks
    # ------------------------------------------------------------------ #

    @staticmethod
    def _fallback_decision(reason: str) -> PresentationDecision:
        """Build the safe default decision used whenever planning cannot proceed."""
        return PresentationDecision(format="table", content=None, reason=reason)
