"""Tests for deterministic Chinese overlay preparation, normalization, and publication."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from idea_pipeline.translator.zh import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    TARGET_LANGUAGE,
    TranslationJob,
    TranslationTarget,
    build_copilot_command,
    build_parser,
    build_prompt,
    discover_reports,
    extract_structure,
    main,
    normalize_translation,
    prepare_translation,
    protected_terms,
    resolve_jobs,
    strip_front_matter,
    translate_job,
    validate_translation,
)

SOURCE_REPORT = """# Reddit Builder Intelligence Report - 2026-08-05

## 1. Executive Value Summary

Open-source **OpenValve** shipped a $50 MRR tool. See [Open project](https://open-valve.com/).

## 2. New Projects and Direct Links

| Project | Type | Stage |
|---|---|---|
| OpenValve | Repository | Prototype |
| PrintMap | SaaS | Launched |

![Chart](https://i.redd.it/example.png)
"""

TRANSLATED_BODY = """# Reddit 建造者情报报告 - 2026-08-05

## 1. 执行摘要

开源项目 **OpenValve** 做出了月收入 50 美元的工具。参见 [Open project](https://open-valve.com/)。

## 2. 新项目与直达链接

| 项目 | 类型 | 阶段 |
|---|---|---|
| OpenValve | 代码仓库 | 原型 |
| PrintMap | SaaS | 已发布 |

![图表](https://i.redd.it/example.png)
"""


def write_report(directory: Path, date_text: str, content: str = SOURCE_REPORT) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{date_text}.md"
    path.write_text(content, encoding="utf-8")
    return path


def target_for(path: Path) -> TranslationTarget:
    return TranslationTarget(report_path=path, report_date=date.fromisoformat(path.stem))


def padded(body: str) -> str:
    """Extend a candidate past the minimum Chinese character floor."""
    filler = "这份报告记录了创始人验证过程中的具体证据与缺口。" * 20
    return f"{body}\n{filler}\n"


class TestStructure:
    def test_extracts_headings_tables_and_links(self) -> None:
        structure = extract_structure(SOURCE_REPORT)

        assert structure.headings == (
            (1, "Reddit Builder Intelligence Report - 2026-08-05"),
            (2, "1. Executive Value Summary"),
            (2, "2. New Projects and Direct Links"),
        )
        assert structure.tables == ((3, 2),)
        assert structure.urls == ("https://open-valve.com/", "https://i.redd.it/example.png")
        assert structure.image_urls == ("https://i.redd.it/example.png",)

    def test_ignores_tables_inside_fenced_code(self) -> None:
        text = "# T\n\n```\n| a | b |\n|---|---|\n| 1 | 2 |\n```\n"

        assert extract_structure(text).tables == ()

    def test_strip_front_matter_returns_flat_mapping(self) -> None:
        document = '---\nlang: "zh-CN"\nsource_sha256: "abc"\n---\n\n# 标题\n'

        front_matter, body = strip_front_matter(document)

        assert front_matter == {"lang": "zh-CN", "source_sha256": "abc"}
        assert body.strip() == "# 标题"

    def test_protected_terms_capture_verbatim_spans(self) -> None:
        terms = protected_terms("Use `npm ci` on r/SaaS for $50 MRR at https://a.example/")

        assert "https://a.example/" in terms["urls"]
        assert "npm ci" in terms["inline_code"]
        assert "r/SaaS" in terms["subreddits"]


class TestNormalization:
    def test_converts_ascii_punctuation_after_chinese_text(self) -> None:
        normalized, messages = normalize_translation("作者自述, 增长停滞; 原因不明.")

        assert normalized == "作者自述，增长停滞；原因不明。"
        assert any("full width" in message for message in messages)

    def test_inserts_spacing_between_chinese_and_latin(self) -> None:
        normalized, _messages = normalize_translation("月收入50美元来自Bing流量")

        assert normalized == "月收入 50 美元来自 Bing 流量"

    def test_leaves_code_urls_and_link_targets_untouched(self) -> None:
        text = "参见 [文档](https://a.example/a,b) 与 `git commit -m 'x, y'` 说明"

        normalized, _messages = normalize_translation(text)

        assert "https://a.example/a,b" in normalized
        assert "`git commit -m 'x, y'`" in normalized

    def test_removes_spaces_before_full_width_punctuation(self) -> None:
        normalized, _messages = normalize_translation("这是结论 。")

        assert normalized == "这是结论。"

    def test_keeps_full_width_punctuation_flush_against_chinese(self) -> None:
        normalized, _messages = normalize_translation("创业想法（150）和 SaaS 构建（203）三个维度")

        assert normalized == "创业想法（150）和 SaaS 构建（203）三个维度"

    def test_preserves_decimal_numbers_and_domains(self) -> None:
        normalized, _messages = normalize_translation("版本 1.2 部署在 printm.app 上")

        assert "1.2" in normalized
        assert "printm.app" in normalized


class TestValidation:
    def test_accepts_faithful_translation(self, tmp_path: Path) -> None:
        candidate = tmp_path / "translation.md"
        candidate.write_text(padded(TRANSLATED_BODY), encoding="utf-8")

        assert validate_translation(candidate, structure=extract_structure(SOURCE_REPORT)) == []

    def test_rejects_dropped_link(self, tmp_path: Path) -> None:
        candidate = tmp_path / "translation.md"
        candidate.write_text(
            padded(TRANSLATED_BODY.replace("[Open project](https://open-valve.com/)", "开源项目")),
            encoding="utf-8",
        )

        errors = validate_translation(candidate, structure=extract_structure(SOURCE_REPORT))

        assert any("drops 1 source link" in error for error in errors)

    def test_rejects_invented_link(self, tmp_path: Path) -> None:
        candidate = tmp_path / "translation.md"
        candidate.write_text(
            padded(TRANSLATED_BODY + "\n参见 [额外](https://invented.example/)\n"),
            encoding="utf-8",
        )

        errors = validate_translation(candidate, structure=extract_structure(SOURCE_REPORT))

        assert any("adds link(s) absent from the source" in error for error in errors)

    def test_rejects_changed_table_shape(self, tmp_path: Path) -> None:
        candidate = tmp_path / "translation.md"
        candidate.write_text(
            padded(TRANSLATED_BODY.replace("| PrintMap | SaaS | 已发布 |\n", "")),
            encoding="utf-8",
        )

        errors = validate_translation(candidate, structure=extract_structure(SOURCE_REPORT))

        assert any("row count differs" in error for error in errors)

    def test_rejects_untranslated_headings(self, tmp_path: Path) -> None:
        candidate = tmp_path / "translation.md"
        candidate.write_text(padded(SOURCE_REPORT), encoding="utf-8")

        errors = validate_translation(candidate, structure=extract_structure(SOURCE_REPORT))

        assert any("was not translated" in error for error in errors)

    def test_rejects_untranslated_table_header(self, tmp_path: Path) -> None:
        candidate = tmp_path / "translation.md"
        candidate.write_text(
            padded(TRANSLATED_BODY.replace("| 项目 | 类型 | 阶段 |", "| Project | Type | Stage |")),
            encoding="utf-8",
        )

        errors = validate_translation(candidate, structure=extract_structure(SOURCE_REPORT))

        assert any("table header row(s) were not translated" in error for error in errors)

    def test_reports_translationese_as_warnings_only(self, tmp_path: Path) -> None:
        candidate = tmp_path / "translation.md"
        candidate.write_text(
            padded(TRANSLATED_BODY + "\n这个工具被认为是一个非常好的用于开发者的产品的方案。\n"),
            encoding="utf-8",
        )
        warnings: list[str] = []

        errors = validate_translation(
            candidate, structure=extract_structure(SOURCE_REPORT), warnings=warnings
        )

        assert errors == []
        assert any("被-passive" in warning for warning in warnings)

    def test_rejects_mostly_english_candidate(self, tmp_path: Path) -> None:
        candidate = tmp_path / "translation.md"
        candidate.write_text(TRANSLATED_BODY, encoding="utf-8")

        errors = validate_translation(candidate, structure=extract_structure(SOURCE_REPORT))

        assert any("Chinese characters" in error for error in errors)


class TestJobSelection:
    def test_discovers_reports_newest_first(self, tmp_path: Path) -> None:
        write_report(tmp_path, "2026-08-04")
        write_report(tmp_path, "2026-08-05")

        targets = discover_reports(tmp_path)

        assert [target.date_text for target in targets] == ["2026-08-05", "2026-08-04"]

    def test_ignores_the_translations_subdirectory(self, tmp_path: Path) -> None:
        write_report(tmp_path, "2026-08-05")
        write_report(tmp_path / "zh", "2026-08-05")

        assert [target.date_text for target in discover_reports(tmp_path)] == ["2026-08-05"]

    def test_unknown_date_is_rejected(self, tmp_path: Path) -> None:
        write_report(tmp_path, "2026-08-05")

        with pytest.raises(ValueError, match="No published report"):
            discover_reports(tmp_path, dates=[date(2026, 8, 1)])

    def test_selects_missing_overlays_up_to_the_limit(self, tmp_path: Path) -> None:
        reports = tmp_path / "reports"
        for day in (3, 4, 5):
            write_report(reports, f"2026-08-0{day}")

        jobs = resolve_jobs(discover_reports(reports), tmp_path / "zh", limit=2)

        assert [job.target.date_text for job in jobs] == ["2026-08-05", "2026-08-04"]
        assert {job.reason for job in jobs} == {"missing overlay"}

    def test_current_overlay_is_skipped_and_stale_overlay_is_queued(self, tmp_path: Path) -> None:
        reports = tmp_path / "reports"
        translations = tmp_path / "zh"
        artifacts = tmp_path / "artifacts"
        report_path = write_report(reports, "2026-08-05")
        job = resolve_jobs([target_for(report_path)], translations)[0]

        def write_candidate(*_args: object, **_kwargs: object) -> MagicMock:
            (artifacts / "2026-08-05" / "translation.md").write_text(
                padded(TRANSLATED_BODY), encoding="utf-8"
            )
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("idea_pipeline.translator.zh.subprocess.run", side_effect=write_candidate):
            assert translate_job(job, artifacts_dir=artifacts).status == "published"

        assert resolve_jobs([target_for(report_path)], translations) == []

        report_path.write_text(SOURCE_REPORT + "\nNew evidence line.\n", encoding="utf-8")
        stale = resolve_jobs([target_for(report_path)], translations)

        assert [job.reason for job in stale] == ["source report changed since the overlay was written"]


class TestPromptAndCommand:
    def test_builds_noninteractive_copilot_command(self) -> None:
        command = build_copilot_command("prompt", model="gpt-5.4", effort="xhigh")

        assert command[:7] == [
            "copilot",
            "-p",
            "prompt",
            "--model",
            "gpt-5.4",
            "--effort",
            "xhigh",
        ]
        assert "--deny-tool=shell" in command
        assert "--autopilot" in command
        assert "--allow-all-urls" not in command

    def test_command_defaults_match_module_defaults(self) -> None:
        command = build_copilot_command("prompt")

        assert command[command.index("--model") + 1] == DEFAULT_MODEL
        assert command[command.index("--effort") + 1] == DEFAULT_EFFORT

    def test_prompt_states_the_structural_contract(self, tmp_path: Path) -> None:
        report_path = write_report(tmp_path / "reports", "2026-08-05")
        target = target_for(report_path)
        prepared = prepare_translation(target, tmp_path / "artifacts")
        job = TranslationJob(
            target=target,
            translation_path=tmp_path / "zh" / "2026-08-05.md",
            reason="missing overlay",
        )

        prompt = build_prompt(job, prepared)

        assert "3 headings in the same order" in prompt
        assert "#1: 3x2" in prompt
        assert "2 distinct link targets" in prompt
        assert TARGET_LANGUAGE in prompt
        assert "Do not run git commands." in prompt


class TestTranslationBoundary:
    def test_prepare_writes_sandbox_without_calling_copilot(self, tmp_path: Path) -> None:
        report_path = write_report(tmp_path / "reports", "2026-08-05")
        job = TranslationJob(
            target=target_for(report_path),
            translation_path=tmp_path / "zh" / "2026-08-05.md",
            reason="missing overlay",
        )

        with patch("idea_pipeline.translator.zh.subprocess.run") as mock_run:
            result = translate_job(
                job, artifacts_dir=tmp_path / "artifacts", prepare_only=True
            )

        mock_run.assert_not_called()
        assert result.status == "prepared"
        sandbox = tmp_path / "artifacts" / "2026-08-05"
        assert json.loads((sandbox / "structure.json").read_text())["tables"] == [
            {"columns": 3, "rows": 2}
        ]
        assert (sandbox / "instructions.md").is_file()
        assert (sandbox / "prompt.txt").is_file()
        assert not job.translation_path.exists()

    def test_publishes_normalized_overlay_with_source_digest(self, tmp_path: Path) -> None:
        report_path = write_report(tmp_path / "reports", "2026-08-05")
        target = target_for(report_path)
        job = TranslationJob(
            target=target,
            translation_path=tmp_path / "zh" / "2026-08-05.md",
            reason="missing overlay",
        )
        prepared = prepare_translation(target, tmp_path / "artifacts")

        def write_candidate(*_args: object, **_kwargs: object) -> MagicMock:
            prepared.candidate_path.write_text(
                padded(TRANSLATED_BODY.replace("参见", "参见 ").replace("50 美元", "50美元")),
                encoding="utf-8",
            )
            return MagicMock(returncode=0, stdout="done", stderr="")

        with patch("idea_pipeline.translator.zh.subprocess.run", side_effect=write_candidate):
            result = translate_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "published"
        published = job.translation_path.read_text(encoding="utf-8")
        front_matter, body = strip_front_matter(published)
        assert front_matter["lang"] == TARGET_LANGUAGE
        assert front_matter["source"] == "2026-08-05.md"
        assert len(front_matter["source_sha256"]) == 64
        assert body.lstrip().startswith("# Reddit 建造者情报报告")
        assert "50 美元" in body

    def test_invalid_candidate_is_not_published(self, tmp_path: Path) -> None:
        report_path = write_report(tmp_path / "reports", "2026-08-05")
        target = target_for(report_path)
        job = TranslationJob(
            target=target,
            translation_path=tmp_path / "zh" / "2026-08-05.md",
            reason="missing overlay",
        )
        prepared = prepare_translation(target, tmp_path / "artifacts")

        def write_candidate(*_args: object, **_kwargs: object) -> MagicMock:
            prepared.candidate_path.write_text(padded(SOURCE_REPORT), encoding="utf-8")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("idea_pipeline.translator.zh.subprocess.run", side_effect=write_candidate):
            result = translate_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "failed"
        assert not job.translation_path.exists()
        errors = json.loads(
            (tmp_path / "artifacts" / "2026-08-05" / "validation-errors.json").read_text()
        )["errors"]
        assert any("was not translated" in error for error in errors)

    def test_valid_candidate_survives_a_copilot_cli_crash(self, tmp_path: Path) -> None:
        report_path = write_report(tmp_path / "reports", "2026-08-05")
        target = target_for(report_path)
        job = TranslationJob(
            target=target,
            translation_path=tmp_path / "zh" / "2026-08-05.md",
            reason="missing overlay",
        )
        prepared = prepare_translation(target, tmp_path / "artifacts")

        def write_candidate(*_args: object, **_kwargs: object) -> MagicMock:
            prepared.candidate_path.write_text(padded(TRANSLATED_BODY), encoding="utf-8")
            return MagicMock(returncode=139, stdout="", stderr="Segmentation fault")

        with patch("idea_pipeline.translator.zh.subprocess.run", side_effect=write_candidate):
            result = translate_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "published"
        assert "1 warning(s)" in result.message

    def test_crash_without_a_candidate_fails_immediately(self, tmp_path: Path) -> None:
        report_path = write_report(tmp_path / "reports", "2026-08-05")
        job = TranslationJob(
            target=target_for(report_path),
            translation_path=tmp_path / "zh" / "2026-08-05.md",
            reason="missing overlay",
        )

        with patch("idea_pipeline.translator.zh.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=139, stdout="", stderr="Segmentation fault")
            result = translate_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "failed"
        assert result.message == "Copilot CLI exited with 139: Segmentation fault"

    def test_pipeline_credentials_never_reach_copilot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REDDIT_COOKIES", "must-not-reach-copilot")
        monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "copilot-token")
        report_path = write_report(tmp_path / "reports", "2026-08-05")
        job = TranslationJob(
            target=target_for(report_path),
            translation_path=tmp_path / "zh" / "2026-08-05.md",
            reason="missing overlay",
        )

        with patch("idea_pipeline.translator.zh.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="stopped")
            translate_job(job, artifacts_dir=tmp_path / "artifacts")

        environment = mock_run.call_args.kwargs["env"]
        assert "REDDIT_COOKIES" not in environment
        assert environment["COPILOT_GITHUB_TOKEN"] == "copilot-token"

    def test_missing_copilot_binary_is_reported(self, tmp_path: Path) -> None:
        report_path = write_report(tmp_path / "reports", "2026-08-05")
        job = TranslationJob(
            target=target_for(report_path),
            translation_path=tmp_path / "zh" / "2026-08-05.md",
            reason="missing overlay",
        )

        with patch(
            "idea_pipeline.translator.zh.subprocess.run", side_effect=FileNotFoundError
        ):
            result = translate_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "failed"
        assert "Copilot CLI not found" in result.message


class TestCommandLine:
    def test_parser_defaults(self) -> None:
        args = build_parser().parse_args([])

        assert args.dates is None
        assert args.limit == 5
        assert args.model == DEFAULT_MODEL
        assert args.effort == DEFAULT_EFFORT
        assert args.force is False

    def test_up_to_date_overlays_exit_successfully(self, tmp_path: Path) -> None:
        reports = tmp_path / "reports"
        write_report(reports, "2026-08-05")

        with patch("idea_pipeline.translator.zh.run_jobs") as mock_run_jobs:
            mock_run_jobs.return_value = []
            exit_code = main(
                [
                    "--reports-dir",
                    str(reports),
                    "--translations-dir",
                    str(tmp_path / "zh"),
                    "--prepare-only",
                    "--artifacts-dir",
                    str(tmp_path / "artifacts"),
                ]
            )

        assert exit_code == 0
        mock_run_jobs.assert_called_once()

    def test_missing_reports_directory_exits_with_two(self, tmp_path: Path) -> None:
        assert main(["--reports-dir", str(tmp_path / "absent")]) == 2


def test_translation_skill_is_available() -> None:
    from idea_pipeline.translator.zh import TRANSLATION_SKILL_PATH

    assert TRANSLATION_SKILL_PATH.is_file()


def test_module_entry_point_is_registered() -> None:
    pyproject = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()

    assert 'translate-zh = "idea_pipeline.translator.zh:main"' in pyproject


def test_subprocess_is_never_invoked_with_shell() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "idea_pipeline"
        / "translator"
        / "zh.py"
    ).read_text()

    assert "shell=True" not in source
    assert subprocess.run.__name__ == "run"
