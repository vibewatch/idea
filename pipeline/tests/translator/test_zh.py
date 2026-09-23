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
    TRANSLATION_QUALITY_VERSION,
    TranslationJob,
    TranslationTarget,
    build_copilot_command,
    build_parser,
    build_prompt,
    discover_reports,
    extract_structure,
    main,
    normalize_reddit_link_labels,
    normalize_required_headings,
    normalize_required_labels,
    normalize_structural_literals,
    normalize_translation,
    prepare_translation,
    protect_translation_source,
    protected_terms,
    resolve_jobs,
    restore_hedge_placeholders,
    strip_front_matter,
    translate_job,
    validate_translation,
)

SOURCE_REPORT = """# Reddit Builder Intelligence Report - 2026-08-05

## 1. Executive Brief

Open-source **OpenValve** reportedly shipped at least a $50 MRR tool. See [Open project](https://open-valve.com/).

## 2. Evidence Ledger

### OpenValve

**Primary link:** [Open project](https://open-valve.com/)

**Stage:** `Prototype`

**User or problem:** Operators need a faster review workflow.

**Build, test, or event:** OpenValve shipped an initial prototype.

**Evidence:** The founder reportedly reached at least $50 MRR.

**Visual proof:** [![A chart shows one active workflow](https://i.redd.it/example.png)](https://i.redd.it/example.png) The chart shows one active workflow.

**Limitation or next proof:** Retention is unknown.

**Reddit source:** [Original post](https://www.reddit.com/r/SaaS/comments/openvalve/openvalve/)

### PrintMap

**Primary link:** Not provided

**Stage:** `Launched`

**User or problem:** Designers need printable maps.

**Build, test, or event:** PrintMap launched a first version.

**Evidence:** Usage is not reported.

**Visual proof:** None.

**Limitation or next proof:** Payment and retention are unknown.

**Reddit source:** [Launch post](https://www.reddit.com/r/SaaS/comments/printmap/printmap/)

## 3. Customer Problems and Existing Workarounds

| Problem | Affected user | Workaround |
|---|---|---|
| Slow reviews | Operators | Manual checklist |
"""

TRANSLATED_BODY = """# Reddit 构建者情报报告 - 2026-08-05

## 1. 核心简报

据报道，开源项目 **OpenValve** 做出的工具至少达到 $50 MRR。参见 [Open project](https://open-valve.com/)。

## 2. 证据台账

### OpenValve

**主要链接：** [打开项目](https://open-valve.com/)

**阶段：** `原型`

**用户或问题：** 运维人员需要更快的审核流程。

**构建、测试或事件：** OpenValve 上线了首个原型。

**证据：** 据报道，创始人至少达到 $50 MRR。

**视觉证据：** [![图表显示一条活跃工作流](https://i.redd.it/example.png)](https://i.redd.it/example.png) 图表显示一条活跃工作流。

**局限或下一步证据：** 留存情况未知。

**Reddit 来源：** [Original post](https://www.reddit.com/r/SaaS/comments/openvalve/openvalve/)

### PrintMap

**主要链接：** 未提供

**阶段：** `已发布`

**用户或问题：** 设计师需要可打印地图。

**构建、测试或事件：** PrintMap 发布了首个版本。

**证据：** 使用情况未披露。

**视觉证据：** 无。

**局限或下一步证据：** 付费和留存情况未知。

**Reddit 来源：** [Launch post](https://www.reddit.com/r/SaaS/comments/printmap/printmap/)

## 3. 用户痛点与现有变通做法

| 问题 | 受影响用户 | 变通做法 |
|---|---|---|
| 审核缓慢 | 运维人员 | 手工清单 |
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
            (2, "1. Executive Brief"),
            (2, "2. Evidence Ledger"),
            (3, "OpenValve"),
            (3, "PrintMap"),
            (2, "3. Customer Problems and Existing Workarounds"),
        )
        assert structure.tables == ((3, 1),)
        assert structure.urls == (
            "https://open-valve.com/",
            "https://i.redd.it/example.png",
            "https://www.reddit.com/r/SaaS/comments/openvalve/openvalve/",
            "https://www.reddit.com/r/SaaS/comments/printmap/printmap/",
        )
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
        assert "$50 MRR" in protected_terms("Revenue reached $50 MRR.")["metrics"]
        assert "AADSTS5000224" in protected_terms("Error AADSTS5000224.")["identifiers"]
        assert protected_terms("Stages: `Usage`, `Revenue`; visual: `None`.")[
            "inline_code"
        ] == []
        assert protected_terms("$2,250 best week; $290.25 balance.")["metrics"] == [
            "$2,250",
            "$290.25",
        ]
        assert protected_terms("$10.62, then £5,913.60.")["metrics"] == [
            "$10.62",
            "£5,913.60",
        ]
        ledger = """## 2. Evidence Ledger

