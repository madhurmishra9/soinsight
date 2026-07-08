# Why SOInsight — Value, Purpose, and What It Provides

This document answers the questions a stakeholder asks before adopting a tool:
**What is it? Why do we need it? What does it give us? And does Stack Overflow
Enterprise already do this for us?**

It is a value/business-case overview. For *how* it runs see the
[README](../README.md) and [User Guide](USER_GUIDE.md); for *why it's automated*
see [BENEFITS.md](BENEFITS.md).

---

## 1. What SOInsight is, in one paragraph

SOInsight is an internal intelligence layer on top of your **Stack Overflow
Enterprise** instance. It ingests tagged questions, uses a **local LLM** to
classify each one into a fixed taxonomy of *pain-point categories* (Product,
Documentation, Operational, Awareness, Technical, Security/Compliance,
Adoption/Migration, Misuse/Noise), detects **recurring patterns** across users,
and renders per-product insights, drill-downs, and exportable reports. It runs
**fully locally** — no hosted LLM, no telemetry, question content never leaves
your machine.

In one line: **Stack Overflow tells you *what* was asked; SOInsight tells you
*what kind of problem* it is and *which problems keep recurring*.**

---

## 2. Why we need it — the problem it solves

A busy SO Enterprise instance accumulates thousands of questions. Buried in them
is the signal product and platform teams actually want:

- *What categories of problems are users hitting?* (a docs gap vs. a real
  product bug vs. an operational/access issue)
- *Which problems recur* across multiple users, rather than one-off questions?
- *Which product is generating the most friction*, and of what type?

Reading that signal by hand does not scale. The native tooling on SO is built to
measure **engagement** (how active the community is, how fast questions get
answered, which tags are popular) — not to **categorize the substance** of
questions or surface recurring themes. So teams are left manually skimming tags
and titles, which is slow, subjective, and inconsistent between people.

SOInsight automates that reading: it turns a pile of raw questions into a
structured, repeatable map of *types of pain* and *recurring patterns* — the
input a roadmap or docs-investment decision actually needs.

---

## 3. What it provides — capabilities

| Capability | What it does |
|---|---|
| **Semantic classification** | Every question is assigned exactly one main + one sub-category from a fixed 8-category taxonomy by a local LLM — so questions are grouped by *type of problem*, not just by tag |
| **Pattern detection** | Surfaces a cluster as a "pattern" only at **≥ 3 questions from ≥ 2 distinct users** — a deliberate threshold so you see recurring issues, not noise or one-offs |
| **Per-product dashboards** | Category / sub-category charts, top issues, key patterns, noise volume, and a technical/non-technical split, scoped per product tag and time window |
| **Drill-downs** | Every bar, row, and card opens the underlying questions with links back to your SO instance — the analysis is always traceable to source |
| **Exportable reports** | One-click **Markdown** or **JSON** reports (questions + links, top issues, key patterns, noise) for sharing or archiving |
| **Scheduled refresh** | A built-in scheduler keeps the analysis current on a cadence with no manual fetch |
| **Runtime model choice** | Switch the classification model from the Settings page to trade speed for accuracy; existing classifications are kept |

---

## 4. What you get out of it — outcomes

- **A repeatable, objective map of user pain** per product, instead of a
  subjective manual skim that differs by who did it.
- **Evidence for roadmap and docs decisions** — "Documentation gaps are the #1
  recurring pattern for *cloudsql* this quarter" is a defensible, drill-down-able
  claim, not a hunch.
- **Early detection of recurring issues** the moment they cross the pattern
  threshold, rather than after they've generated a flood of tickets.
- **Shareable artifacts** (Markdown/JSON reports) that drop straight into a
  planning doc, ticket, or review.
- **Consistency over time** — the same taxonomy and threshold are applied on every
  run, so trends are comparable week over week.

---

## 5. Does Stack Overflow Enterprise already do this?

Short answer: **partly the inputs, not the analysis.** SO Enterprise has strong
native features, but they answer *different questions*. SOInsight is built to fill
the gap they leave.

> Note: exact native feature names and availability vary by SO Enterprise version
> and tier. The distinction below is about *category of capability*, which is
> stable, not about a specific menu item.

