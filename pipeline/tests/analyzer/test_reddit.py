"""Tests for deterministic Reddit intelligence preparation and publication."""

from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from idea_pipeline import PROJECT_ROOT, REPOSITORY_ROOT
from idea_pipeline.analyzer.reddit import (
    DEFAULT_ARTIFACTS_DIR,
    DEFAULT_DATA_DIR,
    DEFAULT_ENV_FILE,
    DEFAULT_REPORTS_DIR,
    REPORT_ARTIFACT_NAME,
    REPORT_TOPICS,
    AnalysisJob,
    ReportTarget,
    SnapshotTarget,
    analyze_job,
    build_copilot_command,
    build_parser,
    build_prompt,
    discover_reports,
    discover_snapshots,
    group_snapshots,
    humanize_topic,
    main,
    materialize_media_assets,
    prepare_report,
    prepare_snapshot,
    rank_score,
    resolve_jobs,
    validate_media_review,
    validate_report,
)


def write_snapshot(path: Path, posts: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"last_fetched": path.stem, "posts": posts}, indent=2) + "\n",
        encoding="utf-8",
    )


def post(
    post_id: str,
    *,
    score: int = 1,
    comments: int = 0,
    title: str | None = None,
    media_url: str | None = None,
    selftext: str | None = None,
    is_video: bool = False,
) -> dict[str, object]:
    permalink = f"/r/SaaS/comments/{post_id}/{post_id}_title/"
    value: dict[str, object] = {
        "id": post_id,
        "title": title or f"Post {post_id}",
        "subreddit": "SaaS",
        "author": f"author_{post_id}",
        "score": score,
        "num_comments": comments,
        "permalink": permalink,
        "url": f"https://www.reddit.com{permalink}",
        "selftext": selftext
        or "A concrete workflow, founder assumption, or measured builder outcome.",
        "is_self": True,
        "is_video": is_video,
    }
    if media_url:
        value["url"] = media_url
        value["is_self"] = False
    return value


def make_report_target(
    root: Path,
    snapshot_date: date = date(2026, 8, 2),
    *,
    post_ids: tuple[str, str, str] = ("pain1", "idea1", "build1"),
) -> ReportTarget:
    snapshots: list[SnapshotTarget] = []
    for topic, post_id in zip(REPORT_TOPICS, post_ids):
        path = root / "data" / topic / f"{snapshot_date.isoformat()}.json"
        selftext = None
        if topic == "saas-build":
            selftext = "A launched workflow tool at https://example.com/product with one user."
        write_snapshot(path, [post(post_id, selftext=selftext)])
        snapshots.append(SnapshotTarget(topic, snapshot_date, path))
    return ReportTarget(snapshot_date, tuple(snapshots))