### OpenValve

**Primary link:** [Open project](https://open-valve.com/)
"""
        assert protected_terms(ledger)["project_names"] == ["OpenValve"]
        descriptive = """## 2. Evidence Ledger

### DistribBuddy — internal-tool validation

### ClipKaboom

### Workflow experiment

### SaaS revenue milestone

### Signl - WiFi Analyzer & mapper

### iRead - read aloud EPUB reader

### First 3 Paying Customers

### Free Won Over Premium

### Security Scanning SaaS

### Open Source Software Directory

**Primary link:** [Open project](https://opensourcesoftware.io)
"""
        assert protected_terms(descriptive)["project_names"] == [
            "DistribBuddy",
            "ClipKaboom",
            "Signl",
            "iRead",
            "Open Source Software Directory",
        ]


class TestNormalization:
    def test_round_trips_immutable_hedge_placeholders(self) -> None:
        source = (
            "The founder reportedly claims at least $50 MRR. "
            "[Claims in a title](https://example.com/) stay unchanged."
        )

        protected_source, placeholders = protect_translation_source(source)

        assert "reportedly claims at least" not in protected_source
        assert len(placeholders) == 3
        assert "[Claims in a title](https://example.com/)" in protected_source
        restored, messages = restore_hedge_placeholders(protected_source, placeholders)
        assert "据报道 声称 至少 $50 MRR" in restored
        assert messages == ["restored 3 immutable uncertainty qualifier token(s)"]

    def test_preserves_and_repairs_markdown_image_markers(self) -> None:
        normalized, messages = normalize_translation(
            "图一和！[图二](https://i.redd.it/example.png)，都需要保留。"
        )

        assert "和![图二](https://i.redd.it/example.png)" in normalized
        assert messages == ["restored 1 Markdown image marker(s)"]

    def test_standardizes_label_spacing_link_spacing_and_reddit_counts(self) -> None:
        normalized, messages = normalize_translation(
            "**解读：** 分析：产品已上线。\n"
            "**证据：**作者见 [帖子](https://example.com/)后行动"
            "（219 points，100 comments）。"
        )

        assert normalized == (
            "**解读：** 产品已上线。\n"
            "**证据：** 作者见 [帖子](https://example.com/) 后行动"
            "（219 分，100 条评论）。"
        )
        assert any("report spacing or Reddit count" in message for message in messages)

        preserved, _messages = normalize_translation(
            "图一和 ![图二](https://i.redd.it/example.png)，都需要保留。"
        )

        assert "和 ![图二](https://i.redd.it/example.png)" in preserved

    def test_restores_reddit_title_labels_in_occurrence_order(self) -> None:
        source = (
            "[First title…](https://www.reddit.com/r/SaaS/comments/abc/post/) "
            "[Short label](https://www.reddit.com/r/SaaS/comments/abc/post/)"
        )
        candidate = (
            "[Expanded first title](https://www.reddit.com/r/SaaS/comments/abc/post/) "
            "[Translated label](https://www.reddit.com/r/SaaS/comments/abc/post/)"
        )

        normalized, restored = normalize_reddit_link_labels(source, candidate)

        assert normalized == source
        assert restored == 2

    def test_restores_exact_reddit_target_and_standard_heading(self) -> None:
        source = (
            "# Reddit Builder Intelligence Report - 2026-08-05\n\n"
            "[CISO title](https://www.reddit.com/r/sysadmin/comments/abc/ciso_title/)\n"
        )
        candidate = (
            "# Reddit Builder Intelligence Report - 2026-08-05\n\n"
            "[Translated](https://www.reddit.com/r/sysadmin/comments/abc/cISO_title/)\n"
        )

        normalized, heading_count = normalize_required_headings(source, candidate)
        normalized, link_count = normalize_reddit_link_labels(source, normalized)

        assert normalized == (
            "# Reddit 构建者情报报告 - 2026-08-05\n\n"
            "[CISO title](https://www.reddit.com/r/sysadmin/comments/abc/ciso_title/)\n"
        )
        assert heading_count == 1
        assert link_count == 1

    def test_restores_standard_case_labels(self) -> None:
        source = (
            "## 2. Evidence Ledger\n\n"
            "### OpenValve\n\n"
            "**Primary link:** [Open](https://open-valve.com/)\n\n"
            "**Visual proof:** None.\n"
        )
        candidate = (
            "## 2. 证据台账\n\n"
            "### OpenValve\n\n"
            "**Primary link:** [打开](https://open-valve.com/)\n\n"
            "**Visual proof:** 无。\n"
        )

        normalized, restored = normalize_required_labels(source, candidate)

        assert "**主要链接：**" in normalized
        assert "**视觉证据：**" in normalized
        assert restored == 2

    def test_converts_ascii_punctuation_after_chinese_text(self) -> None:
        normalized, messages = normalize_translation("作者自述, 增长停滞; 原因不明.")

        assert normalized == "作者自述，增长停滞；原因不明。"
        assert any("full width" in message for message in messages)

    def test_inserts_spacing_between_chinese_and_latin(self) -> None:
        normalized, _messages = normalize_translation("月收入50美元来自Bing流量")

        assert normalized == "月收入 50 美元来自 Bing 流量"

    def test_removes_spaces_between_chinese_words(self) -> None:
        normalized, messages = normalize_translation("作者自述 日活增长，功能 声称仍需验证。")

        assert normalized == "作者自述日活增长，功能声称仍需验证。"
        assert any("tightened" in message for message in messages)

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
            padded(
                TRANSLATED_BODY.replace(
                    "[Open project](https://open-valve.com/)",
                    "开源项目",
                ).replace(
                    "[打开项目](https://open-valve.com/)",
                    "开源项目",
                )
            ),
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
            padded(TRANSLATED_BODY.replace("| 审核缓慢 | 运维人员 | 手工清单 |\n", "")),
            encoding="utf-8",
        )

        errors = validate_translation(candidate, structure=extract_structure(SOURCE_REPORT))

        assert any("row count differs" in error for error in errors)

    def test_rejects_missing_standard_case_label(self, tmp_path: Path) -> None:
        candidate = tmp_path / "translation.md"
        candidate.write_text(
            padded(TRANSLATED_BODY.replace("**视觉证据：**", "**截图：**", 1)),
            encoding="utf-8",
        )

        errors = validate_translation(
            candidate,
            structure=extract_structure(SOURCE_REPORT),
            source_text=SOURCE_REPORT,
        )

        assert any("standard case label" in error for error in errors)

    def test_rejects_untranslated_headings(self, tmp_path: Path) -> None:
        candidate = tmp_path / "translation.md"
        candidate.write_text(padded(SOURCE_REPORT), encoding="utf-8")

        errors = validate_translation(candidate, structure=extract_structure(SOURCE_REPORT))

        assert any("standard translation" in error for error in errors)

    def test_requires_standard_report_headings_and_highlight_labels(self, tmp_path: Path) -> None:
        source = SOURCE_REPORT.replace(
            "## 2. Evidence Ledger",
            """### Key Highlights

- **Best new artifacts:** OpenValve.
- **Strongest traction:** $50 MRR.
- **Sharpest user pain:** Manual work.
- **Most useful visual:** A chart.
- **Biggest evidence gap:** Retention.

### Coverage and Caveats

One source is represented.

## 2. Evidence Ledger""",
        )
        translated = TRANSLATED_BODY.replace(
            "## 2. 证据台账",
            """### 重点信号

- **重点新项目：** OpenValve。
- **最强增长信号：** $50 MRR。
- **最明确的用户痛点：** 手工流程。
- **最有价值的视觉证据：** 图表。
- **最大证据缺口：** 留存。

### 覆盖范围与局限

目前只有一个来源。

## 2. 证据台账""",
        )
        candidate = tmp_path / "translation.md"
        candidate.write_text(padded(translated), encoding="utf-8")

        assert (
            validate_translation(
                candidate,
                structure=extract_structure(source),
                source_text=source,
                protected=protected_terms(source),
            )
            == []
        )

        candidate.write_text(
            padded(translated.replace("### 重点信号", "### 主要亮点")),
            encoding="utf-8",
        )
        errors = validate_translation(
            candidate,
            structure=extract_structure(source),
            source_text=source,
            protected=protected_terms(source),
        )

        assert any("standard translation" in error for error in errors)

    def test_requires_standard_synthesis_labels(self, tmp_path: Path) -> None:
        source = (
            SOURCE_REPORT
            + """
## 4. Patterns, Contradictions, and Gaps

### Narrow pattern

**Evidence:** One case.

**Interpretation:** Partial.

**Missing proof:** Retention.
"""
        )
        translated = (
            TRANSLATED_BODY
            + """
## 4. 模式、矛盾与证据缺口

### 狭窄模式

**证据：** 一个案例。

**解读：** 部分匹配。

**缺失证据：** 留存。
"""
        )
        candidate = tmp_path / "translation.md"
        candidate.write_text(padded(translated), encoding="utf-8")

        assert (
            validate_translation(
                candidate,
                structure=extract_structure(source),
                source_text=source,
                protected=protected_terms(source),
            )
            == []
        )

        candidate.write_text(
            padded(translated.replace("**缺失证据：**", "**待验证：**")),
            encoding="utf-8",
        )
        errors = validate_translation(
            candidate,
            structure=extract_structure(source),
            source_text=source,
            protected=protected_terms(source),
        )

        assert any("standard synthesis label" in error for error in errors)

    def test_rejects_untranslated_table_header(self, tmp_path: Path) -> None:
        candidate = tmp_path / "translation.md"
        candidate.write_text(
            padded(
                TRANSLATED_BODY.replace(
                    "| 问题 | 受影响用户 | 变通做法 |",
                    "| Problem | Affected user | Workaround |",
                )
            ),
            encoding="utf-8",
        )

        errors = validate_translation(candidate, structure=extract_structure(SOURCE_REPORT))

        assert any("table header row(s) were not translated" in error for error in errors)

    def test_rejects_high_confidence_translationese(self, tmp_path: Path) -> None:
        candidate = tmp_path / "translation.md"
        candidate.write_text(
            padded(TRANSLATED_BODY + "\n这个工具被认为是开发者需要的方案。\n"),
            encoding="utf-8",
        )
        warnings: list[str] = []

        errors = validate_translation(
            candidate, structure=extract_structure(SOURCE_REPORT), warnings=warnings
        )

        assert any("high-confidence translationese" in error for error in errors)

    def test_rejects_changed_metric_and_project_name(self, tmp_path: Path) -> None:
        candidate = tmp_path / "translation.md"
        candidate.write_text(
            padded(
                TRANSLATED_BODY.replace("$50 MRR", "50 美元 MRR").replace("OpenValve", "开放阀")
            ),
            encoding="utf-8",
        )

        errors = validate_translation(
            candidate,
            structure=extract_structure(SOURCE_REPORT),
            source_text=SOURCE_REPORT,
            protected=protected_terms(SOURCE_REPORT),
        )

        assert any("protected metrics" in error for error in errors)
        assert any("protected project_names" in error for error in errors)

    def test_rejects_dropped_uncertainty_qualifier(self, tmp_path: Path) -> None:
        candidate = tmp_path / "translation.md"
        candidate.write_text(
            padded(TRANSLATED_BODY.replace("据报道，", "").replace("至少", "")),
            encoding="utf-8",
        )

        errors = validate_translation(
            candidate,
            structure=extract_structure(SOURCE_REPORT),
            source_text=SOURCE_REPORT,
            protected=protected_terms(SOURCE_REPORT),
        )

        assert any("'reportedly'" in error for error in errors)
        assert any("'at least'" in error for error in errors)

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

        assert [job.reason for job in stale] == [
            "source report changed since the overlay was written"
        ]

    def test_overlay_from_older_quality_contract_is_queued(self, tmp_path: Path) -> None:
        reports = tmp_path / "reports"
        translations = tmp_path / "zh"
        report_path = write_report(reports, "2026-08-05")
        prepared = prepare_translation(target_for(report_path), tmp_path / "artifacts")
        translations.mkdir()
        (translations / report_path.name).write_text(
            f'---\nsource_sha256: "{prepared.source_digest}"\n---\n\n{TRANSLATED_BODY}',
            encoding="utf-8",
        )

        jobs = resolve_jobs([target_for(report_path)], translations)

        assert [job.reason for job in jobs] == [
            f"overlay predates translation quality contract v{TRANSLATION_QUALITY_VERSION}"
        ]


class TestPromptAndCommand:
    def test_builds_noninteractive_copilot_command(self) -> None:
        command = build_copilot_command("prompt", model="gpt-5.4", effort="xhigh")

        assert command[:7] == [
            "copilot",
            "-p",
            "prompt",
            "--model",
            "gpt-5.4",
            "--reasoning-effort",
            "xhigh",
        ]
        assert "--deny-tool=shell" in command
        assert "--autopilot" in command
        assert "--allow-all-urls" not in command

    def test_command_defaults_match_module_defaults(self) -> None:
        command = build_copilot_command("prompt")

        assert command[command.index("--model") + 1] == DEFAULT_MODEL
        assert command[command.index("--reasoning-effort") + 1] == DEFAULT_EFFORT
        assert "--no-custom-instructions" in command

    def test_command_records_usage_when_requested(self, tmp_path: Path) -> None:
        usage_path = tmp_path / "usage.json"

        command = build_copilot_command("prompt", usage_output_file=usage_path)

        assert command[-2:] == ["--usage-output-file", str(usage_path)]

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

        assert "6 headings in the same order" in prompt
        assert "2 Section 2 case subsections" in prompt
        assert "#1: 3x1" in prompt
        assert "4 distinct link targets" in prompt
        assert TARGET_LANGUAGE in prompt
        assert "Translation input: translation-source.md" in prompt
        assert "preserve all 4 inline-code tokens byte-identically" in prompt
        assert "Occurrence-level uncertainty ledger" not in prompt
        assert "Immutable Markdown image targets:" in prompt
        assert "`https://i.redd.it/example.png`" in prompt
        assert "Blocked translationese patterns (zero occurrences allowed)" in prompt
        assert "`通过……来`" in prompt
        assert "Use translation-source.md as a fixed Markdown scaffold" in prompt
        assert "`Idea` -> `想法`" in prompt
        assert "`Usage` -> `已有实际使用`" in prompt
        assert "first identify the topic, action, result, and limitation" in prompt
        assert "Do not run git commands." in prompt

    def test_normalizes_structural_stage_and_empty_visual_values(self) -> None:
        source = """**Stage:** `Usage`

Move from `Prototype` to a stronger stage.

**Visual proof:** `None`
"""
        candidate = """**阶段：** `Usage`

从 `Prototype` 提升到更高阶段。

**视觉证据：** `None`
"""

        normalized, count = normalize_structural_literals(source, candidate)

        assert count == 3
        assert "**阶段：** `已有实际使用`" in normalized
        assert "从 `原型` 提升到更高阶段。" in normalized
        assert "**视觉证据：** 无" in normalized


class TestTranslationBoundary:
    def test_prepare_writes_sandbox_without_calling_copilot(self, tmp_path: Path) -> None:
        report_path = write_report(tmp_path / "reports", "2026-08-05")
        job = TranslationJob(
            target=target_for(report_path),
            translation_path=tmp_path / "zh" / "2026-08-05.md",
            reason="missing overlay",
        )
        sandbox = tmp_path / "artifacts" / "2026-08-05"
        sandbox.mkdir(parents=True)
        for name in ("repair-metadata.json", "repair.stdout.log", "repair.stderr.log"):
            (sandbox / name).write_text("stale", encoding="utf-8")

        with patch("idea_pipeline.translator.zh.subprocess.run") as mock_run:
            result = translate_job(job, artifacts_dir=tmp_path / "artifacts", prepare_only=True)

        mock_run.assert_not_called()
        assert result.status == "prepared"
        assert json.loads((sandbox / "structure.json").read_text())["tables"] == [
            {"columns": 3, "rows": 1}
        ]
        assert (sandbox / "instructions.md").is_file()
        assert (sandbox / "translation-source.md").is_file()
        placeholders = json.loads((sandbox / "hedge-placeholders.json").read_text())
        assert [item["replacement"] for item in placeholders] == [
            "据报道",
            "至少",
            "据报道",
            "至少",
        ]
        assert (sandbox / "prompt.txt").is_file()
        assert not (sandbox / "repair-metadata.json").exists()
        assert not (sandbox / "repair.stdout.log").exists()
        assert not (sandbox / "repair.stderr.log").exists()
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
        assert front_matter["quality_version"] == TRANSLATION_QUALITY_VERSION
        assert front_matter["editor_model"] == "gpt-6-luna"
        assert body.lstrip().startswith("# Reddit 构建者情报报告")
        assert "$50 MRR" in body
        metadata = json.loads(
            (tmp_path / "artifacts" / "2026-08-05" / "generation-metadata.json").read_text(
                encoding="utf-8"
            )
        )
        assert metadata["model"] == DEFAULT_MODEL
        assert metadata["effort"] == DEFAULT_EFFORT
        assert metadata["returncode"] == 0
        assert metadata["duration_seconds"] >= 0

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
        assert any(
            "still largely English" in error or "table header" in error for error in errors
        )

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
        assert "2 warning(s)" in result.message

    def test_invalid_editor_output_restores_validated_draft(self, tmp_path: Path) -> None:
        report_path = write_report(tmp_path / "reports", "2026-08-05")
        target = target_for(report_path)
        job = TranslationJob(
            target=target,
            translation_path=tmp_path / "zh" / "2026-08-05.md",
            reason="missing overlay",
        )
        prepared = prepare_translation(target, tmp_path / "artifacts")
        calls = 0

        def write_candidates(*_args: object, **_kwargs: object) -> MagicMock:
            nonlocal calls
            calls += 1
            if calls == 1:
                prepared.candidate_path.write_text(
                    padded(TRANSLATED_BODY),
                    encoding="utf-8",
                )
            else:
                prepared.candidate_path.write_text(
                    padded(SOURCE_REPORT),
                    encoding="utf-8",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("idea_pipeline.translator.zh.subprocess.run", side_effect=write_candidates):
            result = translate_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "published"
        front_matter, body = strip_front_matter(job.translation_path.read_text(encoding="utf-8"))
        assert "editor_model" not in front_matter
        assert body.lstrip().startswith("# Reddit 构建者情报报告")
        assert "restored the validated draft" in (
            tmp_path / "artifacts" / "2026-08-05" / "validation-warnings.json"
        ).read_text(encoding="utf-8")

    def test_editor_timeout_restores_validated_draft(self, tmp_path: Path) -> None:
        report_path = write_report(tmp_path / "reports", "2026-08-05")
        target = target_for(report_path)
        job = TranslationJob(
            target=target,
            translation_path=tmp_path / "zh" / "2026-08-05.md",
            reason="missing overlay",
        )
        prepared = prepare_translation(target, tmp_path / "artifacts")
        calls = 0

        def generate_then_timeout(*_args: object, **_kwargs: object) -> MagicMock:
            nonlocal calls
            calls += 1
            if calls == 1:
                prepared.candidate_path.write_text(
                    padded(TRANSLATED_BODY),
                    encoding="utf-8",
                )
                return MagicMock(returncode=0, stdout="", stderr="")
            raise subprocess.TimeoutExpired(["copilot"], 1200)

        with patch(
            "idea_pipeline.translator.zh.subprocess.run",
            side_effect=generate_then_timeout,
        ):
            result = translate_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "published"
        front_matter, body = strip_front_matter(job.translation_path.read_text(encoding="utf-8"))
        assert "editor_model" not in front_matter
        assert body.lstrip().startswith("# Reddit 构建者情报报告")
        warnings = (
            tmp_path / "artifacts" / "2026-08-05" / "validation-warnings.json"
        ).read_text(encoding="utf-8")
        assert "translation editor exceeded 1200 seconds" in warnings

    def test_crash_without_a_candidate_fails_immediately(self, tmp_path: Path) -> None:
        report_path = write_report(tmp_path / "reports", "2026-08-05")
        job = TranslationJob(
            target=target_for(report_path),
            translation_path=tmp_path / "zh" / "2026-08-05.md",
            reason="missing overlay",
        )

        with patch("idea_pipeline.translator.zh.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=139, stdout="", stderr="Segmentation fault"
            )
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

        with patch("idea_pipeline.translator.zh.subprocess.run", side_effect=FileNotFoundError):
            result = translate_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "failed"
        assert "Copilot CLI not found" in result.message

    def test_translation_timeout_is_reported(self, tmp_path: Path) -> None:
        report_path = write_report(tmp_path / "reports", "2026-08-05")
        job = TranslationJob(
            target=target_for(report_path),
            translation_path=tmp_path / "zh" / "2026-08-05.md",
            reason="missing overlay",
        )

        with patch(
            "idea_pipeline.translator.zh.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                ["copilot"],
                1200,
                output=b"partial output",
            ),
        ):
            result = translate_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "failed"
        assert "translation generation exceeded 1200 seconds" in result.message


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
        Path(__file__).resolve().parents[2] / "src" / "idea_pipeline" / "translator" / "zh.py"
    ).read_text()

    assert "shell=True" not in source
    assert subprocess.run.__name__ == "run"