| Need | Native Stack Overflow Enterprise | SOInsight |
|---|---|---|
| Browse/search questions, tags | ✅ Core feature | Uses it as the data source (API v3) |
| Engagement analytics (questions asked/answered, page views, response time, top tags, top users) | ✅ Native Insights / analytics dashboards | Not its job — complementary |
| Tag health, unanswered questions, SME identification | ✅ Native | Not its job — complementary |
| **Classify each question into a *pain-point* taxonomy** (docs gap vs. product bug vs. operational issue …) | ❌ Not provided | ✅ Core feature (local LLM, fixed taxonomy) |
| **Detect *recurring patterns* across users** with an explicit threshold | ❌ Not provided | ✅ Core feature (≥3 Qs / ≥2 users) |
| **Per-product "type of friction" reports** for roadmap/docs decisions | ❌ Not provided | ✅ Core feature (Markdown/JSON exports) |
| Fully local, no-telemetry analysis of question *content* | N/A (SaaS analytics) | ✅ By design |

**The distinction that matters:** SO's native analytics measure the *health and
activity of the community* — volume, speed, participation, popular tags. They do
**not** read the *meaning* of questions to tell you what *kind* of problem each
one represents, or which problems *keep recurring* across users. That semantic,
thematic layer is exactly what SOInsight adds. It does not replace SO's analytics;
it sits next to them and answers a question they were never designed to answer.

---

## 6. Other benefits

- **Fully local / privacy-preserving.** Classification and embeddings run on local
  Ollama; question content never leaves the machine, there is no hosted LLM and no
  telemetry, and the only outbound call is read-only HTTPS to *your* SO instance.
  Secrets live in a git-ignored `.env`, are never logged, never returned by an API
  response, and never embedded in exports. (See [SECURITY.md](SECURITY.md).)
- **No per-token cost, no vendor lock-in for inference.** Running the LLM locally
  means analyzing a large backlog has no usage bill, and you can swap models
  freely.
- **Taxonomy discipline.** The classifier is constrained to a fixed enum; invalid
  output retries once then falls back to Misuse/Noise. You get clean, comparable
  categories instead of free-form labels that drift.
- **Honest thresholds.** Below the pattern threshold the dashboard *explains why*
  and lists emerging signals — it never invents a pattern or lowers the bar to
  manufacture a finding.
- **Read-only and non-destructive.** Recommendations are text only; the tool never
  writes to Confluence, Backstage, Jira, or ServiceNow. It is safe to point at a
  production SO instance.
- **Cheap to keep current.** Incremental fetch and skip-already-classified
  analysis make refreshes take seconds, so the insight stays fresh without cost or
  effort (see [BENEFITS.md](BENEFITS.md)).
- **Low operational footprint.** One command to set up, one local process to run,
  one SQLite file for data.

---

## 7. What it is *not* (honest limitations)

- **Not a replacement for SO's native analytics.** Use SO Insights for engagement
  and content-health metrics; use SOInsight for pain-point categorization and
  pattern detection. They are complementary.
- **Classification is LLM-based, so not perfect.** Accuracy depends on the model;
  the bundled eval harness reports per-category precision/recall/F1 so you can
  measure and tune it (few-shot examples or a larger model).
- **The technical/non-technical split is a tag heuristic**, explicitly labelled
  APPROXIMATE in the UI — treat it as a rough cut, not ground truth.
- **It analyzes questions, not answers or full thread resolution** — it maps the
  *demand* side (what users struggle with), not whether each thread was resolved.

---

## 8. In summary

| Question | Answer |
|---|---|
| **What is it?** | A local intelligence layer that classifies SO Enterprise questions into pain-point categories and detects recurring patterns |
| **Why do we need it?** | To turn thousands of raw questions into a structured, repeatable map of *what kind of problem* users hit and *which recur* — something manual skimming and native analytics don't provide |
| **What does it give?** | Per-product dashboards, traceable drill-downs, recurring-pattern detection, and shareable Markdown/JSON reports |
| **Does SO already do this?** | SO provides the data and engagement/health analytics; it does **not** do semantic pain-point classification or cross-user pattern detection — that gap is what SOInsight fills |
| **Other benefits?** | Fully local, no telemetry, no per-token cost, read-only/non-destructive, disciplined taxonomy, cheap to keep fresh |

---

## See also

- [README](../README.md) — quick start and full feature reference
- [BENEFITS.md](BENEFITS.md) — why the pipeline is automated (launcher, scheduler, incremental runs)
- [User Guide](USER_GUIDE.md) — first-time setup, daily use, every tab, exports
- [Architecture](ARCHITECTURE.md) — components, data flow, taxonomy, tables
- [Security](SECURITY.md) — data handling, secrets, network posture