def valid_report(
    report_date: str = "2026-08-02",
    *,
    post_ids: tuple[str, str, str] = ("pain1", "idea1", "build1"),
    image_url: str | None = None,
    video_url: str | None = None,
) -> str:
    pain_id, idea_id, build_id = post_ids
    pain = f"https://www.reddit.com/r/SaaS/comments/{pain_id}/{pain_id}_title/"
    idea = f"https://www.reddit.com/r/SaaS/comments/{idea_id}/{idea_id}_title/"
    build = f"https://www.reddit.com/r/SaaS/comments/{build_id}/{build_id}_title/"
    project = "https://example.com/product"
    image = image_url or build
    video = video_url or build
    sections = [
        (
            "## 1. Executive Value Summary",
            f"A linked project, a concrete pain, and a visual demo are available ([pain]({pain}), [idea]({idea}), [build]({build})).",
        ),
        (
            "## 2. New Projects and Direct Links",
            f"""| Project or artifact | Type | What it does | Intended user or problem | Stage | Concrete evidence or why it is notable | Direct link | Reddit source |
|---|---|---|---|---|---|---|---|
| Review tool | SaaS | Routes review work | Operators | Launched | One user | [Open project]({project}) | [build]({build}) |""",
        ),
        (
            "## 3. Customer Problems and Existing Workarounds",
            f"""| Problem | Affected user and context | Trigger or workflow | Observed consequence | Existing tool, service, or workaround | Evidence breadth | Sources |
|---|---|---|---|---|---|---|
| Manual handoff | Operator | Every review | Delay | Checklist | One account | [pain]({pain}) |""",
        ),
        (
            "## 4. Founder Ideas and Validation Signals",
            f"""| Idea or validation case | Intended user and outcome | What was tested | Strongest validation signal | Disconfirming evidence or gap | Status | Sources |
|---|---|---|---|---|---|---|
| Review helper | Operator saves time | Prototype | One interview | No usage evidence | Prototype | [idea]({idea}) |""",
        ),
        (
            "## 5. Launches, Traction, and Distribution Results",
            f"""| Project or experiment | Direct link | Stage | Channel or implementation | Measured result | What the result supports | What it does not prove | Source |
|---|---|---|---|---|---|---|---|
| Review tool | [Open]({project}) | Launched | Direct outreach | One signup | Initial acquisition | Retention | [build]({build}) |""",
        ),
        (
            "## 6. Visual and Demo Evidence",
            f"""| Project or post | Media type | What was visibly demonstrated | Value beyond the text claim | Limitation | Media | Reddit source |
|---|---|---|---|---|---|---|
| Review tool | Image | Queue with one item | Confirms an interface exists | Static frame | [View image]({image}) | [build]({build}) |
| Review tool demo | Video contact sheet | Review flow across sampled frames | Confirms interaction sequence | No audio | [Watch video]({video}) | [build]({build}) |""",
        ),
        (
            "## 7. Cross-Stream Matches and Gaps",
            f"""| Theme or concrete artifact | Customer-pain evidence | Founder-idea evidence | Build/outcome evidence | Relationship | Missing link |
|---|---|---|---|---|---|
| Review delay | [pain]({pain}) | [idea]({idea}) | [build]({build}) | Partial | Retention evidence |""",
        ),
        (
            "## 8. Practical Takeaways and Watchlist",
            f"""### Reusable lessons

| Lesson | Concrete evidence | Scope or contradiction | Practical use |
|---|---|---|---|
| Shipping does not establish retention | [build]({build}) | One case | Measure repeat use |

### Watchlist

| Priority | Project, problem, or signal to monitor | Current evidence | What remains unknown | Evidence that would change the reading |
|---:|---|---|---|---|
| 1 | Review delay | [pain]({pain}) | Frequency | Independent accounts |""",
        ),
    ]
    chunks = [f"# Reddit Builder Intelligence Report - {report_date}"]
    for heading, body in sections:
        chunks.extend(["", heading, "", body, "", "---"])
    return "\n".join(chunks).rstrip("-\n") + "\n"


def required_section_ids() -> dict[str, set[str]]:
    return {
        "## 3. Customer Problems and Existing Workarounds": {"pain1"},
        "## 4. Founder Ideas and Validation Signals": {"idea1"},
        "## 5. Launches, Traction, and Distribution Results": {"build1"},
    }


class TestPathsAndHelpers:
    def test_default_paths_follow_repository_layout(self) -> None:
        assert DEFAULT_DATA_DIR == REPOSITORY_ROOT / "data" / "reddit"
        assert DEFAULT_REPORTS_DIR == REPOSITORY_ROOT / "reports" / "reddit"
        assert DEFAULT_ARTIFACTS_DIR == PROJECT_ROOT / "artifacts" / "reddit"
        assert DEFAULT_ENV_FILE == PROJECT_ROOT / ".env"
        assert REPORT_ARTIFACT_NAME == "builder-intelligence"

    @pytest.mark.parametrize(
        ("topic", "expected"),
        [
            ("saas-build", "SaaS Build"),
            ("startup-ideas", "Startup Ideas"),
            ("ai_api", "AI API"),
        ],
    )
    def test_humanizes_topic(self, topic: str, expected: str) -> None:
        assert humanize_topic(topic) == expected

    def test_rank_score_rewards_discussion_and_evidence(self) -> None:
        plain = post("plain", score=10, comments=0)
        rich = post("rich", score=10, comments=8)
        rich["comments_data"] = [
            {"id": "c1", "author": "user", "body": "detail", "score": 2}
        ]

        assert rank_score(rich) > rank_score(plain)

    def test_rank_score_prefers_detailed_evidence_over_thin_virality(self) -> None:
        viral = post("viral", score=500, comments=100)
        viral["selftext"] = ""
        detailed = post("detailed", score=20, comments=10)
        detailed["selftext"] = (
            "This manual workflow costs 8 hours every week. We launched a prototype, "
            "migrated 12 customers, and earned $500 in revenue. " * 8
        )
        detailed["comments_data"] = [
            {
                "id": "c1",
                "author": "operator",
                "body": "We have the same recurring problem and solve it with a spreadsheet.",
                "score": 3,
            }
        ]

        assert rank_score(detailed) > rank_score(viral)


