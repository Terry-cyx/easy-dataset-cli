"""Unit tests for easyds.core.dataset_eval and its helpers.

Covers schema-rule detection, task-type auto-detection, post-processing
--fix handlers, attribution wiring, and report aggregation. The LLM judge
is exercised with a mocked ``requests.post`` so we never hit a real API.

The case-2 sentiment failure is included verbatim as a golden fixture
at ``tests/fixtures/eval/case2-broken-sentiment.json`` — if this file
ever changes, please update the rationale in the docstring below.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from easyds.core import (
    dataset_eval,
    eval_attribution,
    eval_fixes,
    eval_judge,
    session as session_mod,
)


FIXTURES = Path(__file__).parent / "fixtures" / "eval"


# ────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def good_alpaca_qa(tmp_path):
    records = [
        {
            "instruction": f"Explain concept {i}",
            "input": "",
            "output": f"Concept {i} is defined as a well-formed description "
                      f"that is at least a few dozen characters long.",
        }
        for i in range(10)
    ]
    p = tmp_path / "good.json"
    p.write_text(json.dumps(records, ensure_ascii=False))
    return p


@pytest.fixture
def case2_broken_fixture():
    return FIXTURES / "case2-broken-sentiment.json"


@pytest.fixture
def placeholder_leak_sharegpt(tmp_path):
    convs = [{
        "messages": [
            {"role": "system", "content": "You are a tutor for {{student}}"},
            {"role": "user", "content": "What is gravity?"},
            {"role": "assistant", "content": "It's the force that pulls things down."},
        ],
    }]
    p = tmp_path / "sharegpt.json"
    p.write_text(json.dumps(convs, ensure_ascii=False))
    return p


@pytest.fixture
def chunks_source(tmp_path):
    """A minimal chunks-list JSON to drive --fix chunk-join."""
    chunks = [
        {"name": f"reviews-part-{i+1}", "content": f"review body {i+1}"}
        for i in range(8)
    ]
    p = tmp_path / "chunks.json"
    p.write_text(json.dumps(chunks, ensure_ascii=False))
    return p


# ────────────────────────────────────────────────────────────────────
# Task-type auto-detection
# ────────────────────────────────────────────────────────────────────


class TestDetectTaskType:
    def test_auto_detects_classification_from_identical_instructions(self):
        recs = [{"instruction": "classify", "input": "review 1", "output": "pos"},
                {"instruction": "classify", "input": "review 2", "output": "neg"}]
        assert dataset_eval.detect_task_type(recs, "alpaca") == "classification"

    def test_auto_detects_vqa_from_image_input(self):
        recs = [{"instruction": "describe", "input": "image://car1.png", "output": "a car"}]
        assert dataset_eval.detect_task_type(recs, "alpaca") == "vqa"

    def test_auto_detects_multi_turn_from_sharegpt(self):
        recs = [{"messages": [{"role": "user", "content": "hi"}]}]
        assert dataset_eval.detect_task_type(recs, "sharegpt") == "multi-turn"

    def test_auto_detects_qa_as_default(self):
        recs = [{"instruction": f"Q{i}", "input": "", "output": f"A{i}"}
                for i in range(3)]
        assert dataset_eval.detect_task_type(recs, "alpaca") == "qa"

    def test_explicit_override_wins(self):
        recs = [{"instruction": "same", "input": "a", "output": "b"},
                {"instruction": "same", "input": "c", "output": "d"}]
        # Auto would say classification; override forces qa
        assert dataset_eval.detect_task_type(
            recs, "alpaca", explicit="qa"
        ) == "qa"


# ────────────────────────────────────────────────────────────────────
# Schema rules
# ────────────────────────────────────────────────────────────────────


class TestSchemaRules:
    def test_good_qa_passes_all_hard_rules(self, good_alpaca_qa):
        report = dataset_eval.evaluate(good_alpaca_qa)
        hard_fails = [c for c in report.checks if c.verdict == "fail"]
        assert hard_fails == []
        assert report.verdict in ("pass", "warn")
        assert report.task_type == "qa"

    def test_case2_broken_fails_input_empty_and_double_encoded(self, case2_broken_fixture):
        """The golden fixture: case-2 sentiment broken export.

        Must fail BOTH input_empty_rate (because task auto-detects as
        classification) AND output_double_encoded (because the answer
        is a JSON-stringified array). Without both signals, the
        feedback loop can't attribute the failure correctly.
        """
        report = dataset_eval.evaluate(case2_broken_fixture)
        assert report.task_type == "classification"
        assert report.verdict == "fail"
        assert report.exit_code == 2

        names = {c.name: c for c in report.checks}
        assert names["input_empty_rate"].verdict == "fail"
        assert names["input_empty_rate"].value == 1.0
        assert names["output_double_encoded"].verdict == "fail"
        assert names["output_double_encoded"].value == 1.0

    def test_case2_attribution_points_at_export_and_post_process(self, case2_broken_fixture):
        report = dataset_eval.evaluate(case2_broken_fixture)
        rules = {a["rule"]: a for a in report.attribution}
        assert "input_empty_rate" in rules
        assert rules["input_empty_rate"]["step"] == "export"
        assert rules["input_empty_rate"].get("fix") == "chunk-join"
        assert "output_double_encoded" in rules
        assert rules["output_double_encoded"]["step"] == "post-process"
        assert rules["output_double_encoded"].get("fix") == "unwrap-labels"

    def test_placeholder_leak_detected_in_sharegpt(self, placeholder_leak_sharegpt):
        report = dataset_eval.evaluate(placeholder_leak_sharegpt)
        names = {c.name: c for c in report.checks}
        assert names["placeholder_leak_rate"].verdict == "fail"
        assert "{{student}}" in names["placeholder_leak_rate"].message

    def test_multi_turn_malformed_fails(self, tmp_path):
        # Only 2 messages (needs ≥3) and starts with assistant (wrong)
        bad = [{"messages": [
            {"role": "assistant", "content": "out of order"},
            {"role": "user", "content": "why?"},
        ]}]
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(bad))
        report = dataset_eval.evaluate(p)
        names = {c.name for c in report.checks if c.verdict == "fail"}
        assert "multi_turn_malformed" in names

    def test_sample_size_too_small_is_warn_by_default(self, tmp_path):
        small = [{"instruction": f"q{i}", "input": "", "output": f"a{i}"}
                 for i in range(3)]
        p = tmp_path / "small.json"
        p.write_text(json.dumps(small))
        report = dataset_eval.evaluate(p)
        ssize = next(c for c in report.checks if c.name == "sample_size_too_small")
        assert ssize.verdict == "warn"
        assert report.exit_code == 1  # warn-only

    def test_strict_promotes_warns_to_fails(self, tmp_path):
        small = [{"instruction": "q", "input": "", "output": "a"}]
        p = tmp_path / "small.json"
        p.write_text(json.dumps(small))
        report = dataset_eval.evaluate(p, strict=True)
        ssize = next(c for c in report.checks if c.name == "sample_size_too_small")
        assert ssize.verdict == "fail"
        assert report.exit_code == 2

    def test_input_empty_rule_does_not_fire_for_qa_task(self, good_alpaca_qa):
        """QA tasks with empty input should pass, not fail input_empty."""
        report = dataset_eval.evaluate(good_alpaca_qa)
        names = {c.name for c in report.checks}
        assert "input_empty_rate" not in names  # rule is skipped entirely for qa


# ────────────────────────────────────────────────────────────────────
# Post-processing fixes
# ────────────────────────────────────────────────────────────────────


class TestFixChunkJoin:
    def test_fills_empty_input_from_chunks(self, case2_broken_fixture, chunks_source):
        records, ft = eval_fixes.load_records(case2_broken_fixture)
        new, summary = eval_fixes.fix_chunk_join(records, chunks_source)
        assert summary["updated"] == 8
        # Every row should now have its chunk body
        for i, r in enumerate(new):
            assert r["input"] == f"review body {i+1}"
        # Original list must not be mutated
        assert records[0]["input"] == ""

    def test_reports_unmatched(self, chunks_source, tmp_path):
        recs = [{"instruction": "x", "input": "", "output": "y",
                 "chunkName": "does-not-exist"}]
        p = tmp_path / "r.json"
        p.write_text(json.dumps(recs))
        records, _ = eval_fixes.load_records(p)
        new, summary = eval_fixes.fix_chunk_join(records, chunks_source)
        assert summary["updated"] == 0
        assert "does-not-exist" in summary["unmatched_chunks"]


class TestFixUnwrapLabels:
    def test_unwraps_single_element_array(self, tmp_path):
        recs = [{"instruction": "x", "input": "", "output": '["positive"]'}]
        new, summary = eval_fixes.fix_unwrap_labels(recs)
        assert new[0]["output"] == "positive"
        assert summary["updated"] == 1

    def test_joins_multi_element_array(self):
        recs = [{"instruction": "x", "input": "", "output": '["a", "b"]'}]
        new, summary = eval_fixes.fix_unwrap_labels(recs)
        assert new[0]["output"] == "a, b"

    def test_idempotent_on_plain_string(self):
        recs = [{"instruction": "x", "input": "", "output": "positive"}]
        new, summary = eval_fixes.fix_unwrap_labels(recs)
        assert new[0]["output"] == "positive"
        assert summary["updated"] == 0


class TestFixRenderPlaceholders:
    def test_substitutes_in_sharegpt_messages(self, placeholder_leak_sharegpt):
        records, _ = eval_fixes.load_records(placeholder_leak_sharegpt)
        new, summary = eval_fixes.fix_render_placeholders(
            records, {"student": "高中生"}
        )
        assert summary["substitutions"] == 1
        assert "{{student}}" not in new[0]["messages"][0]["content"]
        assert "高中生" in new[0]["messages"][0]["content"]

    def test_reports_unresolved_placeholders(self, placeholder_leak_sharegpt):
        records, _ = eval_fixes.load_records(placeholder_leak_sharegpt)
        new, summary = eval_fixes.fix_render_placeholders(records, {})
        assert "student" in summary["unresolved_placeholders"]


# ────────────────────────────────────────────────────────────────────
# LLM judge (mocked — no real API calls)
# ────────────────────────────────────────────────────────────────────


class TestLLMJudge:
    def _fake_response(self, content: str) -> mock.Mock:
        resp = mock.Mock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        return resp

    def test_judge_parses_scores_and_aggregates(self, good_alpaca_qa):
        records, _ = eval_fixes.load_records(good_alpaca_qa)
        fake = self._fake_response(
            '{"groundedness": 5, "correctness": 4, "clarity": 5, "issues": []}'
        )
        with mock.patch("easyds.core.eval_judge.requests.post", return_value=fake):
            out = eval_judge.judge_records(
                records,
                model_config={
                    "endpoint": "https://api.example.com/v1",
                    "apiKey": "sk-fake",
                    "modelId": "judge-m",
                },
                sample_size=3,
            )
        assert out["sample_size"] == 3
        assert out["mean"]["groundedness"] == 5.0
        assert out["mean"]["correctness"] == 4.0
        assert len(out["per_record"]) == 3

    def test_judge_tolerates_markdown_fenced_json(self, good_alpaca_qa):
        records, _ = eval_fixes.load_records(good_alpaca_qa)
        fake = self._fake_response(
            '```json\n{"groundedness": 3, "correctness": 3, "clarity": 3, "issues":["ok"]}\n```'
        )
        with mock.patch("easyds.core.eval_judge.requests.post", return_value=fake):
            out = eval_judge.judge_records(
                records,
                model_config={
                    "endpoint": "https://api.example.com/v1",
                    "apiKey": "sk-fake",
                    "modelId": "judge-m",
                },
                sample_size=2,
            )
        assert out["mean"]["groundedness"] == 3.0

    def test_judge_errors_are_collected_not_raised(self, good_alpaca_qa):
        records, _ = eval_fixes.load_records(good_alpaca_qa)
        with mock.patch(
            "easyds.core.eval_judge.requests.post",
            side_effect=RuntimeError("network down"),
        ):
            out = eval_judge.judge_records(
                records,
                model_config={
                    "endpoint": "https://api.example.com/v1",
                    "apiKey": "sk-fake",
                    "modelId": "judge-m",
                },
                sample_size=2,
            )
        # All per-record attempts failed, but the function still returns
        assert len(out["errors"]) == 2
        assert out["per_record"] == []

    def test_judge_skipped_when_model_config_missing_fields(self, good_alpaca_qa):
        records, _ = eval_fixes.load_records(good_alpaca_qa)
        out = eval_judge.judge_records(
            records,
            model_config={"endpoint": "x"},  # missing apiKey + modelId
            sample_size=2,
        )
        assert out["sample_size"] == 0
        assert any("endpoint/apiKey/modelId" in e for e in out["errors"])

    def test_evaluate_with_llm_judge_promotes_low_scores(self, good_alpaca_qa):
        fake = mock.Mock()
        fake.status_code = 200
        fake.json.return_value = {
            "choices": [{"message": {"content":
                '{"groundedness": 2, "correctness": 1, "clarity": 2, "issues":["bad"]}'
            }}]
        }
        with mock.patch("easyds.core.eval_judge.requests.post", return_value=fake):
            report = dataset_eval.evaluate(
                good_alpaca_qa,
                llm_judge=True,
                judge_model_config={
                    "endpoint": "https://api.example.com/v1",
                    "apiKey": "sk-fake",
                    "modelId": "judge-m",
                },
                judge_sample_size=2,
            )
        names = {c.name: c for c in report.checks}
        assert names["judge_groundedness_low"].verdict == "fail"
        assert names["judge_correctness_low"].verdict == "fail"
        assert report.verdict == "fail"


# ────────────────────────────────────────────────────────────────────
# Attribution table completeness
# ────────────────────────────────────────────────────────────────────


class TestAttributionCoverage:
    def test_every_hard_rule_has_attribution(self):
        """If we fail a rule without telling the agent where to fix it,
        the feedback loop is broken. Every hard-fail rule must map."""
        hard_fail_rules = [
            "instruction_empty_rate",
            "input_empty_rate",
            "output_empty_rate",
            "output_double_encoded",
            "placeholder_leak_rate",
            "multi_turn_malformed",
            "judge_groundedness_low",
            "judge_correctness_low",
            "judge_clarity_low",
        ]
        missing = [r for r in hard_fail_rules if eval_attribution.attribute(r) is None]
        assert missing == [], f"Missing attribution for: {missing}"

    def test_attribution_entries_have_required_keys(self):
        for rule, entry in eval_attribution.ATTRIBUTION.items():
            assert "step" in entry, f"{rule} missing step"
            assert "command" in entry, f"{rule} missing command"
            assert "suggested_change" in entry, f"{rule} missing suggested_change"
            assert "root_cause_hint" in entry, f"{rule} missing root_cause_hint"


# ────────────────────────────────────────────────────────────────────
# Session history
# ────────────────────────────────────────────────────────────────────


class TestEvalHistory:
    def test_append_and_retrieve_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            session_mod, "_session_dir", lambda: tmp_path / "sess"
        )
        # Seed with a project
        session_mod.save_session({"current_project_id": "proj-1"})

        session_mod.append_eval_history({"file": "a.json", "verdict": "fail"})
        session_mod.append_eval_history({"file": "a.json", "verdict": "pass"})

        hist = session_mod.get_eval_history()
        assert len(hist) == 2
        assert hist[0]["verdict"] == "fail"
        assert hist[1]["verdict"] == "pass"

    def test_history_is_per_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            session_mod, "_session_dir", lambda: tmp_path / "sess"
        )
        session_mod.save_session({"current_project_id": "proj-A"})
        session_mod.append_eval_history({"file": "A.json", "verdict": "pass"})
        session_mod.save_session({
            **session_mod.load_session(),
            "current_project_id": "proj-B",
        })
        session_mod.append_eval_history({"file": "B.json", "verdict": "fail"})

        assert len(session_mod.get_eval_history("proj-A")) == 1
        assert len(session_mod.get_eval_history("proj-B")) == 1
        assert session_mod.get_eval_history("proj-A")[0]["file"] == "A.json"

    def test_history_trimmed_to_max(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            session_mod, "_session_dir", lambda: tmp_path / "sess"
        )
        session_mod.save_session({"current_project_id": "proj"})
        for i in range(session_mod.EVAL_HISTORY_MAX + 5):
            session_mod.append_eval_history({"file": f"{i}.json", "verdict": "pass"})
        hist = session_mod.get_eval_history()
        assert len(hist) == session_mod.EVAL_HISTORY_MAX
        # Oldest entries evicted
        assert hist[0]["file"] == "5.json"
