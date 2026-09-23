"""Tests for deterministic builder intelligence preparation and publication."""

from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from idea_pipeline import PROJECT_ROOT, REPOSITORY_ROOT
from idea_pipeline.analyzer.builder import (
    DEFAULT_ARTIFACTS_DIR,
    DEFAULT_EFFORT,
    DEFAULT_ENV_FILE,
    DEFAULT_HACKERNEWS_DATA_DIR,
    DEFAULT_LIMIT,
    DEFAULT_MAX_AI_CREDITS,
    DEFAULT_MODEL,
    DEFAULT_REDDIT_DATA_DIR,
    DEFAULT_REPORTS_DIR,
    MEDIA_AUDIT_EFFORT,
    MEDIA_AUDIT_MAX_AI_CREDITS,
    MEDIA_AUDIT_MODEL,
    REPORT_ARTIFACT_NAME,
    REQUIRED_REDDIT_TOPICS,
    AnalysisJob,
    PreparedMediaAssets,
    PreparedReportArtifacts,
    ReportTarget,
    SnapshotTarget,
    _attached_media_repair_entries,
    _canonical_url,
    _cited_attached_media_entries,
    _download_image_asset,
    _merge_media_audit_report,
    _merge_media_repair_batch,
    _summarize_copilot_usage,
    analyze_job,
    audit_cited_media_evidence,
    build_copilot_command,
    build_parser,
    build_prompt,
    discover_reports,
    discover_snapshots,
    group_snapshots,
    humanize_topic,
    main,
    materialize_media_assets,
    normalize_hackernews_citations,
    normalize_media_review,
    normalize_reddit_citations,
    normalize_report_links,
    normalize_report_structure,
    prepare_report,
    prepare_snapshot,
    rank_score,
    repair_attached_media_review,
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


def hackernews_post(
    post_id: str,
    *,
    stream: str = "show-hn",
    score: int = 10,
    comments: int = 4,
) -> dict[str, object]:
    return {
        "source": "hackernews",
        "stream": stream,
        "id": post_id,
        "title": f"Show HN: Project {post_id}",
        "subreddit": stream,
        "author": f"hn_{post_id}",
        "score": score,
        "num_comments": comments,
        "permalink": f"https://news.ycombinator.com/item?id={post_id}",
        "url": f"https://example.com/{post_id}",
        "selftext": "A technical launch with implementation details and measured usage.",
        "is_self": False,
        "is_video": False,
        "time": 1_775_260_800,
    }


def make_report_target(
    root: Path,
    snapshot_date: date = date(2026, 8, 2),
    *,
    post_ids: tuple[str, str, str] = ("pain1", "idea1", "build1"),
) -> ReportTarget:
    snapshots: list[SnapshotTarget] = []
    for topic, post_id in zip(REQUIRED_REDDIT_TOPICS, post_ids):
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
    visual_items: list[str] = []
    if image_url:
        visual_items.append(
            f"[![The review queue visibly contains one item]({image_url})]({image_url}) "
            "The image shows one queued item"
        )
    if video_url:
        visual_items.append(f"[watch video]({video_url}) shows the sampled review flow")
    visual_proof = "; ".join(visual_items) + "." if visual_items else "None."
    sections = [
        (
            "## 1. Executive Brief",
            f"""A linked project, a concrete pain, and a visual demo are available ([pain]({pain}), [idea]({idea}), [build]({build})).

### Key Highlights

- **Best new artifacts:** Review tool routes operator work.
- **Strongest traction:** One signup is reported.
- **Sharpest user pain:** Manual handoffs delay review work.
- **Most useful visual:** The queue visibly contains one item.
- **Biggest evidence gap:** Retention is unknown.

### Coverage and Caveats

Three current streams are represented; the evidence is limited to one account per stream.""",
        ),
        (
            "## 2. Evidence Ledger",
            f"""### Review tool

**Primary link:** [Open project]({project})

**Stage:** `Launched`

**User or problem:** Operators handling review queues.

**Build, test, or event:** A prototype was tested, then launched through direct outreach.

**Evidence:** One signup is author-reported.

**Visual proof:** {visual_proof}

**Limitation or next proof:** Retention and repeat use are unknown.

**Reddit source:** [idea]({idea}) · [build]({build})""",
        ),
        (
            "## 3. Customer Problems and Existing Workarounds",
            f"""| Problem | Affected user and context | Trigger and consequence | Current workaround | Evidence breadth | Sources |
|---|---|---|---|---|---|
| Manual handoff | Operator managing reviews | Every review creates a delayed handoff | Checklist | One account | [pain]({pain}) |""",
        ),
        (
            "## 4. Patterns, Contradictions, and Gaps",
            f"""### Workflow pain can motivate a build without proving retention

**Evidence:** The manual handoff appears in [pain]({pain}), while the tool test appears in [idea]({idea}) and [build]({build}).

**Interpretation:** This is a `Partial` match between a narrow workflow problem and one builder response.

**Missing proof:** Repeat use by independent operators.

### Shipping creates a measurable baseline, not product-market fit

**Evidence:** The builder reports one signup in [build]({build}).

**Interpretation:** The launch establishes initial acquisition only.

**Missing proof:** Activation, retention, and payment.

### Visual proof confirms an interface, not the outcome

**Evidence:** The [image]({image}) and [video]({video}) show the queue and sampled flow.

**Interpretation:** The media corroborates implementation but not user value.

**Missing proof:** Observed time saved during real review work.""",
        ),
        (
            "## 5. Decisions and Watchlist",
            f"""### Practical Moves

- Measure repeat review completion before adding features.
- Preserve the manual checklist as a baseline for time-saved comparisons.
- Treat the first signup as acquisition evidence, not retention.

### Watchlist

| Priority | Case or signal | Current baseline | Trigger to revisit | Why it matters |
|---:|---|---|---|---|
| 1 | Review tool retention | One author-reported signup in [build]({build}) | A second-week retained user or payment | Distinguishes launch attention from durable use |""",
        ),
    ]
    chunks = [f"# Builder Intelligence Report - {report_date}"]
    for heading, body in sections:
        chunks.extend(["", heading, "", body, "", "---"])
    return "\n".join(chunks).rstrip("-\n") + "\n"


def required_section_ids() -> dict[str, set[str]]:
    return {
        "## 3. Customer Problems and Existing Workarounds": {"pain1"},
        "## 2. Evidence Ledger": {"idea1", "build1"},
    }


class TestPathsAndHelpers:
    def test_default_paths_follow_repository_layout(self) -> None:
        assert DEFAULT_REDDIT_DATA_DIR == REPOSITORY_ROOT / "data" / "reddit"
        assert DEFAULT_HACKERNEWS_DATA_DIR == REPOSITORY_ROOT / "data" / "hackernews"
        assert DEFAULT_REPORTS_DIR == REPOSITORY_ROOT / "reports" / "builder"
        assert DEFAULT_ARTIFACTS_DIR == PROJECT_ROOT / "artifacts" / "builder"
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
        rich["comments_data"] = [{"id": "c1", "author": "user", "body": "detail", "score": 2}]

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

    def test_malformed_ipv6_like_url_is_ignored(self) -> None:
        malformed = "https://[broken"
        evidence = post("broken-url")
        evidence["url"] = malformed
        evidence["comments_data"] = [
            {
                "id": "c1",
                "author": "operator",
                "body": f"This workflow fails near {malformed} and needs manual repair.",
                "score": 3,
            }
        ]

        assert _canonical_url(malformed) == ""
        assert isinstance(rank_score(evidence), float)

    def test_canonical_url_strips_inline_code_backtick(self) -> None:
        assert _canonical_url("https://i.redd.it/example.png`") == "https://i.redd.it/example.png"


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
        assert [item.topic for item in reports[0].snapshots] == list(REQUIRED_REDDIT_TOPICS)

    def test_discover_reports_requires_every_stream_for_explicit_date(self, tmp_path: Path) -> None:
        for topic in REQUIRED_REDDIT_TOPICS[:2]:
            write_snapshot(tmp_path / topic / "2026-08-02.json", [post(f"{topic}1")])
        (tmp_path / REQUIRED_REDDIT_TOPICS[2]).mkdir(parents=True)

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

    def test_missing_optional_hackernews_directory_does_not_block_report(
        self, tmp_path: Path
    ) -> None:
        expected = make_report_target(tmp_path).snapshots

        reports = discover_reports(
            tmp_path / "data",
            optional_source_dirs={"hackernews": tmp_path / "missing-hackernews"},
            dates=[date(2026, 8, 2)],
        )

        assert len(reports) == 1
        assert reports[0].is_multi_source is False
        assert [item.path for item in reports[0].snapshots] == [
            item.path for item in expected
        ]

    def test_rejects_unknown_optional_source(self, tmp_path: Path) -> None:
        make_report_target(tmp_path)

        with pytest.raises(ValueError, match="Unknown optional source"):
            discover_reports(
                tmp_path / "data",
                optional_source_dirs={"unknown": tmp_path / "unknown"},
                dates=[date(2026, 8, 2)],
            )

    def test_discover_reports_appends_optional_hackernews_streams(self, tmp_path: Path) -> None:
        snapshot_date = date(2026, 8, 2)
        expected = make_report_target(tmp_path, snapshot_date).snapshots
        hn_dir = tmp_path / "hackernews"
        write_snapshot(
            hn_dir / "show-hn" / f"{snapshot_date.isoformat()}.json",
            [hackernews_post("123")],
        )

        reports = discover_reports(
            tmp_path / "data",
            optional_source_dirs={"hackernews": hn_dir},
            dates=[snapshot_date],
        )

        assert len(reports) == 1
        assert [item.path for item in reports[0].snapshots[:3]] == [
            item.path for item in expected
        ]
        assert reports[0].snapshots[-1].topic == "show-hn"
        assert reports[0].snapshots[-1].source == "hackernews"
        assert reports[0].is_multi_source is True


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

    def test_external_link_manifest_normalizes_bare_domains(self, tmp_path: Path) -> None:
        source = tmp_path / "data" / "saas-build" / "2026-08-01.json"
        write_snapshot(
            source,
            [
                post(
                    "bare",
                    title="A card tracker launched at cardnDEX.com",
                    selftext=(
                        "Try gesture.live for the demo and (usestyla dot com) for outfits. "
                        "README.md and media-review.json are filenames, not websites. "
                        "The value dot lower wording is prose, not a domain. "
                        "Use [legacy.test](http://legacy.test) for the old endpoint."
                    ),
                )
            ],
        )

        prepared = prepare_snapshot(
            SnapshotTarget("saas-build", date(2026, 8, 1), source),
            tmp_path / "artifacts",
        )

        links = json.loads(prepared.links_path.read_text())
        assert {item["canonical_url"] for item in links} == {
            "http://legacy.test/",
            "https://cardndex.com/",
            "https://gesture.live/",
            "https://usestyla.com/",
        }
        assert "https://legacy.test/" not in {item["canonical_url"] for item in links}
        source_locations = {item["canonical_url"]: item["source_location"] for item in links}
        assert source_locations["https://cardndex.com/"] == "title"
        assert source_locations["https://usestyla.com/"] == "selftext"

    def test_hackernews_discussion_is_not_a_project_candidate(self, tmp_path: Path) -> None:
        source = tmp_path / "hackernews" / "show-hn" / "2026-08-01.json"
        write_snapshot(source, [hackernews_post("123")])

        prepared = prepare_snapshot(
            SnapshotTarget(
                "show-hn",
                date(2026, 8, 1),
                source,
                source="hackernews",
            ),
            tmp_path / "artifacts",
        )

        links = json.loads(prepared.links_path.read_text())
        assert {
            (item["canonical_url"], item["kind"])
            for item in links
        } == {
            ("https://example.com/123", "website"),
            ("https://news.ycombinator.com/item?id=123", "discussion"),
        }

    def test_prepares_one_sandbox_with_all_three_sources(self, tmp_path: Path) -> None:
        target = make_report_target(tmp_path)
        originals = {item.topic: item.path.read_bytes() for item in target.snapshots}

        prepared = prepare_report(target, tmp_path / "artifacts")

        assert prepared.directory == (tmp_path / "artifacts" / REPORT_ARTIFACT_NAME / "2026-08-02")
        assert prepared.total_posts == 3
        assert len(prepared.topic_artifacts) == 3
        assert "Builder Intelligence Analysis" in (
            prepared.instructions_path.read_text()
        )
        metadata = json.loads(prepared.metadata_path.read_text())
        assert metadata["report_type"] == "builder-intelligence-v2"
        assert metadata["total_posts"] == 3
        assert metadata["external_link_count"] == 1
        assert [source["topic"] for source in metadata["sources"]] == list(REQUIRED_REDDIT_TOPICS)
        assert prepared.link_manifest_path.exists()
        assert prepared.media_manifest_path.exists()
        for snapshot, topic_prepared in zip(target.snapshots, prepared.topic_artifacts):
            assert snapshot.path.read_bytes() == originals[snapshot.topic]
            assert topic_prepared.source_path.read_bytes() == originals[snapshot.topic]

    def test_compacts_history_instead_of_copying_full_snapshots(self, tmp_path: Path) -> None:
        target = make_report_target(tmp_path, date(2026, 8, 9))
        snapshot = target.snapshots[0]
        history_paths: list[Path] = []
        for day in range(2, 9):
            path = snapshot.path.parent / f"2026-08-{day:02d}.json"
            write_snapshot(
                path,
                [
                    post(
                        f"history-{day}-{index}",
                        score=20 - index,
                        selftext=("Detailed historical workflow evidence. " * 30),
                    )
                    for index in range(12)
                ],
            )
            history_paths.append(path)
        compact_target = SnapshotTarget(
            snapshot.topic,
            snapshot.snapshot_date,
            snapshot.path,
            tuple(history_paths),
        )

        prepared = prepare_snapshot(compact_target, tmp_path / "artifacts")

        assert [path.name for path in prepared.history_paths] == ["summary.json"]
        summary = json.loads(prepared.history_paths[0].read_text())
        assert len(summary["snapshots"]) == 7
        assert all(len(item["top_evidence"]) == 6 for item in summary["snapshots"])
        metadata = json.loads(prepared.metadata_path.read_text())
        assert metadata["history_summary_bytes"] < metadata["history_source_bytes"]

    def test_resolve_jobs_skips_existing_full_report_unless_forced(self, tmp_path: Path) -> None:
        target = make_report_target(tmp_path)
        report = tmp_path / "reports" / "2026-08-02.md"
        report.parent.mkdir(parents=True)
        report.write_text("existing", encoding="utf-8")

        assert resolve_jobs([target], tmp_path / "reports") == []
        assert resolve_jobs([target], tmp_path / "reports", force=True) == [
            AnalysisJob(target, report)
        ]

    def test_resolve_jobs_prioritizes_newest_missing_reports(self, tmp_path: Path) -> None:
        targets = [
            make_report_target(tmp_path / f"day-{day}", date(2026, 8, day)) for day in (2, 3, 4)
        ]

        jobs = resolve_jobs(targets, tmp_path / "reports", limit=2)

        assert [job.target.date_text for job in jobs] == ["2026-08-04", "2026-08-03"]

    def test_materializes_images_and_video_contact_sheets(self, tmp_path: Path) -> None:
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
                "idea_pipeline.analyzer.builder._download_image_asset",
                side_effect=fake_image,
            ),
            patch(
                "idea_pipeline.analyzer.builder._extract_video_contact_sheet",
                side_effect=fake_video,
            ),
        ):
            assets = materialize_media_assets(prepared)

        assert len(assets.attachments) == 2
        statuses = {entry["media_type"]: entry["asset_status"] for entry in assets.entries}
        assert statuses == {
            "gallery": "url-only",
            "image": "attached",
            "video": "attached",
        }
        assert json.loads(assets.manifest_path.read_text()) == list(assets.entries)

    @patch("idea_pipeline.analyzer.builder.subprocess.run")
    @patch("idea_pipeline.analyzer.builder.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("idea_pipeline.analyzer.builder.requests.get")
    def test_converts_gif_to_a_model_safe_static_png(
        self,
        mock_get: MagicMock,
        _mock_which: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        response = MagicMock()
        response.is_redirect = False
        response.is_permanent_redirect = False
        response.headers = {"content-type": "image/gif"}
        response.iter_content.return_value = [b"GIF89a-source"]
        mock_get.return_value = response

        def convert(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            Path(command[-1]).write_bytes(b"\x89PNG\r\n\x1a\nconverted")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        mock_run.side_effect = convert

        asset = _download_image_asset("https://i.redd.it/animated.gif", tmp_path / "asset")

        assert asset == tmp_path / "asset.png"
        assert asset.read_bytes().startswith(b"\x89PNG")
        assert not list(tmp_path.glob("*.gif"))
        command = mock_run.call_args.args[0]
        assert command[0] == "/usr/bin/ffmpeg"
        assert "thumbnail=100" in command[command.index("-vf") + 1]
        assert command[command.index("-frames:v") + 1] == "1"

    @patch("idea_pipeline.analyzer.builder.requests.get")
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
        assert "# Builder Intelligence Report - 2026-08-02" in prompt
        assert "Combined corpus: 3 posts across 3 evidence streams" in prompt
        assert "Prioritize concrete operational pain" in prompt
        assert "Treat these as founder hypotheses" in prompt
        assert "One builder's outcome is not automatically repeatable" in prompt
        assert "Do not write an opportunity ranking" in prompt
        assert "All direct external links" in prompt
        assert "Read every entry in media-manifest.json" in prompt
        assert "animated or unsupported source images" in prompt
        assert "Make a destination clickable only when that exact URL appears" in prompt
        assert "visible only inside an attachment" in prompt
        assert "Source diversity is a tiebreaker, not a quota" in prompt
        assert "Order Section 2 by decision value" in prompt
        assert "write media-review.json" in prompt
        assert "media-review item for that exact media URL" in prompt
        assert "Give each case one `###` subsection" in prompt
        assert "linked Markdown image" in prompt
        assert "Output candidate:\n- report.md\n- media-review.json" in prompt
        assert str(target.snapshots[0].path) not in prompt
        assert "Do not run git commands" in prompt

    def test_prompt_includes_optional_hackernews_source(self, tmp_path: Path) -> None:
        target = make_report_target(tmp_path)
        hn_path = tmp_path / "hackernews" / "show-hn" / "2026-08-02.json"
        write_snapshot(hn_path, [hackernews_post("123")])
        target = ReportTarget(
            target.report_date,
            (*target.snapshots, SnapshotTarget("show-hn", target.report_date, hn_path, source="hackernews")),
        )
        job = AnalysisJob(target, tmp_path / "reports" / "2026-08-02.md")
        prepared = prepare_report(target, tmp_path / "artifacts")

        prompt = build_prompt(job, prepared)

        assert "# Builder Intelligence Report - 2026-08-02" in prompt
        assert "show-hn (hackernews)" in prompt
        assert "technical launches, linked artifacts" in prompt
        assert "Reddit or Hacker News discussions" in prompt
        metadata = json.loads(prepared.metadata_path.read_text())
        assert metadata["sources"][-1]["platform"] == "hackernews"

    def test_builds_noninteractive_copilot_command(self) -> None:
        assert build_copilot_command(
            "prompt", model="gpt-5.4", effort="xhigh", copilot_command="copilot-test"
        ) == [
            "copilot-test",
            "-p",
            "prompt",
            "--model",
            "gpt-5.4",
            "--reasoning-effort",
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
            "--max-ai-credits",
            "150",
        ]

    def test_defaults_to_low_cost_synthesis_model(self) -> None:
        args = build_parser().parse_args([])
        command = build_copilot_command("prompt")

        assert DEFAULT_MODEL == "gpt-6-luna"
        assert DEFAULT_EFFORT == "high"
        assert DEFAULT_MAX_AI_CREDITS == 150
        assert (args.model, args.effort) == (DEFAULT_MODEL, DEFAULT_EFFORT)
        assert args.max_ai_credits == DEFAULT_MAX_AI_CREDITS
        assert command[command.index("--model") + 1] == DEFAULT_MODEL
        assert command[command.index("--reasoning-effort") + 1] == DEFAULT_EFFORT
        assert (
            command[command.index("--max-ai-credits") + 1]
            == str(DEFAULT_MAX_AI_CREDITS)
        )
        assert args.limit == DEFAULT_LIMIT == 1

    def test_adds_visual_attachments_to_copilot_command(self, tmp_path: Path) -> None:
        attachment = tmp_path / "demo.jpg"

        command = build_copilot_command("prompt", attachments=[attachment])

        assert command[-2:] == ["--attachment", str(attachment)]

    def test_adds_usage_telemetry_and_credit_limit_to_copilot_command(
        self, tmp_path: Path
    ) -> None:
        usage_path = tmp_path / "usage.json"

        command = build_copilot_command(
            "prompt",
            usage_output_file=usage_path,
            max_ai_credits=300,
        )

        assert command[command.index("--usage-output-file") + 1] == str(usage_path)
        assert command[command.index("--max-ai-credits") + 1] == "300"

    def test_summarizes_copilot_usage_cost_and_tokens(self, tmp_path: Path) -> None:
        usage_path = tmp_path / "copilot-usage.json"
        usage_path.write_text(
            json.dumps(
                {
                    "totalNanoAiu": 14_669_664_000,
                    "totalApiDurationMs": 713_440,
                    "tokenDetails": {
                        "cache_read": {"tokenCount": 3_992_019},
                        "cache_write": {"tokenCount": 585_578},
                        "output": {"tokenCount": 67_132},
                    },
                    "modelMetrics": {
                        "gpt-6-luna": {
                            "requests": {"count": 44},
                            "totalNanoAiu": 14_669_664_000,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        summary, errors = _summarize_copilot_usage((usage_path,))

        assert errors == []
        assert summary == {
            "files": ["copilot-usage.json"],
            "ai_credits": 14.669664,
            "cost_usd": 0.146697,
            "api_duration_ms": 713_440,
            "token_counts": {
                "cache_read": 3_992_019,
                "cache_write": 585_578,
                "output": 67_132,
            },
            "models": {
                "gpt-6-luna": {
                    "requests": 44,
                    "ai_credits": 14.669664,
                    "cost_usd": 0.146697,
                }
            },
        }

    def test_parser_rejects_invalid_workers_dates_and_topic_mode(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--workers", "0"])
        with pytest.raises(SystemExit):
            parser.parse_args(["--max-ai-credits", "29"])
        with pytest.raises(SystemExit):
            parser.parse_args(["--date", "08-01-2026"])
        with pytest.raises(SystemExit):
            parser.parse_args(["--topic", "saas-build"])
        with pytest.raises(SystemExit):
            parser.parse_args(["--source-data-dir", "hackernews"])

    def test_parser_accepts_generic_optional_source_directory(self, tmp_path: Path) -> None:
        args = build_parser().parse_args(
            ["--source-data-dir", f"hackernews={tmp_path / 'hackernews'}"]
        )

        assert args.source_data_dirs == [("hackernews", tmp_path / "hackernews")]


class TestValidation:
    def test_accepts_complete_grounded_report(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        candidate.write_text(valid_report(), encoding="utf-8")

        assert (
            validate_report(
                candidate,
                expected_title="# Builder Intelligence Report - 2026-08-02",
                allowed_post_ids={"pain1", "idea1", "build1"},
                required_section_post_ids=required_section_ids(),
            )
            == []
        )

    def test_accepts_generic_source_case_labels(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        candidate.write_text(
            valid_report().replace("**Reddit source:**", "**Source:**"),
            encoding="utf-8",
        )

        assert (
            validate_report(
                candidate,
                expected_title="# Builder Intelligence Report - 2026-08-02",
                allowed_post_ids={"pain1", "idea1", "build1"},
                required_section_post_ids=required_section_ids(),
            )
            == []
        )

    def test_requires_current_hackernews_citation_when_source_is_supplied(
        self, tmp_path: Path
    ) -> None:
        candidate = tmp_path / "report.md"
        hackernews_url = "https://news.ycombinator.com/item?id=123"
        candidate.write_text(valid_report(), encoding="utf-8")

        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
            required_hackernews_urls={hackernews_url},
        )

        assert any("Hacker News source" in error for error in errors)

        candidate.write_text(
            valid_report().replace(
                "Three current streams are represented;",
                f"Three current streams and [one HN discussion]({hackernews_url}) "
                "are represented;",
            ),
            encoding="utf-8",
        )
        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
            required_hackernews_urls={hackernews_url},
        )

        assert not any("Hacker News source" in error for error in errors)

    def test_rejects_report_without_executive_highlights(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        candidate.write_text(
            valid_report().replace(
                """### Key Highlights

- **Best new artifacts:** Review tool routes operator work.
- **Strongest traction:** One signup is reported.
- **Sharpest user pain:** Manual handoffs delay review work.
- **Most useful visual:** The queue visibly contains one item.
- **Biggest evidence gap:** Retention is unknown.

### Coverage and Caveats

Three current streams are represented; the evidence is limited to one account per stream.

""",
                "",
            ),
            encoding="utf-8",
        )

        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
        )

        assert any("required highlight heading" in error for error in errors)
        assert any("required highlight label" in error for error in errors)

    def test_rejects_incomplete_or_tabular_evidence_cases(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        content = valid_report().replace(
            "**Limitation or next proof:** Retention and repeat use are unknown.\n\n",
            "",
            1,
        )
        content = content.replace(
            "### Review tool\n",
            "### Review tool\n\n| Legacy | Table |\n|---|---|\n| Old | Shape |\n\n",
            1,
        )
        candidate.write_text(content, encoding="utf-8")

        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
        )

        assert any("case 1 must contain exactly one field" in error for error in errors)
        assert any("case subsections rather than Markdown tables" in error for error in errors)

    def test_rejects_incomplete_synthesis_and_extra_numbered_section(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        content = valid_report().replace("**Missing proof:**", "**Open question:**", 1)
        content += "\n## 6. Duplicate Inventory\n\nThis section should not exist.\n"
        candidate.write_text(content, encoding="utf-8")

        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
        )

        assert any("each synthesis theme" in error for error in errors)
        assert any("unexpected numbered report section" in error for error in errors)

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
            expected_title="# Builder Intelligence Report - 2026-08-02",
            allowed_post_ids={"pain1", "idea1", "build1"},
            allowed_external_urls={"https://example.com/product"},
            allowed_image_urls={"https://i.redd.it/example.png"},
            required_project_urls={"https://example.com/product"},
            minimum_project_links=8,
            required_media_urls_by_type={
                "image": {"https://i.redd.it/example.png"},
                "video": {"https://v.redd.it/example"},
            },
        )

        assert errors == []

    def test_accepts_source_url_without_utm_tracking_parameters(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        clean_url = "https://clevernote.net/en/"
        tracked_url = (
            "https://clevernote.net/en/?utm_source=reddit&utm_medium=organic"
            "&utm_campaign=microsaas&utm_content=attribution-lie"
        )
        candidate.write_text(
            valid_report().replace("https://example.com/product", clean_url),
            encoding="utf-8",
        )

        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
            allowed_post_ids={"pain1", "idea1", "build1"},
            allowed_external_urls={tracked_url},
            required_project_urls={tracked_url},
            minimum_project_links=1,
        )

        assert errors == []

    def test_preserves_functional_query_parameters_when_removing_utm(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        candidate.write_text(
            valid_report().replace(
                "https://example.com/product",
                "https://example.com/product?account=reported",
            ),
            encoding="utf-8",
        )

        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
            allowed_post_ids={"pain1", "idea1", "build1"},
            allowed_external_urls={"https://example.com/product?account=source&utm_source=reddit"},
        )

        assert any("external URLs absent" in error for error in errors)

    def test_rejects_missing_section_unknown_post_and_local_path(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        content = valid_report(post_ids=("unknown", "idea1", "build1"))
        content = content.replace(
            "## 4. Patterns, Contradictions, and Gaps",
            "### 4. Patterns, Contradictions, and Gaps",
        )
        content += "Internal evidence: pipeline/artifacts/builder/review.txt\n"
        candidate.write_text(content, encoding="utf-8")

        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
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

        warnings: list[str] = []
        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
            allowed_post_ids={"pain1", "idea1", "build1"},
            warnings=warnings,
        )

        assert any("public HTTPS" in error for error in errors)
        assert any("HTTP link" in warning for warning in warnings)

    def test_allows_source_derived_http_links_with_a_warning(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        candidate.write_text(
            valid_report().replace("https://example.com/product", "http://indepai.app"),
            encoding="utf-8",
        )
        warnings: list[str] = []

        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
            allowed_external_urls={"http://indepai.app"},
            warnings=warnings,
        )

        assert errors == []
        assert warnings == [
            ("Report uses an HTTP link; HTTPS is preferred when available: http://indepai.app")
        ]

    def test_accepts_an_https_upgrade_of_a_source_http_url(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        candidate.write_text(
            valid_report().replace("https://example.com/product", "https://indepai.app"),
            encoding="utf-8",
        )

        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
            allowed_external_urls={"http://indepai.app"},
        )

        assert errors == []

    def test_warns_when_stream_section_lacks_a_current_snapshot_citation(
        self, tmp_path: Path
    ) -> None:
        candidate = tmp_path / "report.md"
        pain_url = "https://www.reddit.com/r/SaaS/comments/pain1/pain1_title/"
        idea_url = "https://www.reddit.com/r/SaaS/comments/idea1/idea1_title/"
        content = valid_report().replace(
            f"| Manual handoff | Operator managing reviews | Every review creates a delayed handoff | Checklist | One account | [pain]({pain_url}) |",
            f"| Manual handoff | Operator managing reviews | Every review creates a delayed handoff | Checklist | One account | [idea]({idea_url}) |",
        )
        candidate.write_text(content, encoding="utf-8")

        warnings: list[str] = []
        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
            allowed_post_ids={"pain1", "idea1", "build1"},
            required_section_post_ids=required_section_ids(),
            warnings=warnings,
        )

        assert not any("current source snapshot" in error for error in errors)
        assert any(
            "## 3. Customer Problems and Existing Workarounds" in warning for warning in warnings
        )

    def test_rejects_unlisted_project_and_missing_image_evidence(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        content = (
            valid_report(
                image_url="https://i.redd.it/example.png",
                video_url="https://v.redd.it/example",
            )
            .replace("https://example.com/product", "https://invented.example/product")
            .replace("https://i.redd.it/example.png", "https://v.redd.it/example")
        )
        candidate.write_text(content, encoding="utf-8")

        warnings: list[str] = []
        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
            allowed_post_ids={"pain1", "idea1", "build1"},
            allowed_external_urls={"https://example.com/product"},
            required_project_urls={"https://example.com/product"},
            minimum_project_links=1,
            required_media_urls_by_type={
                "image": {"https://i.redd.it/example.png"},
                "video": {"https://v.redd.it/example"},
            },
            warnings=warnings,
        )

        assert any("external URLs absent" in error for error in errors)
        assert any("direct project links" in warning for warning in warnings)
        assert any("source image" in error for error in errors)

    def test_warns_on_nonstandard_table_header_and_rejects_invented_media(
        self, tmp_path: Path
    ) -> None:
        candidate = tmp_path / "report.md"
        content = valid_report(
            image_url="https://i.redd.it/invented.png",
            video_url="https://v.redd.it/example",
        ).replace("| Trigger and consequence |", "| Vague summary |")
        candidate.write_text(content, encoding="utf-8")

        warnings: list[str] = []
        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
            allowed_post_ids={"pain1", "idea1", "build1"},
            allowed_media_urls={
                "https://i.redd.it/source.png",
                "https://v.redd.it/example",
            },
            warnings=warnings,
        )

        assert any("recommended schema" in warning for warning in warnings)
        assert any("Reddit media URLs absent" in error for error in errors)

    def test_allows_source_manifest_external_media(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        candidate.write_text(
            valid_report(image_url="https://i.imgur.com/source.png"),
            encoding="utf-8",
        )

        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
            allowed_external_urls={"https://example.com/product"},
            allowed_media_urls={"https://i.imgur.com/source.png"},
        )

        assert errors == []

    def test_rejects_a_required_table_section_without_a_populated_table(
        self, tmp_path: Path
    ) -> None:
        candidate = tmp_path / "report.md"
        candidate.write_text(
            valid_report().replace(
                "| Manual handoff | Operator managing reviews | Every review creates a delayed "
                "handoff | Checklist | One account | "
                "[pain](https://www.reddit.com/r/SaaS/comments/pain1/pain1_title/) |",
                "Problem details were not tabulated.",
            ),
            encoding="utf-8",
        )

        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
        )

        assert any("populated Markdown table" in error for error in errors)

    def test_rejects_an_oversized_project_inventory(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        original_case = valid_report().partition("### Review tool\n")[2].partition(
            "\n---\n\n## 3."
        )[0]
        cases = "\n\n".join(
            f"### Review tool {index}\n{original_case}" for index in range(1, 26)
        )
        content = valid_report().replace(
            f"### Review tool\n{original_case}",
            cases,
            1,
        )
        candidate.write_text(content, encoding="utf-8")

        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
        )

        assert any("exceeds the 24-case" in error for error in errors)

    def test_allows_decision_useful_case_without_primary_artifact_link(
        self, tmp_path: Path
    ) -> None:
        candidate = tmp_path / "report.md"
        content = valid_report().replace(
            "**Primary link:** [Open project](https://example.com/product)",
            "**Primary link:** Not provided",
            1,
        )
        candidate.write_text(content, encoding="utf-8")

        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
        )

        assert errors == []

    def test_accepts_markdown_tables_without_trailing_pipes(self, tmp_path: Path) -> None:
        candidate = tmp_path / "report.md"
        content = "\n".join(
            line[:-1] if line.startswith("|") and line.endswith("|") else line
            for line in valid_report().splitlines()
        )
        candidate.write_text(content, encoding="utf-8")

        errors = validate_report(
            candidate,
            expected_title="# Builder Intelligence Report - 2026-08-02",
        )

        assert errors == []

    def test_normalizes_not_provided_markdown_destination(self, tmp_path: Path) -> None:
        report = tmp_path / "report.md"
        report.write_text(
            "| [Pricing tool](Not provided) | `https://i.redd.it/example.png` |\n",
            encoding="utf-8",
        )

        messages = normalize_report_links(
            report,
            allowed_external_urls=(),
            allowed_media_urls=("https://i.redd.it/example.png",),
        )

        assert report.read_text(encoding="utf-8") == (
            "| Pricing tool — Not provided | [View media](https://i.redd.it/example.png) |\n"
        )
        assert messages == [
            (
                "converted inline-code media URL to Markdown link: "
                "https://i.redd.it/example.png"
            ),
            "converted non-URL Markdown destination to plain text: Not provided",
        ]

    def test_normalizes_report_headings_labels_and_stages(self, tmp_path: Path) -> None:
        report = tmp_path / "report.md"
        report.write_text(
            "## 1. Bottom line\n\n"
            "### Example\n\n"
            "Primary link: Not provided\n\n"
            "- Stage: Pricing validation.  \n\n"
            "#### Evidence: One interview.\n",
            encoding="utf-8",
        )

        messages = normalize_report_structure(report)

        assert report.read_text(encoding="utf-8") == (
            "## 1. Executive Brief\n\n"
            "### Example\n\n"
            "**Primary link:** Not provided\n\n"
            "**Stage:** `Prototype`\n\n"
            "**Evidence:** One interview.\n"
        )
        assert any("normalized report heading" in message for message in messages)
        assert any("stage to Prototype" in message for message in messages)

    def test_normalizes_report_whitespace_without_other_repairs(self, tmp_path: Path) -> None:
        report = tmp_path / "report.md"
        report.write_text("# Report  \n\n\n", encoding="utf-8")

        messages = normalize_report_structure(report)

        assert report.read_text(encoding="utf-8") == "# Report\n"
        assert messages == ["normalized report line endings and trailing whitespace"]

    def test_upgrades_schemeless_linked_image_urls(self, tmp_path: Path) -> None:
        report = tmp_path / "report.md"
        source = "https://i.redd.it/example.png"
        report.write_text(
            "**Visual proof:** "
            "[![Visible chart](i.redd.it/example.png)](i.redd.it/example.png)\n",
            encoding="utf-8",
        )

        messages = normalize_report_links(
            report,
            allowed_external_urls=(),
            allowed_media_urls=(source,),
            image_media_urls=(source,),
        )

        assert report.read_text(encoding="utf-8") == (
            f"**Visual proof:** [![Visible chart]({source})]({source})\n"
        )
        assert any("schemeless" in message for message in messages)

    def test_restores_grounded_preview_url_from_schemeless_image_alias(
        self, tmp_path: Path
    ) -> None:
        report = tmp_path / "report.md"
        source = (
            "https://preview.redd.it/example.png"
            "?width=1082&format=png&auto=webp&s=source"
        )
        report.write_text(
            "**Visual proof:** ![Visible chart](i.redd.it/example.png)\n",
            encoding="utf-8",
        )

        normalize_report_links(
            report,
            allowed_external_urls=(),
            allowed_media_urls=(source,),
            image_media_urls=(source,),
        )

        assert report.read_text(encoding="utf-8") == (
            f"**Visual proof:** [![Visible chart]({source})]({source})\n"
        )

    def test_embeds_and_links_visual_proof_images(self, tmp_path: Path) -> None:
        report = tmp_path / "report.md"
        image_url = "https://i.redd.it/example.png"
        report.write_text(
            f"**Visual proof:** `{image_url}` shows the queue.\n",
            encoding="utf-8",
        )

        messages = normalize_report_links(
            report,
            allowed_external_urls=(),
            allowed_media_urls=(image_url,),
            image_media_urls=(image_url,),
        )

        assert report.read_text(encoding="utf-8") == (
            f"**Visual proof:** [![View media]({image_url})]({image_url}) "
            "shows the queue.\n"
        )
        assert any("embedded visual evidence image" in message for message in messages)

    def test_converts_video_image_embed_to_link(self, tmp_path: Path) -> None:
        report = tmp_path / "report.md"
        video_url = "https://v.redd.it/example"
        report.write_text(
            f"**Visual proof:** [![Sampled video frames]({video_url})]({video_url})\n",
            encoding="utf-8",
        )

        messages = normalize_report_links(
            report,
            allowed_external_urls=(),
            allowed_media_urls=(video_url,),
            image_media_urls=(),
        )

        assert report.read_text(encoding="utf-8") == (
            f"**Visual proof:** [Sampled video frames]({video_url})\n"
        )
        assert messages == [
            f"converted non-image media embed to Markdown link: {video_url}"
        ]

    def test_rejects_non_image_media_embed(self, tmp_path: Path) -> None:
        report = tmp_path / "report.md"
        video_url = "https://v.redd.it/example"
        report.write_text(
            valid_report(video_url=video_url).replace(
                f"[watch video]({video_url})",
                f"[![Sampled frames]({video_url})]({video_url})",
            ),
            encoding="utf-8",
        )

        errors = validate_report(
            report,
            expected_title="# Builder Intelligence Report - 2026-08-02",
            allowed_image_urls=set(),
        )

        assert any("Markdown image embeds must use source image media URLs" in error for error in errors)

    def test_rejects_media_urls_formatted_as_inline_code(self, tmp_path: Path) -> None:
        report = tmp_path / "report.md"
        image_url = "https://i.redd.it/example.png"
        content = valid_report(image_url=image_url).replace(
            f"[![The review queue visibly contains one item]({image_url})]({image_url})",
            f"`{image_url}`",
            1,
        )
        report.write_text(content, encoding="utf-8")

        errors = validate_report(
            report,
            expected_title="# Builder Intelligence Report - 2026-08-02",
            allowed_media_urls={image_url},
        )

        assert errors == [
            (
                "media URLs must be Markdown links rather than inline code: "
                "https://i.redd.it/example.png"
            )
        ]

    def test_normalizes_reddit_citation_engagement(self, tmp_path: Path) -> None:
        report = tmp_path / "report.md"
        report.write_text(
            "[Source](https://www.reddit.com/r/SaaS/comments/build1/build1_title/)\n",
            encoding="utf-8",
        )

        messages = normalize_reddit_citations(
            report,
            engagement={"build1": (42, 7)},
        )

        assert "(42 points, 7 comments)" in report.read_text()
        assert messages == ["added engagement metadata after 1 Reddit citation(s)"]

    def test_normalizes_hackernews_citation_engagement(self, tmp_path: Path) -> None:
        report = tmp_path / "report.md"
        source = "https://news.ycombinator.com/item?id=123"
        report.write_text(f"[Source]({source})\n", encoding="utf-8")

        messages = normalize_hackernews_citations(
            report,
            engagement={"123": (42, 7)},
        )

        assert "(42 points, 7 comments)" in report.read_text()
        assert messages == ["added engagement metadata after 1 Hacker News citation(s)"]

    def test_replaces_loose_hackernews_engagement_without_duplication(
        self, tmp_path: Path
    ) -> None:
        report = tmp_path / "report.md"
        source = "https://news.ycombinator.com/item?id=123"
        report.write_text(
            f"([Source]({source}), 42 points, 7 comments)\n",
            encoding="utf-8",
        )

        messages = normalize_hackernews_citations(
            report,
            engagement={"123": (42, 7)},
        )

        assert report.read_text() == (
            f"([Source]({source}) (42 points, 7 comments))\n"
        )
        assert messages == ["added engagement metadata after 1 Hacker News citation(s)"]

    def test_normalizes_media_type_and_report_included(self, tmp_path: Path) -> None:
        review = tmp_path / "media-review.json"
        report = tmp_path / "report.md"
        entries = [
            {
                "post_id": "image",
                "url": "https://i.redd.it/demo.png",
                "media_type": "image",
                "asset_status": "attached",
            }
        ]
        review.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [
                        {
                            "post_id": "image",
                            "media_url": "https://i.redd.it/demo.png",
                            "media_type": "video",
                            "status": "inspected",
                            "observation": "The attached image visibly shows a product interface.",
                            "report_included": False,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        report.write_text("[View image](https://i.redd.it/demo.png)\n", encoding="utf-8")

        messages = normalize_media_review(review, expected_entries=entries, report_path=report)

        item = json.loads(review.read_text())["items"][0]
        assert item["media_type"] == "image"
        assert item["report_included"] is True
        assert len(messages) == 2
        assert validate_media_review(review, expected_entries=entries, report_path=report) == []

    def test_adds_missing_non_attached_media_review(self, tmp_path: Path) -> None:
        review = tmp_path / "media-review.json"
        review.write_text('{"version": 1, "items": []}\n', encoding="utf-8")
        entries = [
            {
                "post_id": "image",
                "url": "https://redd.it/example.png",
                "media_type": "image",
                "asset_status": "failed",
                "asset_error": "image host was not approved",
            }
        ]

        messages = normalize_media_review(review, expected_entries=entries)

        document = json.loads(review.read_text(encoding="utf-8"))
        assert document["items"] == [
            {
                "post_id": "image",
                "media_url": "https://redd.it/example.png",
                "media_type": "image",
                "status": "unavailable",
                "observation": "Visual asset was not attached: image host was not approved.",
                "report_included": False,
            }
        ]
        assert messages == ["added unavailable media review for non-attached post image"]

    def test_wraps_bare_media_review_item_list(self, tmp_path: Path) -> None:
        review = tmp_path / "media-review.json"
        review.write_text(
            json.dumps(
                [
                    {
                        "post_id": "image",
                        "media_url": "https://i.redd.it/image.png",
                        "media_type": "image",
                        "status": "inspected",
                        "observation": "The image visibly shows a complete product interface.",
                        "report_included": False,
                    }
                ]
            ),
            encoding="utf-8",
        )
        entries = [
            {
                "post_id": "image",
                "url": "https://i.redd.it/image.png",
                "media_type": "image",
                "asset_status": "attached",
            }
        ]

        messages = normalize_media_review(review, expected_entries=entries)

        document = json.loads(review.read_text(encoding="utf-8"))
        assert document["version"] == 1
        assert len(document["items"]) == 1
        assert messages == ["wrapped bare media review items in the version 1 document schema"]

    def test_cleans_unknown_duplicate_and_non_attached_statuses(self, tmp_path: Path) -> None:
        review = tmp_path / "media-review.json"
        review.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [
                        {
                            "post_id": "known",
                            "media_url": "https://i.redd.it/known.png",
                            "media_type": "image",
                            "status": "skipped-limit",
                            "observation": "The attachment limit prevented visual inspection.",
                            "report_included": False,
                        },
                        {
                            "post_id": "known",
                            "media_url": "https://i.redd.it/known.png",
                            "media_type": "image",
                            "status": "unavailable",
                            "observation": "The public image URL was not available for inspection.",
                            "report_included": False,
                        },
                        {
                            "post_id": "unknown",
                            "media_url": "https://i.redd.it/unknown.png",
                            "media_type": "image",
                            "status": "unavailable",
                            "observation": "This item is not present in the source manifest.",
                            "report_included": False,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        entries = [
            {
                "post_id": "known",
                "url": "https://i.redd.it/known.png",
                "media_type": "image",
                "asset_status": "skipped-limit",
            }
        ]

        messages = normalize_media_review(review, expected_entries=entries)

        items = json.loads(review.read_text(encoding="utf-8"))["items"]
        assert len(items) == 1
        assert items[0]["status"] == "unavailable"
        assert any("duplicate" in message for message in messages)
        assert any("unknown" in message for message in messages)

    def test_identifies_only_missing_or_invalid_attached_media_for_repair(
        self, tmp_path: Path
    ) -> None:
        review = tmp_path / "media-review.json"
        review.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [
                        {
                            "post_id": "complete",
                            "media_url": "https://i.redd.it/complete.png",
                            "media_type": "image",
                            "status": "inspected",
                            "observation": "The image visibly shows a complete product interface.",
                            "report_included": False,
                        },
                        {
                            "post_id": "unavailable",
                            "media_url": "https://i.redd.it/unavailable.png",
                            "media_type": "image",
                            "status": "unavailable",
                            "observation": "The attached image was incorrectly marked unavailable.",
                            "report_included": False,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        entries = [
            {
                "post_id": "complete",
                "url": "https://i.redd.it/complete.png",
                "media_type": "image",
                "asset_status": "attached",
            },
            {
                "post_id": "missing",
                "url": "https://i.redd.it/missing.png",
                "media_type": "image",
                "asset_status": "attached",
            },
            {
                "post_id": "unavailable",
                "url": "https://i.redd.it/unavailable.png",
                "media_type": "image",
                "asset_status": "attached",
            },
            {
                "post_id": "failed",
                "url": "https://redd.it/failed.png",
                "media_type": "image",
                "asset_status": "failed",
            },
        ]

        pending = _attached_media_repair_entries(review, expected_entries=entries)

        assert [entry["post_id"] for entry in pending] == ["missing", "unavailable"]

    def test_media_repair_batch_preserves_unrelated_items(self, tmp_path: Path) -> None:
        review = tmp_path / "media-review.json"
        retained = {
            "post_id": "retained",
            "media_url": "https://i.redd.it/retained.png",
            "media_type": "image",
            "status": "inspected",
            "observation": "The retained image shows a complete dashboard state.",
            "report_included": False,
        }
        repaired = {
            "post_id": "repaired",
            "media_url": "https://i.redd.it/repaired.png",
            "media_type": "image",
            "status": "inspected",
            "observation": "The repaired image shows a complete workflow state.",
            "report_included": False,
        }
        review.write_text(
            json.dumps({"version": 1, "items": [repaired]}),
            encoding="utf-8",
        )

        accepted = _merge_media_repair_batch(
            review,
            before_items=[retained],
            batch=[
                {
                    "post_id": "repaired",
                    "url": "https://i.redd.it/repaired.png",
                }
            ],
        )

        items = json.loads(review.read_text(encoding="utf-8"))["items"]
        assert accepted == 1
        assert [item["post_id"] for item in items] == ["retained", "repaired"]

    def test_normalizes_cited_attached_media_to_inspected(self, tmp_path: Path) -> None:
        image_url = "https://i.redd.it/cited.png"
        review = tmp_path / "media-review.json"
        report = tmp_path / "report.md"
        review.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [
                        {
                            "post_id": "cited",
                            "media_url": image_url,
                            "media_type": "image",
                            "status": "not-substantive",
                            "observation": (
                                "The cited screenshot contradicts the report's original claim."
                            ),
                            "report_included": False,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        report.write_text(f"[cited image]({image_url})\n", encoding="utf-8")

        messages = normalize_media_review(
            review,
            expected_entries=[
                {
                    "post_id": "cited",
                    "url": image_url,
                    "media_type": "image",
                    "asset_status": "attached",
                }
            ],
            report_path=report,
        )

        item = json.loads(review.read_text(encoding="utf-8"))["items"][0]
        assert item["report_included"] is True
        assert item["status"] == "inspected"
        assert any("cited attached media status" in message for message in messages)

    def test_selects_only_cited_attached_media_for_audit(self, tmp_path: Path) -> None:
        cited_url = "https://i.redd.it/cited.png"
        report = tmp_path / "report.md"
        report.write_text(
            valid_report(image_url=cited_url),
            encoding="utf-8",
        )
        entries = [
            {
                "post_id": "cited",
                "url": cited_url,
                "media_type": "image",
                "asset_status": "attached",
                "asset_paths": ["media-assets/cited.png"],
            },
            {
                "post_id": "uncited",
                "url": "https://i.redd.it/uncited.png",
                "media_type": "image",
                "asset_status": "attached",
                "asset_paths": ["media-assets/uncited.png"],
            },
            {
                "post_id": "url-only",
                "url": "https://i.redd.it/url-only.png",
                "media_type": "image",
                "asset_status": "url-only",
                "asset_paths": [],
            },
        ]

        selected = _cited_attached_media_entries(
            report,
            expected_entries=entries,
        )

        assert [entry["post_id"] for entry in selected] == ["cited"]

    def test_media_audit_merge_preserves_every_other_report_field(self) -> None:
        before = valid_report(image_url="https://i.redd.it/cited.png")
        after = (
            before.replace(
                "# Builder Intelligence Report - 2026-08-02",
                "# Rewritten title",
                1,
            )
            .replace(
                "**Evidence:** One signup is author-reported.",
                (
                    "**Evidence:** One signup is author-reported. "
                    "The cited screenshot shows a corrected interface state."
                ),
                1,
            )
            .replace(
                "The image shows one queued item",
                "The image shows the corrected interface state",
                1,
            )
            .replace(
                "**Interpretation:** This is a `Partial` match",
                "**Interpretation:** The audit rewrote unrelated synthesis",
                1,
            )
        )

        merged, accepted, compatible = _merge_media_audit_report(before, after)

        assert compatible is True
        assert accepted == 2
        assert merged.startswith("# Builder Intelligence Report - 2026-08-02")
        assert "The cited screenshot shows a corrected interface state." in merged
        assert "The image shows the corrected interface state" in merged
        assert "**Interpretation:** This is a `Partial` match" in merged
        assert "The audit rewrote unrelated synthesis" not in merged

    @patch("idea_pipeline.analyzer.builder.subprocess.run")
    def test_focused_media_audit_uses_sol_and_preserves_unrelated_items(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        directory = tmp_path / "artifacts"
        media_directory = directory / "media-assets"
        media_directory.mkdir(parents=True)
        cited_url = "https://i.redd.it/cited.png"
        uncited_url = "https://i.redd.it/uncited.png"
        cited_asset = media_directory / "cited.png"
        uncited_asset = media_directory / "uncited.png"
        cited_asset.write_bytes(b"cited")
        uncited_asset.write_bytes(b"uncited")
        candidate = directory / "report.md"
        candidate.write_text(valid_report(image_url=cited_url), encoding="utf-8")
        review = directory / "media-review.json"
        retained_item = {
            "post_id": "uncited",
            "media_url": uncited_url,
            "media_type": "image",
            "status": "inspected",
            "observation": "The uncited image shows an unrelated interface state.",
            "report_included": False,
        }
        review.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [
                        {
                            "post_id": "cited",
                            "media_url": cited_url,
                            "media_type": "image",
                            "status": "inspected",
                            "observation": "The cited image initially appeared to show one item.",
                            "report_included": True,
                        },
                        retained_item,
                    ],
                }
            ),
            encoding="utf-8",
        )
        entries = (
            {
                "post_id": "cited",
                "url": cited_url,
                "media_type": "image",
                "asset_status": "attached",
                "asset_paths": ["media-assets/cited.png"],
            },
            {
                "post_id": "uncited",
                "url": uncited_url,
                "media_type": "image",
                "asset_status": "attached",
                "asset_paths": ["media-assets/uncited.png"],
            },
        )
        prepared = PreparedReportArtifacts(
            directory=directory,
            topic_artifacts=(),
            metadata_path=directory / "metadata.json",
            media_manifest_path=directory / "media-manifest.json",
            link_manifest_path=directory / "external-links.json",
            media_assets_path=directory / "media-assets.json",
            media_review_path=review,
            instructions_path=directory / "instructions.md",
            candidate_path=candidate,
            total_posts=2,
        )
        media_assets = PreparedMediaAssets(
            manifest_path=prepared.media_assets_path,
            attachments=(cited_asset, uncited_asset),
            entries=entries,
        )

        def audit(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            assert command[command.index("--model") + 1] == MEDIA_AUDIT_MODEL
            assert command[command.index("--reasoning-effort") + 1] == MEDIA_AUDIT_EFFORT
            assert command[command.index("--max-ai-credits") + 1] == str(
                MEDIA_AUDIT_MAX_AI_CREDITS
            )
            assert command.count("--attachment") == 1
            assert command[command.index("--attachment") + 1] == str(cited_asset)
            usage_path = Path(command[command.index("--usage-output-file") + 1])
            usage_path.write_text(
                json.dumps(
                    {
                        "totalNanoAiu": 20_000_000_000,
                        "modelMetrics": {
                            MEDIA_AUDIT_MODEL: {
                                "requests": {"count": 3},
                                "totalNanoAiu": 20_000_000_000,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            content = candidate.read_text(encoding="utf-8")
            candidate.write_text(
                content.replace(
                    "# Builder Intelligence Report - 2026-08-02",
                    "# Rewritten title",
                    1,
                )
                .replace(
                    "**Evidence:** One signup is author-reported.",
                    (
                        "**Evidence:** One signup is author-reported. "
                        "The screenshot shows a corrected interface state."
                    ),
                    1,
                )
                .replace(
                    "The image shows one queued item",
                    "The image shows the corrected interface state",
                    1,
                ),
                encoding="utf-8",
            )
            review.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": [
                            {
                                "post_id": "cited",
                                "media_url": cited_url,
                                "media_type": "image",
                                "status": "inspected",
                                "observation": (
                                    "The cited image shows the corrected interface state."
                                ),
                                "report_included": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="audited", stderr="")

        mock_run.side_effect = audit

        result = audit_cited_media_evidence(
            prepared,
            media_assets,
            primary_model="gpt-6-luna",
        )

        assert result.status == "completed"
        assert result.attachment_count == 1
        assert candidate.read_text(encoding="utf-8").startswith(
            "# Builder Intelligence Report - 2026-08-02"
        )
        assert "The screenshot shows a corrected interface state." in candidate.read_text(
            encoding="utf-8"
        )
        items = json.loads(review.read_text(encoding="utf-8"))["items"]
        assert [item["post_id"] for item in items] == ["cited", "uncited"]
        assert items[1] == retained_item
        manifest = json.loads(
            (directory / "media-audit-manifest.json").read_text(encoding="utf-8")
        )
        assert [item["post_id"] for item in manifest["items"]] == ["cited"]
        assert any("outside Section 2" in message for message in result.messages)

        mock_run.reset_mock()
        skipped = audit_cited_media_evidence(
            prepared,
            media_assets,
            primary_model=MEDIA_AUDIT_MODEL,
        )
        assert skipped.status == "skipped-same-model"
        mock_run.assert_not_called()

    @patch("idea_pipeline.analyzer.builder.subprocess.run")
    def test_media_repair_retries_items_omitted_by_a_batch(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        directory = tmp_path / "artifacts"
        directory.mkdir()
        candidate = directory / "report.md"
        candidate.write_text("# report\n", encoding="utf-8")
        review = directory / "media-review.json"
        review.write_text('{"version": 1, "items": []}\n', encoding="utf-8")
        entries = tuple(
            {
                "post_id": post_id,
                "url": f"https://i.redd.it/{post_id}.png",
                "media_type": "image",
                "asset_status": "attached",
                "asset_paths": [f"{post_id}.png"],
            }
            for post_id in ("first", "second")
        )
        for entry in entries:
            (directory / entry["asset_paths"][0]).write_bytes(b"image")
        prepared = PreparedReportArtifacts(
            directory=directory,
            topic_artifacts=(),
            metadata_path=directory / "metadata.json",
            media_manifest_path=directory / "media-manifest.json",
            link_manifest_path=directory / "external-links.json",
            media_assets_path=directory / "media-assets.json",
            media_review_path=review,
            instructions_path=directory / "instructions.md",
            candidate_path=candidate,
            total_posts=2,
        )
        media_assets = PreparedMediaAssets(
            manifest_path=prepared.media_assets_path,
            attachments=tuple(directory / entry["asset_paths"][0] for entry in entries),
            entries=entries,
        )
        calls = 0

        def repair(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal calls
            entry = entries[calls]
            calls += 1
            review.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": [
                            {
                                "post_id": entry["post_id"],
                                "media_url": entry["url"],
                                "media_type": "image",
                                "status": "inspected",
                                "observation": (
                                    "The image shows a complete product workflow state."
                                ),
                                "report_included": False,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")

        mock_run.side_effect = repair

        messages = repair_attached_media_review(
            prepared,
            media_assets,
            model="gpt-5.4-mini",
        )

        items = json.loads(review.read_text(encoding="utf-8"))["items"]
        assert calls == 2
        assert [item["post_id"] for item in items] == ["first", "second"]
        assert any("retrying 1 attachment" in message for message in messages)

    def test_removes_ungrounded_external_links_without_dropping_text(self, tmp_path: Path) -> None:
        report = tmp_path / "report.md"
        report.write_text(
            "[Source](https://example.com/product) | "
            "[Repository](https://github.com/GyulyVGC/sniffnet) | "
            "Visible result: https://globcall.com/path. | "
            "[Media](https://i.redd.it/demo.png)\n",
            encoding="utf-8",
        )

        messages = normalize_report_links(
            report,
            allowed_external_urls={"https://example.com/product"},
            allowed_media_urls={"https://i.redd.it/demo.png"},
        )

        content = report.read_text()
        assert "[Source](https://example.com/product)" in content
        assert "[Media](https://i.redd.it/demo.png)" in content
        assert "Repository" in content
        assert "https://github.com/GyulyVGC/sniffnet" not in content
        assert "Visible result: globcall.com/path." in content
        assert "https://globcall.com/path" not in content
        assert len(messages) == 2

    def test_removes_ungrounded_reddit_media_but_preserves_post_links(self, tmp_path: Path) -> None:
        report = tmp_path / "report.md"
        report.write_text(
            "[Post](https://www.reddit.com/r/SaaS/comments/build1/title/) | "
            "[Invented image](https://i.redd.it/invented.png)\n",
            encoding="utf-8",
        )

        messages = normalize_report_links(
            report,
            allowed_external_urls=(),
            allowed_media_urls=(),
        )

        content = report.read_text(encoding="utf-8")
        assert "[Post](https://www.reddit.com/r/SaaS/comments/build1/title/)" in content
        assert "https://i.redd.it/invented.png" not in content
        assert messages == [
            "removed ungrounded external link from report: https://i.redd.it/invented.png"
        ]

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

        assert validate_media_review(review, expected_entries=entries, report_path=report) == []

        document = json.loads(review.read_text())
        document["items"][0]["report_included"] = False
        review.write_text(json.dumps(document), encoding="utf-8")
        errors = validate_media_review(review, expected_entries=entries, report_path=report)
        assert any("does not match report.md" in error for error in errors)

        document["items"][0]["status"] = "unavailable"
        document["items"].pop()
        review.write_text(json.dumps(document), encoding="utf-8")
        errors = validate_media_review(review, expected_entries=entries)

        assert any("cannot be marked unavailable" in error for error in errors)
        assert any("missing manifest item" in error for error in errors)
        assert any("attached video" in error for error in errors)


class TestAnalysisBoundary:
    @patch("idea_pipeline.analyzer.builder.subprocess.run")
    def test_valid_candidate_is_atomically_published(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = make_report_target(tmp_path)
        report = tmp_path / "reports" / "2026-08-02.md"
        job = AnalysisJob(target, report)
        candidate = tmp_path / "artifacts" / REPORT_ARTIFACT_NAME / "2026-08-02" / "report.md"

        def generate(*args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            command = args[0]
            assert isinstance(command, list)
            usage_path = Path(command[command.index("--usage-output-file") + 1])
            usage_path.write_text(
                json.dumps(
                    {
                        "totalNanoAiu": 1_500_000_000,
                        "totalApiDurationMs": 12_000,
                        "tokenDetails": {"output": {"tokenCount": 500}},
                        "modelMetrics": {
                            "gpt-6-luna": {
                                "requests": {"count": 2},
                                "totalNanoAiu": 1_500_000_000,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
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

        assert result.status == "published", result.message
        assert "(1 points, 0 comments)" in report.read_text(encoding="utf-8")
        assert all(path.read_bytes() == content for path, content in originals.items())
        assert mock_run.call_args.kwargs["cwd"] == candidate.parent
        assert mock_run.call_args.kwargs["check"] is False
        assert "REDDIT_COOKIES" not in mock_run.call_args.kwargs["env"]
        assert mock_run.call_args.kwargs["env"]["COPILOT_GITHUB_TOKEN"] == "copilot-token"
        generation = json.loads(
            (candidate.parent / "generation-metadata.json").read_text(encoding="utf-8")
        )
        assert generation["max_ai_credits"] == DEFAULT_MAX_AI_CREDITS
        assert generation["usage"]["ai_credits"] == 1.5
        assert generation["usage"]["cost_usd"] == 0.015

    @patch("idea_pipeline.analyzer.builder._download_image_asset")
    @patch("idea_pipeline.analyzer.builder.subprocess.run")
    def test_visual_audit_is_normalized_and_included_in_generation_telemetry(
        self,
        mock_run: MagicMock,
        mock_download: MagicMock,
        tmp_path: Path,
    ) -> None:
        target = make_report_target(tmp_path)
        image_url = "https://i.redd.it/demo.png"
        retained_image_url = "https://i.redd.it/retained.png"
        build2 = "https://www.reddit.com/r/SaaS/comments/build2/build2_title/"
        write_snapshot(
            target.snapshots[-1].path,
            [
                post(
                    "build1",
                    media_url=image_url,
                    selftext="A launched tool at https://example.com/product with one user.",
                ),
                post(
                    "build2",
                    media_url=retained_image_url,
                    selftext="A second screenshot of the same review workflow.",
                ),
            ],
        )
        report = tmp_path / "reports" / "2026-08-02.md"
        job = AnalysisJob(target, report)
        candidate = tmp_path / "artifacts" / REPORT_ARTIFACT_NAME / "2026-08-02" / "report.md"

        def download(_url: str, stem: Path) -> Path:
            asset = stem.with_suffix(".png")
            asset.write_bytes(b"image")
            return asset

        call_count = 0

        def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal call_count
            call_count += 1
            usage_path = Path(command[command.index("--usage-output-file") + 1])
            if call_count == 1:
                usage_path.write_text(
                    json.dumps(
                        {
                            "totalNanoAiu": 1_500_000_000,
                            "modelMetrics": {
                                "gpt-6-luna": {
                                    "requests": {"count": 2},
                                    "totalNanoAiu": 1_500_000_000,
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                content = (
                    valid_report(image_url=image_url)
                    .replace(
                        (
                            f"**Visual proof:** [![The review queue visibly contains one item]"
                            f"({image_url})]({image_url}) The image shows one queued item."
                        ),
                        (
                            f"**Visual proof:** [![The review queue visibly contains one item]"
                            f"({image_url})]({image_url}) The image shows one queued item; "
                            f"[![A retained workflow screenshot]({retained_image_url})]"
                            f"({retained_image_url}) shows the retained workflow."
                        ),
                        1,
                    )
                    .replace(
                        (
                            f"**Evidence:** The [image]({image_url}) and "
                            "[video](https://www.reddit.com/r/SaaS/comments/build1/"
                            "build1_title/) show the queue and sampled flow."
                        ),
                        "**Evidence:** The sampled flow adds no separate visual evidence.",
                    )
                    .replace(
                        (
                            "**Reddit source:** [idea](https://www.reddit.com/r/SaaS/"
                            "comments/idea1/idea1_title/) · [build](https://www.reddit.com/"
                            "r/SaaS/comments/build1/build1_title/)"
                        ),
                        (
                            "**Reddit source:** [idea](https://www.reddit.com/r/SaaS/"
                            "comments/idea1/idea1_title/) · [build](https://www.reddit.com/"
                            f"r/SaaS/comments/build1/build1_title/) · [second build]({build2})"
                        ),
                        1,
                    )
                )
                candidate.write_text(content, encoding="utf-8")
                (candidate.parent / "media-review.json").write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "items": [
                                {
                                    "post_id": "build1",
                                    "media_url": image_url,
                                    "media_type": "image",
                                    "status": "inspected",
                                    "observation": (
                                        "The image initially appeared to show one queued item."
                                    ),
                                    "report_included": True,
                                },
                                {
                                    "post_id": "build2",
                                    "media_url": retained_image_url,
                                    "media_type": "image",
                                    "status": "inspected",
                                    "observation": (
                                        "The retained image visibly shows the review workflow."
                                    ),
                                    "report_included": True,
                                },
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="generated", stderr="")

            assert command[command.index("--model") + 1] == MEDIA_AUDIT_MODEL
            usage_path.write_text(
                json.dumps(
                    {
                        "totalNanoAiu": 20_500_000_000,
                        "modelMetrics": {
                            MEDIA_AUDIT_MODEL: {
                                "requests": {"count": 4},
                                "totalNanoAiu": 20_500_000_000,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            content = candidate.read_text(encoding="utf-8")
            candidate.write_text(
                content.replace(
                    "**Evidence:** One signup is author-reported.",
                    (
                        "**Evidence:** One signup is author-reported; "
                        "the screenshot adds no decision-useful evidence."
                    ),
                    1,
                ).replace(
                    next(
                        line
                        for line in content.splitlines()
                        if line.startswith("**Visual proof:**")
                    ),
                    (
                        f"**Visual proof:** [![A retained workflow screenshot]"
                        f"({retained_image_url})]({retained_image_url}) "
                        "shows the retained workflow."
                    ),
                    1,
                ),
                encoding="utf-8",
            )
            (candidate.parent / "media-review.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": [
                            {
                                "post_id": "build1",
                                "media_url": image_url,
                                "media_type": "image",
                                "status": "not-substantive",
                                "observation": (
                                    "The image adds no decision-useful evidence beyond the post."
                                ),
                                "report_included": True,
                            },
                            {
                                "post_id": "build2",
                                "media_url": retained_image_url,
                                "media_type": "image",
                                "status": "inspected",
                                "observation": (
                                    "The retained image visibly shows the review workflow."
                                ),
                                "report_included": True,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="audited", stderr="")

        mock_download.side_effect = download
        mock_run.side_effect = run

        result = analyze_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "published", result.message
        assert call_count == 2
        review = json.loads((candidate.parent / "media-review.json").read_text())
        assert review["items"][0]["report_included"] is False
        assert review["items"][1]["report_included"] is True
        generation = json.loads(
            (candidate.parent / "generation-metadata.json").read_text(encoding="utf-8")
        )
        assert generation["media_audit"]["status"] == "completed"
        assert generation["media_audit"]["attachment_count"] == 2
        assert generation["usage"]["ai_credits"] == 22.0
        assert generation["usage"]["cost_usd"] == 0.22
        assert generation["usage"]["files"] == [
            "copilot-usage.json",
            "media-audit-usage.json",
        ]
        assert generation["total_duration_seconds"] >= generation["duration_seconds"]

    @patch("idea_pipeline.analyzer.builder._download_image_asset")
    @patch("idea_pipeline.analyzer.builder.subprocess.run")
    def test_visual_audit_failure_blocks_publication(
        self,
        mock_run: MagicMock,
        mock_download: MagicMock,
        tmp_path: Path,
    ) -> None:
        target = make_report_target(tmp_path)
        image_url = "https://i.redd.it/demo.png"
        write_snapshot(
            target.snapshots[-1].path,
            [
                post(
                    "build1",
                    media_url=image_url,
                    selftext="A launched tool at https://example.com/product with one user.",
                )
            ],
        )
        report = tmp_path / "reports" / "2026-08-02.md"
        report.parent.mkdir(parents=True)
        report.write_text("known-good report\n", encoding="utf-8")
        job = AnalysisJob(target, report)
        candidate = tmp_path / "artifacts" / REPORT_ARTIFACT_NAME / "2026-08-02" / "report.md"
        call_count = 0

        def download(_url: str, stem: Path) -> Path:
            asset = stem.with_suffix(".png")
            asset.write_bytes(b"image")
            return asset

        def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                candidate.write_text(
                    valid_report(image_url=image_url),
                    encoding="utf-8",
                )
                (candidate.parent / "media-review.json").write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "items": [
                                {
                                    "post_id": "build1",
                                    "media_url": image_url,
                                    "media_type": "image",
                                    "status": "inspected",
                                    "observation": (
                                        "The attached image visibly shows one queued item."
                                    ),
                                    "report_included": True,
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="generated", stderr="")
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="visual audit failed",
            )

        mock_download.side_effect = download
        mock_run.side_effect = run

        result = analyze_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "failed"
        assert "cited media audit exited with 1" in result.message
        assert report.read_text(encoding="utf-8") == "known-good report\n"
        validation = json.loads(
            (candidate.parent / "validation-errors.json").read_text(encoding="utf-8")
        )
        assert "cited media audit exited with 1" in validation["errors"][0]

    @patch("idea_pipeline.analyzer.builder.subprocess.run")
    def test_structure_drift_is_normalized_before_validation(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        target = make_report_target(tmp_path)
        report = tmp_path / "reports" / "2026-08-02.md"
        job = AnalysisJob(target, report)
        candidate = tmp_path / "artifacts" / REPORT_ARTIFACT_NAME / "2026-08-02" / "report.md"

        def generate(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            drifted = (
                valid_report()
                .replace("## 1. Executive Brief", "## 1. Bottom line", 1)
                .replace("### Key Highlights\n\n", "", 1)
                .replace("**Stage:** `Launched`", "Stage: Signal", 1)
                .replace("**Evidence:**", "Evidence:", 1)
            )
            candidate.write_text(drifted, encoding="utf-8")
            (candidate.parent / "media-review.json").write_text(
                '{"version": 1, "items": []}\n', encoding="utf-8"
            )
            return subprocess.CompletedProcess([], 0, stdout="done", stderr="")

        mock_run.side_effect = generate

        result = analyze_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "published"
        published = report.read_text(encoding="utf-8")
        assert "## 1. Executive Brief" in published
        assert "### Key Highlights" in published
        assert "**Stage:** `Unknown`" in published
        assert "**Evidence:**" in published

    @patch("idea_pipeline.analyzer.builder.subprocess.run")
    def test_valid_candidate_survives_a_copilot_cli_crash(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        target = make_report_target(tmp_path)
        report = tmp_path / "reports" / "2026-08-02.md"
        job = AnalysisJob(target, report)
        candidate = tmp_path / "artifacts" / REPORT_ARTIFACT_NAME / "2026-08-02" / "report.md"

        def generate(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            candidate.write_text(valid_report(), encoding="utf-8")
            (candidate.parent / "media-review.json").write_text(
                '{"version": 1, "items": []}\n', encoding="utf-8"
            )
            return subprocess.CompletedProcess(
                [],
                1,
                stdout="generated output",
                stderr="native binary was terminated by signal SIGSEGV",
            )

        mock_run.side_effect = generate

        result = analyze_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "published"
        assert "1 warning(s)" in result.message
        assert "(1 points, 0 comments)" in report.read_text(encoding="utf-8")
        warning_document = json.loads((candidate.parent / "validation-warnings.json").read_text())
        assert warning_document["warnings"] == [
            (
                "Copilot CLI exited with 1 after writing a candidate: "
                "native binary was terminated by signal SIGSEGV"
            )
        ]

    @patch("idea_pipeline.analyzer.builder.subprocess.run")
    def test_invalid_candidate_from_a_copilot_cli_crash_is_not_published(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        target = make_report_target(tmp_path)
        report = tmp_path / "reports" / "2026-08-02.md"
        job = AnalysisJob(target, report)
        candidate = tmp_path / "artifacts" / REPORT_ARTIFACT_NAME / "2026-08-02" / "report.md"

        def generate(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            candidate.write_text("# incomplete\n", encoding="utf-8")
            return subprocess.CompletedProcess([], 139, stdout="", stderr="Segmentation fault")

        mock_run.side_effect = generate

        result = analyze_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "failed"
        assert not report.exists()
        validation = json.loads((candidate.parent / "validation-errors.json").read_text())
        assert any("first line must be exactly" in error for error in validation["errors"])
        assert (
            "Copilot CLI exited with 139 after writing a candidate: Segmentation fault"
            in validation["warnings"]
        )

    @patch("idea_pipeline.analyzer.builder.subprocess.run")
    def test_copilot_cli_crash_without_a_candidate_still_fails_immediately(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        target = make_report_target(tmp_path)
        job = AnalysisJob(target, tmp_path / "reports" / "2026-08-02.md")
        mock_run.return_value = subprocess.CompletedProcess(
            [], 139, stdout="", stderr="Segmentation fault"
        )

        result = analyze_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "failed"
        assert result.message == "Copilot CLI exited with 139: Segmentation fault"

    @patch("idea_pipeline.analyzer.builder._download_image_asset")
    @patch("idea_pipeline.analyzer.builder.subprocess.run")
    def test_quality_warnings_and_media_normalization_do_not_block_publication(
        self,
        mock_run: MagicMock,
        mock_download: MagicMock,
        tmp_path: Path,
    ) -> None:
        target = make_report_target(tmp_path)
        image_url = "https://i.redd.it/demo.png"
        write_snapshot(
            target.snapshots[-1].path,
            [
                post(
                    "build1",
                    media_url=image_url,
                    selftext="A launched tool at https://example.com/product with one user.",
                )
            ],
        )
        report = tmp_path / "reports" / "2026-08-02.md"
        job = AnalysisJob(target, report)
        candidate = tmp_path / "artifacts" / REPORT_ARTIFACT_NAME / "2026-08-02" / "report.md"

        def download(_url: str, stem: Path) -> Path:
            asset = stem.with_suffix(".png")
            asset.write_bytes(b"image")
            return asset

        def generate(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            candidate.write_text(
                valid_report(image_url=image_url).replace(
                    "| Trigger and consequence |", "| Product function |"
                ),
                encoding="utf-8",
            )
            (candidate.parent / "media-review.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": [
                            {
                                "post_id": "build1",
                                "media_url": image_url,
                                "media_type": "video",
                                "status": "inspected",
                                "observation": (
                                    "The attached image visibly shows a product interface."
                                ),
                                "report_included": False,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess([], 0, stdout="done", stderr="")

        mock_download.side_effect = download
        mock_run.side_effect = generate

        result = analyze_job(
            job,
            artifacts_dir=tmp_path / "artifacts",
            model=MEDIA_AUDIT_MODEL,
        )

        assert result.status == "published"
        assert "warning(s)" in result.message
        assert report.is_file()
        review = json.loads((candidate.parent / "media-review.json").read_text())
        assert review["items"][0]["media_type"] == "image"
        assert review["items"][0]["report_included"] is True
        warning_document = json.loads((candidate.parent / "validation-warnings.json").read_text())
        assert len(warning_document["normalizations"]) == 3
        assert any("recommended schema" in warning for warning in warning_document["warnings"])

    @patch("idea_pipeline.analyzer.builder.subprocess.run")
    def test_ungrounded_links_are_sanitized_before_publication(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        target = make_report_target(tmp_path)
        write_snapshot(
            target.snapshots[-1].path,
            [
                post(
                    "build1",
                    selftext=(
                        "A launched workflow tool at http://example.com/product with one user."
                    ),
                )
            ],
        )
        report = tmp_path / "reports" / "2026-08-02.md"
        job = AnalysisJob(target, report)
        candidate = tmp_path / "artifacts" / REPORT_ARTIFACT_NAME / "2026-08-02" / "report.md"

        def generate(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            candidate.write_text(
                valid_report().replace(
                    "A linked project",
                    "An [unverified repository](https://github.com/example/invented) "
                    "and a linked project",
                ),
                encoding="utf-8",
            )
            (candidate.parent / "media-review.json").write_text(
                '{"version": 1, "items": []}\n', encoding="utf-8"
            )
            return subprocess.CompletedProcess([], 0, stdout="done", stderr="")

        mock_run.side_effect = generate

        result = analyze_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "published"
        published = report.read_text()
        assert "unverified repository" in published
        assert "https://github.com/example/invented" not in published
        assert "https://example.com/product" in published
        warning_document = json.loads((candidate.parent / "validation-warnings.json").read_text())
        assert any(
            "removed ungrounded external link" in message
            for message in warning_document["normalizations"]
        )

    @patch("idea_pipeline.analyzer.builder.subprocess.run")
    def test_invalid_candidate_never_replaces_existing_report(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        target = make_report_target(tmp_path)
        report = tmp_path / "reports" / "2026-08-02.md"
        report.parent.mkdir(parents=True)
        report.write_text("known-good report\n", encoding="utf-8")
        job = AnalysisJob(target, report)
        candidate = tmp_path / "artifacts" / REPORT_ARTIFACT_NAME / "2026-08-02" / "report.md"

        def generate(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            candidate.write_text("# incomplete\n", encoding="utf-8")
            return subprocess.CompletedProcess([], 0, stdout="done", stderr="")

        mock_run.side_effect = generate

        result = analyze_job(job, artifacts_dir=tmp_path / "artifacts")

        assert result.status == "failed"
        assert report.read_text(encoding="utf-8") == "known-good report\n"
        assert (candidate.parent / "validation-errors.json").exists()

    @patch("idea_pipeline.analyzer.builder.subprocess.run")
    def test_prepare_only_cli_builds_one_combined_sandbox(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        make_report_target(tmp_path)

        exit_code = main(
            [
                "--date",
                "2026-08-02",
                "--prepare-only",
                "--reddit-data-dir",
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
        assert "Generate exactly one evidence-grounded Builder Intelligence Report" in prompt
        assert "Do not write an opportunity ranking" in prompt
        assert not (root / "report.md").exists()
        assert not (tmp_path / "reports" / "2026-08-02.md").exists()