class TestDiscovery:
    def test_automatic_discovery_excludes_today_and_future(self, tmp_path: Path) -> None:
        topic_dir = tmp_path / "saas-build"
        write_snapshot(topic_dir / "2026-08-01.json", [post("old")])
        write_snapshot(topic_dir / "2026-08-02.json", [post("today")])
        write_snapshot(topic_dir / "2026-08-03.json", [post("future")])

        targets = discover_snapshots(tmp_path, today=date(2026, 8, 2))

        assert [target.date_text for target in targets] == ["2026-08-01"]

    def test_include_today_only_changes_automatic_discovery(self, tmp_path: Path) -> None:
        topic_dir = tmp_path / "saas-build"
        write_snapshot(topic_dir / "2026-08-02.json", [post("today")])
        write_snapshot(topic_dir / "2026-08-03.json", [post("future")])

        automatic = discover_snapshots(tmp_path, include_today=True, today=date(2026, 8, 2))
        explicit = discover_snapshots(
            tmp_path,
            dates=[date(2026, 8, 3)],
            today=date(2026, 8, 2),
        )

        assert [target.date_text for target in automatic] == ["2026-08-02"]
        assert [target.date_text for target in explicit] == ["2026-08-03"]

    def test_repeatable_filters_select_only_existing_pairs(self, tmp_path: Path) -> None:
        write_snapshot(tmp_path / "a" / "2026-08-01.json", [post("a1")])
        write_snapshot(tmp_path / "b" / "2026-08-02.json", [post("b2")])

        targets = discover_snapshots(
            tmp_path,
            topics=["a", "b"],
            dates=[date(2026, 8, 1), date(2026, 8, 2)],
        )

        assert [(target.topic, target.date_text) for target in targets] == [
            ("a", "2026-08-01"),
            ("b", "2026-08-02"),
        ]

    def test_history_is_limited_to_seven_prior_snapshots(self, tmp_path: Path) -> None:
        start = date(2026, 7, 20)
        for offset in range(10):
            snapshot_date = start + timedelta(days=offset)
            write_snapshot(
                tmp_path / "ideas" / f"{snapshot_date.isoformat()}.json",
                [post(f"p{offset}")],
            )

        target = discover_snapshots(
            tmp_path,
            topics=["ideas"],
            dates=[start + timedelta(days=9)],
        )[0]

        assert len(target.history) == 7
        assert target.history[0].stem == (start + timedelta(days=2)).isoformat()

    def test_rejects_unknown_or_unsafe_topics(self, tmp_path: Path) -> None:
        (tmp_path / "known").mkdir()

        with pytest.raises(ValueError, match="Unknown Reddit topic"):
            discover_snapshots(tmp_path, topics=["missing"])
        with pytest.raises(ValueError, match="Invalid Reddit topic"):
            discover_snapshots(tmp_path, topics=["../known"])

    def test_groups_only_complete_dates_in_fixed_stream_order(self, tmp_path: Path) -> None:
        complete = date(2026, 8, 1)
        incomplete = date(2026, 8, 2)
        targets = list(make_report_target(tmp_path, complete).snapshots)
        path = tmp_path / "data" / "customer-pain" / f"{incomplete.isoformat()}.json"
        write_snapshot(path, [post("later")])
        targets.append(SnapshotTarget("customer-pain", incomplete, path))

        reports = group_snapshots(reversed(targets))

        assert [report.date_text for report in reports] == ["2026-08-01"]
        assert [item.topic for item in reports[0].snapshots] == list(REPORT_TOPICS)

    def test_discover_reports_requires_every_stream_for_explicit_date(
        self, tmp_path: Path
    ) -> None:
        for topic in REPORT_TOPICS[:2]:
            write_snapshot(tmp_path / topic / "2026-08-02.json", [post(f"{topic}1")])
        (tmp_path / REPORT_TOPICS[2]).mkdir(parents=True)

        with pytest.raises(FileNotFoundError, match="missing: saas-build"):
            discover_reports(tmp_path, dates=[date(2026, 8, 2)])

    def test_discover_reports_returns_one_target_for_three_files(self, tmp_path: Path) -> None:
        expected = make_report_target(tmp_path).snapshots

        reports = discover_reports(
            tmp_path / "data",
            dates=[date(2026, 8, 2)],
        )

        assert len(reports) == 1
        assert [item.path for item in reports[0].snapshots] == [item.path for item in expected]


