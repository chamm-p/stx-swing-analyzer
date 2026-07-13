"""Tests für robustes JSON-Parsing der LLM-Antworten."""

import pytest

from app.llm.client import LLMError, _parse_json


def test_reines_json():
    assert _parse_json('{"score": 0.5}') == {"score": 0.5}


def test_markdown_fences():
    assert _parse_json('```json\n{"score": 0.5}\n```') == {"score": 0.5}
    assert _parse_json('```\n{"a": 1}\n```') == {"a": 1}


def test_erklaertext_drumherum():
    text = 'Hier ist die Analyse: {"score": -0.3, "label": "bearish"} Hoffe das hilft!'
    assert _parse_json(text)["label"] == "bearish"


def test_kein_json_wirft_llmerror():
    with pytest.raises(LLMError):
        _parse_json("Es tut mir leid, ich kann das nicht bewerten.")