class TestPreparation:
    def test_writes_ranked_artifacts_without_mutating_source(self, tmp_path: Path) -> None:
        source = tmp_path / "data" / "saas-build" / "2026-08-01.json"
        posts = [
            post("low", score=1),
            post("top", score=20, comments=10, media_url="https://i.redd.it/chart.png"),
            post("middle", score=10),
            post("other", score=2),
        ]
        write_snapshot(source, posts)
        original = source.read_bytes()
        target = SnapshotTarget("saas-build", date(2026, 8, 1), source)

        prepared = prepare_snapshot(target, tmp_path / "artifacts")

        assert source.read_bytes() == original
        assert prepared.total_posts == 4
        assert prepared.review_size == 2
        assert prepared.analysis_size == 1
        assert "id=top" in prepared.review_path.read_text(encoding="utf-8").splitlines()[-2]
        assert "id=top" in prepared.analysis_path.read_text(encoding="utf-8")
        assert prepared.source_path.read_bytes() == original
        manifest = json.loads(prepared.manifest_path.read_text(encoding="utf-8"))
        assert manifest[0]["url"] == "https://i.redd.it/chart.png"
        assert prepared.links_path.exists()

    def test_manifests_cover_media_and_links_outside_ranked_review_set(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "data" / "saas-build" / "2026-08-01.json"
        posts = [post(f"top{index}", score=100 - index) for index in range(4)]
        posts.extend(
            [
                post(
                    "visual",
                    score=0,
                    media_url="https://v.redd.it/demo123",
                    is_video=True,
                ),
                post(
                    "linked",
                    score=0,
                    selftext="The live project is https://example.com/new-project.",
                ),
            ]
        )
        write_snapshot(source, posts)

        prepared = prepare_snapshot(
            SnapshotTarget("saas-build", date(2026, 8, 1), source),
            tmp_path / "artifacts",
        )

        assert prepared.review_size == 3
        assert "id=visual" not in prepared.review_path.read_text()
        media = json.loads(prepared.manifest_path.read_text())
        links = json.loads(prepared.links_path.read_text())
        assert any(item["post_id"] == "visual" and item["media_type"] == "video" for item in media)
        assert any(
            item["post_id"] == "linked"
            and item["canonical_url"] == "https://example.com/new-project"
            for item in links
        )

    def test_prepares_one_sandbox_with_all_three_sources(self, tmp_path: Path) -> None:
        target = make_report_target(tmp_path)
        originals = {item.topic: item.path.read_bytes() for item in target.snapshots}

        prepared = prepare_report(target, tmp_path / "artifacts")

        assert prepared.directory == (
            tmp_path / "artifacts" / REPORT_ARTIFACT_NAME / "2026-08-02"
        )
        assert prepared.total_posts == 3
        assert len(prepared.topic_artifacts) == 3
        assert "Reddit Value and Builder Intelligence Extraction" in (
            prepared.instructions_path.read_text()
        )
        metadata = json.loads(prepared.metadata_path.read_text())
        assert metadata["report_type"] == "builder-intelligence-v2"
        assert metadata["total_posts"] == 3
        assert metadata["external_link_count"] == 1
        assert [source["topic"] for source in metadata["sources"]] == list(REPORT_TOPICS)
        assert prepared.link_manifest_path.exists()
        assert prepared.media_manifest_path.exists()
        for snapshot, topic_prepared in zip(target.snapshots, prepared.topic_artifacts):
            assert snapshot.path.read_bytes() == originals[snapshot.topic]
            assert topic_prepared.source_path.read_bytes() == originals[snapshot.topic]

    def test_resolve_jobs_skips_existing_full_report_unless_forced(
        self, tmp_path: Path
    ) -> None:
        target = make_report_target(tmp_path)
        report = tmp_path / "reports" / "2026-08-02.md"
        report.parent.mkdir(parents=True)
        report.write_text("existing", encoding="utf-8")

        assert resolve_jobs([target], tmp_path / "reports") == []
        assert resolve_jobs([target], tmp_path / "reports", force=True) == [
            AnalysisJob(target, report)
        ]

    def test_materializes_images_and_video_contact_sheets(
        self, tmp_path: Path
    ) -> None:
        target = make_report_target(tmp_path)
        write_snapshot(
            target.snapshots[-1].path,
            [
                post("image", media_url="https://i.redd.it/demo.png"),
                post(
                    "video",
                    media_url="https://v.redd.it/demo",
                    is_video=True,
                ),
                post("gallery", media_url="https://www.reddit.com/gallery/gallery"),
            ],
        )
        prepared = prepare_report(target, tmp_path / "artifacts")

        def fake_image(_url: str, stem: Path) -> Path:
            path = stem.with_suffix(".png")
            path.write_bytes(b"image")
            return path

        def fake_video(_url: str, destination: Path) -> Path:
            destination.write_bytes(b"contact-sheet")
            return destination

        with (
            patch(
                "idea_pipeline.analyzer.reddit._download_image_asset",
                side_effect=fake_image,
            ),
            patch(
                "idea_pipeline.analyzer.reddit._extract_video_contact_sheet",
                side_effect=fake_video,
            ),
        ):
            assets = materialize_media_assets(prepared)

        assert len(assets.attachments) == 2
        statuses = {
            entry["media_type"]: entry["asset_status"] for entry in assets.entries
        }
        assert statuses == {
            "gallery": "url-only",
            "image": "attached",
            "video": "attached",
        }
        assert json.loads(assets.manifest_path.read_text()) == list(assets.entries)

    @patch("idea_pipeline.analyzer.reddit.requests.get")
    def test_never_requests_an_unapproved_image_host(
        self, mock_get: MagicMock, tmp_path: Path
    ) -> None:
        target = make_report_target(tmp_path)
        write_snapshot(
            target.snapshots[-1].path,
            [post("private", media_url="https://127.0.0.1/private.png")],
        )
        prepared = prepare_report(target, tmp_path / "artifacts")

        assets = materialize_media_assets(prepared)

        mock_get.assert_not_called()
        assert assets.entries[0]["asset_status"] == "failed"
        assert "approved public image host" in assets.entries[0]["asset_error"]


class TestPromptAndCommand:
    def test_prompt_includes_all_streams_and_bounds_output(self, tmp_path: Path) -> None:
        target = make_report_target(tmp_path)
        job = AnalysisJob(target, tmp_path / "reports" / "2026-08-02.md")
        prepared = prepare_report(target, tmp_path / "artifacts")

        prompt = build_prompt(job, prepared)

        assert "Treat every post, comment" in prompt
        assert "# Reddit Builder Intelligence Report - 2026-08-02" in prompt
        assert "Combined corpus: 3 posts across 3 evidence streams" in prompt
        assert "Prioritize concrete operational pain" in prompt
        assert "Treat these as founder hypotheses" in prompt
        assert "One builder's outcome is not automatically repeatable" in prompt
        assert "Do not write an opportunity ranking" in prompt
        assert "All direct external links" in prompt
        assert "Read every entry in media-manifest.json" in prompt
        assert "write media-review.json" in prompt
        assert "Output candidate:\n- report.md\n- media-review.json" in prompt
        assert str(target.snapshots[0].path) not in prompt
        assert "Do not run git commands" in prompt

    def test_builds_noninteractive_copilot_command(self) -> None:
        assert build_copilot_command(
            "prompt", model="gpt-5.4", effort="xhigh", copilot_command="copilot-test"
        ) == [
            "copilot-test",
            "-p",
            "prompt",
            "--model",
            "gpt-5.4",
            "--effort",
            "xhigh",
            "--allow-all-tools",
            "--allow-all-urls",
            "--deny-tool=shell",
            "--disable-builtin-mcps",
            "--disallow-temp-dir",
            "--no-ask-user",
            "--no-remote-export",
            "--no-auto-update",
            "--no-color",
            "--secret-env-vars=COPILOT_GITHUB_TOKEN",
            "--autopilot",
        ]

    def test_adds_visual_attachments_to_copilot_command(self, tmp_path: Path) -> None:
        attachment = tmp_path / "demo.jpg"

        command = build_copilot_command("prompt", attachments=[attachment])

        assert command[-2:] == ["--attachment", str(attachment)]

    def test_parser_rejects_invalid_workers_dates_and_topic_mode(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--workers", "0"])
        with pytest.raises(SystemExit):
            parser.parse_args(["--date", "08-01-2026"])
        with pytest.raises(SystemExit):
            parser.parse_args(["--topic", "saas-build"])


class TestValidation:
    def test_accepts_complete_grounded_report(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        candidate.write_text(valid_report(), encoding="utf-8")

        assert validate_report(
            candidate,
            expected_title="# Reddit Builder Intelligence Report - 2026-08-02",
            allowed_post_ids={"pain1", "idea1", "build1"},
            required_section_post_ids=required_section_ids(),
        ) == []

    def test_accepts_source_derived_project_and_media_links(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        candidate.write_text(
            valid_report(
                image_url="https://i.redd.it/example.png",
                video_url="https://v.redd.it/example",
            ),
            encoding="utf-8",
        )

        errors = validate_report(
            candidate,
            expected_title="# Reddit Builder Intelligence Report - 2026-08-02",
            allowed_post_ids={"pain1", "idea1", "build1"},
            allowed_external_urls={"https://example.com/product"},
            required_project_urls={"https://example.com/product"},
            minimum_project_links=8,
            required_media_urls_by_type={
                "image": {"https://i.redd.it/example.png"},
                "video": {"https://v.redd.it/example"},
            },
        )

        assert errors == []

    def test_rejects_missing_section_unknown_post_and_local_path(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        content = valid_report(post_ids=("unknown", "idea1", "build1"))
        content = content.replace(
            "## 6. Visual and Demo Evidence", "### 6. Visual and Demo Evidence"
        )
        content += "Internal evidence: pipeline/artifacts/reddit/review.txt\n"
        candidate.write_text(content, encoding="utf-8")

        errors = validate_report(
            candidate,
            expected_title="# Reddit Builder Intelligence Report - 2026-08-02",
            allowed_post_ids={"pain1", "idea1", "build1"},
        )

        assert any("required heading" in error for error in errors)
        assert any("internal or local" in error for error in errors)
        assert any("absent from source" in error for error in errors)

    def test_rejects_relative_markdown_links(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        content = valid_report().replace(
            "A linked project",
            "A [local detail](notes.md) and http://example.com show distinct",
        )
        candidate.write_text(content, encoding="utf-8")

        errors = validate_report(
            candidate,
            expected_title="# Reddit Builder Intelligence Report - 2026-08-02",
            allowed_post_ids={"pain1", "idea1", "build1"},
        )

        assert any("public HTTPS" in error for error in errors)
        assert any("Report URLs" in error for error in errors)

    def test_requires_native_current_citation_in_each_stream_section(
        self, tmp_path: Path
    ) -> None:
        candidate = tmp_path / "report.md"
        pain_url = "https://www.reddit.com/r/SaaS/comments/pain1/pain1_title/"
        idea_url = "https://www.reddit.com/r/SaaS/comments/idea1/idea1_title/"
        content = valid_report().replace(
            f"| Manual handoff | Operator | Every review | Delay | Checklist | One account | [pain]({pain_url}) |",
            f"| Manual handoff | Operator | Every review | Delay | Checklist | One account | [idea]({idea_url}) |",
        )
        candidate.write_text(content, encoding="utf-8")

        errors = validate_report(
            candidate,
            expected_title="# Reddit Builder Intelligence Report - 2026-08-02",
            allowed_post_ids={"pain1", "idea1", "build1"},
            required_section_post_ids=required_section_ids(),
        )

        assert any(
            "## 3. Customer Problems and Existing Workarounds" in error
            for error in errors
        )

    def test_rejects_unlisted_project_and_missing_image_evidence(
        self, tmp_path: Path
    ) -> None:
        candidate = tmp_path / "report.md"
        content = valid_report(
            image_url="https://i.redd.it/example.png",
            video_url="https://v.redd.it/example",
        ).replace(
            "https://example.com/product", "https://invented.example/product"
        ).replace("https://i.redd.it/example.png", "https://v.redd.it/example")
        candidate.write_text(content, encoding="utf-8")

        errors = validate_report(
            candidate,
            expected_title="# Reddit Builder Intelligence Report - 2026-08-02",
            allowed_post_ids={"pain1", "idea1", "build1"},
            allowed_external_urls={"https://example.com/product"},
            required_project_urls={"https://example.com/product"},
            minimum_project_links=1,
            required_media_urls_by_type={
                "image": {"https://i.redd.it/example.png"},
                "video": {"https://v.redd.it/example"},
            },
        )

        assert any("external URLs absent" in error for error in errors)
        assert any("direct project links" in error for error in errors)
        assert any("source image" in error for error in errors)

    def test_rejects_malformed_value_table_and_invented_reddit_media(
        self, tmp_path: Path
    ) -> None:
        candidate = tmp_path / "report.md"
        content = valid_report(
            image_url="https://i.redd.it/invented.png",
            video_url="https://v.redd.it/example",
        ).replace("| What it does |", "| Vague summary |")
        candidate.write_text(content, encoding="utf-8")

        errors = validate_report(
            candidate,
            expected_title="# Reddit Builder Intelligence Report - 2026-08-02",
            allowed_post_ids={"pain1", "idea1", "build1"},
            allowed_media_urls={
                "https://i.redd.it/source.png",
                "https://v.redd.it/example",
            },
        )

        assert any("required populated table" in error for error in errors)
        assert any("Reddit media URLs absent" in error for error in errors)

    def test_validates_complete_media_review(self, tmp_path: Path) -> None:
        review = tmp_path / "media-review.json"
        report = tmp_path / "report.md"
        entries = [
            {
                "post_id": "image",
                "url": "https://i.redd.it/demo.png",
                "media_type": "image",
                "asset_status": "attached",
            },
            {
                "post_id": "video",
                "url": "https://v.redd.it/demo",
                "media_type": "video",
                "asset_status": "attached",
            },
        ]
        review.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [
                        {
                            "post_id": entry["post_id"],
                            "media_url": entry["url"],
                            "media_type": entry["media_type"],
                            "status": "inspected",
                            "observation": "The attached visual was inspected and shows a concrete interface.",
                            "report_included": True,
                        }
                        for entry in entries
                    ],
                }
            ),
            encoding="utf-8",
        )
        report.write_text(
            valid_report(
                image_url="https://i.redd.it/demo.png",
                video_url="https://v.redd.it/demo",
            ),
            encoding="utf-8",
        )

        assert validate_media_review(
            review, expected_entries=entries, report_path=report
        ) == []

        document = json.loads(review.read_text())
        document["items"][0]["report_included"] = False
        review.write_text(json.dumps(document), encoding="utf-8")
        errors = validate_media_review(
            review, expected_entries=entries, report_path=report
        )
        assert any("does not match report.md" in error for error in errors)

        document["items"][0]["status"] = "unavailable"
        document["items"].pop()
        review.write_text(json.dumps(document), encoding="utf-8")
        errors = validate_media_review(review, expected_entries=entries)

        assert any("cannot be marked unavailable" in error for error in errors)
        assert any("missing manifest item" in error for error in errors)
        assert any("attached video" in error for error in errors)


class TestAnalysisBoundary:
    @patch("idea_pipeline.analyzer.reddit.subprocess.run")
    def test_valid_candidate_is_atomically_published(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = make_report_target(tmp_path)
        report = tmp_path / "reports" / "2026-08-02.md"
        job = AnalysisJob(target, report)
        candidate = (
            tmp_path
            / "artifacts"
            / REPORT_ARTIFACT_NAME
            / "2026-08-02"
            / "report.md"
        )

        def generate(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            candidate.write_text(valid_report(), encoding="utf-8")
            (candidate.parent / "media-review.json").write_text(
                '{"version": 1, "items": []}\n', encoding="utf-8"
            )
            return subprocess.CompletedProcess([], 0, stdout="done", stderr="")

        mock_run.side_effect = generate
        monkeypatch.setenv("REDDIT_COOKIES", "must-not-reach-copilot")
        monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "copilot-token")
        originals = {item.path: item.path.read_bytes() for item in target.snapshots}

        result = analyze_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "published"
        assert report.read_text(encoding="utf-8") == valid_report()
        assert all(path.read_bytes() == content for path, content in originals.items())
        assert mock_run.call_args.kwargs["cwd"] == candidate.parent
        assert mock_run.call_args.kwargs["check"] is False
        assert "REDDIT_COOKIES" not in mock_run.call_args.kwargs["env"]
        assert mock_run.call_args.kwargs["env"]["COPILOT_GITHUB_TOKEN"] == "copilot-token"

    @patch("idea_pipeline.analyzer.reddit.subprocess.run")
    def test_invalid_candidate_never_replaces_existing_report(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        target = make_report_target(tmp_path)
        report = tmp_path / "reports" / "2026-08-02.md"
        report.parent.mkdir(parents=True)
        report.write_text("known-good report\n", encoding="utf-8")
        job = AnalysisJob(target, report)
        candidate = (
            tmp_path
            / "artifacts"
            / REPORT_ARTIFACT_NAME
            / "2026-08-02"
            / "report.md"
        )

        def generate(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            candidate.write_text("# incomplete\n", encoding="utf-8")
            return subprocess.CompletedProcess([], 0, stdout="done", stderr="")

        mock_run.side_effect = generate

        result = analyze_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "failed"
        assert report.read_text(encoding="utf-8") == "known-good report\n"
        assert (candidate.parent / "validation-errors.json").exists()

    @patch("idea_pipeline.analyzer.reddit.subprocess.run")
    def test_prepare_only_cli_builds_one_combined_sandbox(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        make_report_target(tmp_path)

        exit_code = main(
            [
                "--date",
                "2026-08-02",
                "--prepare-only",
                "--data-dir",
                str(tmp_path / "data"),
                "--reports-dir",
                str(tmp_path / "reports"),
                "--artifacts-dir",
                str(tmp_path / "artifacts"),
            ]
        )

        assert exit_code == 0
        mock_run.assert_not_called()
        root = tmp_path / "artifacts" / REPORT_ARTIFACT_NAME / "2026-08-02"
        assert (root / "metadata.json").exists()
        assert len(list((root / "topics").glob("*/*/source.json"))) == 3
        prompt = (root / "prompt.txt").read_text()
        assert "Generate exactly one evidence-grounded Reddit Builder Intelligence Report" in prompt
        assert "Do not write an opportunity ranking" in prompt
        assert not (root / "report.md").exists()
        assert not (tmp_path / "reports" / "2026-08-02.md").exists()
