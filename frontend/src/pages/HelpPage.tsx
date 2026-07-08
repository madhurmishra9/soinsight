import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import {
  BarChart2,
  BellOff,
  Download,
  Gauge,
  History,
  LayoutDashboard,
  Lightbulb,
  Settings2,
  TrendingUp,
} from 'lucide-react'

interface Section {
  id: string
  icon: React.ReactNode
  title: string
  body: React.ReactNode
}

const TOC: Array<{ id: string; label: string }> = [
  { id: 'overview', label: 'How SOInsight fits together' },
  { id: 'settings', label: 'Settings' },
  { id: 'fetch', label: 'Fetch Questions' },
  { id: 'analysis', label: 'Analysis' },
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'trends', label: 'Rising trends' },
  { id: 'metrics', label: 'Metrics' },
  { id: 'tag-suggestions', label: 'Tag suggestions' },
  { id: 'snoozed', label: 'Snoozed' },
  { id: 'runs', label: 'Run history' },
]

function Field({ name, children }: { name: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <code style={{ fontSize: 12 }}>{name}</code>
      <div style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 2 }}>{children}</div>
    </div>
  )
}

export function HelpPage() {
  const { hash } = useLocation()

  useEffect(() => {
    if (!hash) return
    const el = document.getElementById(hash.replace('#', ''))
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [hash])

  const sections: Section[] = [
    {
      id: 'overview',
      icon: <LayoutDashboard size={16} />,
      title: 'How SOInsight fits together',
      body: (
        <>
          <p style={{ marginBottom: 10 }}>
            SOInsight turns raw Stack Overflow Enterprise questions into recurring-problem
            insights, in four stages that map to the left-hand nav, top to bottom:
          </p>
          <ol style={{ paddingLeft: 20, lineHeight: 1.9 }}>
            <li><strong>Settings</strong> — point SOInsight at your SO instance and your local Ollama model.</li>
            <li><strong>Fetch Questions</strong> — pull tagged questions (and answers) from SO into the local database.</li>
            <li><strong>Analysis</strong> — classify each fetched question (main/sub category, noise or signal) and detect patterns.</li>
            <li>
              <strong>Insights</strong> (Dashboard, Rising trends, Metrics, Tag suggestions, Snoozed) — read-only views over
              the classified data: nothing here calls Stack Overflow or the model except the Dashboard's
              on-demand <em>remediation guide</em> generation.
            </li>
          </ol>
          <p style={{ marginTop: 10 }}>
            Nothing is computed live on page load beyond simple SQL aggregation — all category counts,
            patterns, and trends are derived from rows already stored by a Fetch + Analysis run. If a
            tab looks empty, the most common cause is: fetch hasn't run for that tag/window, or fetch
            ran but analysis hasn't classified the new questions yet.
          </p>
        </>
      ),
    },
    {
      id: 'settings',
      icon: <Settings2 size={16} />,
      title: 'Settings',
      body: (
        <>
          <Field name="Base URL">
            The root API URL of your SO Enterprise instance (e.g. <code>https://your-instance.stackenterprise.co/api/v3</code>).
            All question/tag/answer fetches are relative to this.
          </Field>
          <Field name="API Key">
            Sent as a bearer token on every SO API call. Stored in the backend's memory only for
            the life of the process — never written to disk or echoed back by any endpoint.
          </Field>
          <Field name="Team / Scope">
            Optional. Scopes every SO call to one Team/Community (Private Teams-style instances).
            Leave blank to query the instance-wide public scope.
          </Field>
          <Field name="Ollama URL / Classification model">
            Where the local LLM used for classification and remediation lives, and which installed
            model to call. The dropdown is populated from Ollama's own <code>/api/tags</code>.
          </Field>
          <Field name="Test connection">
            Saves the form, then probes the instance: reachability, detected API version, and the
            list of Team/Community scopes visible to your API key. Use this after any change —
            <strong> Fetch</strong> and tag validation both reuse this same stored config.
          </Field>
        </>
      ),
    },
    {
      id: 'fetch',
      icon: <Download size={16} />,
      title: 'Fetch Questions',
      body: (
        <>
          <Field name="Tags to ingest">
            One or more SO tags to pull questions for. Once a connection is established, use the
            searchable dropdown to pick from every tag your instance actually has (fetched live
            from SO) instead of typing blind — tags in red weren't found on the instance.
          </Field>
          <Field name="Time window / Custom date range">
            Bounds on question <em>creation date</em>. A custom range overrides the quick-pick window.
            This only controls what's pulled from SO — Dashboard/Trends windows are independent and
            applied afterwards against whatever is already stored locally.
          </Field>
          <Field name="Incremental fetch">
            When on, each tag resumes from its own <code>latest_question_at</code> watermark (the newest
            question already stored for that tag) instead of re-downloading the whole range — much
            faster for a daily refresh. Turn it off to force a full re-pull of the selected range
            (e.g. after changing the date range backward, or to pick up score/answer edits on
            existing questions).
          </Field>
          <Field name="Local data coverage">
            Read live from the database, not from SO: how many questions/answers you have stored
            per tag, the newest stored question's date ("data fetched till" — the incremental
            watermark), and when a fetch last completed for that tag.
          </Field>
        </>
      ),
    },
    {
      id: 'analysis',
      icon: <BarChart2 size={16} />,
      title: 'Analysis',
      body: (
        <>
          <p style={{ marginBottom: 10 }}>
            Analysis has two steps, run together: <strong>classification</strong> (the LLM assigns each
            question a main/sub category, or flags it as <code>Misuse / Noise</code>) and
            <strong> aggregation</strong> (grouping classified questions into patterns and category counts
            for the given tag/window).
          </p>
          <Field name="Tags / Time window / Custom date range">
            Same semantics as Fetch — this is the set of already-fetched questions to classify and
            aggregate, not a new SO pull.
          </Field>
          <Field name="Always incremental">
            Only questions with no existing classification row are sent to the model — previously
            classified questions load instantly from the database. To force re-classification (e.g.
            after changing taxonomy or the model), see <code>routers/analysis.py</code> — there is
            currently no UI toggle for it, by design, so re-runs stay cheap.
          </Field>
          <Field name="Results by tag">
            <em>Signal questions</em> = classified as a real category (not noise). <em>Noise</em> =
            classified as <code>Misuse / Noise</code> (spam, duplicates, off-topic — excluded from every
            downstream count). <em>Patterns</em> = distinct (main, sub) clusters in this run that met
            the pattern threshold (see Dashboard below).
          </Field>
        </>
      ),
    },
    {
      id: 'dashboard',
      icon: <LayoutDashboard size={16} />,
      title: 'Dashboard',
      body: (
        <>
          <p style={{ marginBottom: 10 }}>
            Everything on this tab is scoped to one product/tag and one window (quick-pick days, or
            a custom date range), computed from classifications already stored by Analysis.
          </p>
          <Field name="Signal questions">
            Count of non-noise classified questions for this tag in the window.
          </Field>
          <Field name="Noise volume">
            Count classified <code>Misuse / Noise</code> — shown for visibility but excluded from every
            other stat, chart, and pattern on this page. Click the tile to see which questions.
          </Field>
          <Field name="Patterns detected">
            Number of (main, sub) clusters with <strong>≥3 questions from ≥2 distinct users</strong> in
            the window. Below that threshold a cluster shows up in the category breakdown / top
            issues, but not as a formal "pattern" — the threshold exists so one person's repeated
            asking doesn't look like a team-wide trend.
          </Field>
          <Field name="Technical / Non-technical split">
            <strong>APPROXIMATE.</strong> A question counts as "technical" if it carries at least one tag
            from a fixed technical-tag list (python, docker, kubernetes, api, sql, …) — this is a
            heuristic over question tags, not a verified attribute of the asking user.
          </Field>
          <Field name="Category distribution / Sub-category frequency">
            Bar charts over the same category counts as the stats row — main-category totals, and
            the top 8 sub-categories by volume. Click any bar to drill into its source questions.
          </Field>
          <Field name="Top issues">
            The 5 (main, sub) pairs with the most questions in the window, regardless of whether
            they cleared the pattern threshold.
          </Field>
          <Field name="Key patterns">
            The qualifying clusters (see "Patterns detected" above), each with a suggested action
            drawn from a fixed recommendation matrix keyed by main category.
          </Field>
          <Field name="Remediation guide">
            On-demand, LLM-generated fix write-ups per qualifying pattern cluster — <em>the only thing
            on this page that calls the model live</em>. For each cluster the model is given the
            cluster's actual captured questions and answers and asked for a root cause, solution,
            and prevention plan, citing which question/answer IDs it used.
            <br /><br />
            <strong>Grounded</strong> means at least one cited question ID was verified to really belong to
            the cluster's captured sources — ungrounded output is discarded entirely and replaced
            with a neutral notice. Every question and answer referenced in a remediation card is
            tagged with its Stack Overflow ID (<code>[Q#12345]</code> / <code>[A#67890]</code>) so you can
            trace every claim back to the exact source post, both on this page and in
            JSON/Markdown/PDF exports.
            <br /><br />
            <strong>Update guide</strong> only (re)generates clusters whose source questions/answers changed
            since the last run (cheap). <strong>Regenerate all</strong> forces every cluster through the
            model again.
          </Field>
          <Field name="Recommended actions">
            Deduplicated list of suggested actions from Key patterns, ordered by how frequent the
            underlying pattern is.
          </Field>
          <Field name="Export (JSON / Markdown / PDF)">
            Full snapshot of everything above for the current product/window, including every
            source question, its stored answers, and the remediation guide if one has been
            generated.
          </Field>
        </>
      ),
    },
    {
      id: 'trends',
      icon: <TrendingUp size={16} />,
      title: 'Rising trends',
      body: (
        <>
          <p style={{ marginBottom: 10 }}>
            Flags categories whose question volume just spiked, by comparing a short recent window
            against a longer trailing baseline for the same tag.
          </p>
          <Field name="Recent (days)">
            The "is this spiking right now" window — counts every signal question classified into
            each (main, sub) category in the last N days.
          </Field>
          <Field name="Baseline (days)">
            The full look-back window used to establish a "normal" rate. Must be longer than
            Recent. The portion of the baseline <em>before</em> the recent window (i.e.
            <code>baseline_days − recent_days</code>) is the "trailing" period.
          </Field>
          <Field name="Trailing avg / window">
            The trailing period's question count, scaled to a window the same length as
            "Recent" — i.e. <code>(trailing_count / trailing_days) × recent_days</code> — so the
            comparison is apples-to-apples regardless of how long the baseline is.
          </Field>
          <Field name="Multiplier">
            <code>recent_count / max(trailing_avg, 1)</code>. A multiplier of 3.0× means the category is
            getting questions three times faster right now than its recent history predicts.
          </Field>
          <Field name="Threshold ×">
            The multiplier a category must reach or exceed to be flagged 🚨 Rising.
          </Field>
          <Field name="Min recent">
            A noise floor — a category needs at least this many recent questions to be flagged,
            even if its multiplier clears the threshold (protects against 0→1 questions registering
            as "infinite" growth).
          </Field>
          <Field name="The chart">
            Each pair of bars is one category: the tall bar is the actual recent count, the muted
            bar is the trailing baseline average. Categories outlined in red cleared the rising
            threshold — the further the red bar towers over its paired gray bar, the sharper the
            spike. Sorted by multiplier, rising categories first.
          </Field>
        </>
      ),
    },
    {
      id: 'metrics',
      icon: <Gauge size={16} />,
      title: 'Metrics',
      body: (
        <>
          <p style={{ marginBottom: 10 }}>
            An operational, pipeline-health view for a date range — separate from the Dashboard's
            per-product insight view. Use it to answer "did the pipeline actually process
            everything for this period?"
          </p>
          <Field name="Total questions">
            Every question stored locally with a creation date inside the selected range, across
            the tags you choose (or all known tags if none selected).
          </Field>
          <Field name="Answered / Unanswered">
            Answered = <code>answer_count &gt; 0</code> on the stored question record (from SO at fetch
            time). This reflects SO's own answer count, independent of whether we fetched and
            stored the answer bodies (see Settings → <code>FETCH_ANSWERS</code>).
          </Field>
          <Field name="Classified / analyzed">
            Questions in range that have at least one row in the classifications table — i.e. have
            actually been run through Analysis.
          </Field>
          <Field name="Skipped / missing">
            Questions fetched but with no classification row yet. Reasons shown are inferred: most
            commonly "not yet analysed" (Analysis hasn't been run for this window since the fetch),
            occasionally a classification call failed and was never retried.
          </Field>
          <Field name="People who asked">
            Count of distinct <code>author_id</code> values across the in-range questions — how many
            different people were asking, not how many questions were asked.
          </Field>
          <Field name="Got an accepted answer / Answered, still unresolved">
            <code>Got an accepted answer</code> counts questions where the asker (or a moderator)
            marked one answer accepted on Stack Overflow. <code>Answered, still unresolved</code> is
            the gap: questions with at least one answer but none accepted — often a better signal of
            unresolved pain than the raw "answered" count.
          </Field>
          <Field name="Avg. answers / question, Avg. views / question">
            Simple engagement/interest averages over every in-range question for the selected tags.
          </Field>
          <Field name="Time to first answer">
            Mean and median hours from a question's creation to its earliest captured answer.
            Only counts questions whose answer bodies were actually fetched (see Fetch →{' '}
            <code>FETCH_ANSWERS</code>) — a question with <code>answer_count &gt; 0</code> but no stored
            answer rows is excluded here rather than skewing the average.
          </Field>
          <Field name="Tag-wise breakdown">
            The same totals broken out per tag, so you can see which product's data is stale or
            incomplete at a glance.
          </Field>
          <Field name="Clicking a number">
            Every stat and every tag-row cell opens a side drawer listing the exact questions behind
            it (linked back to Stack Overflow with their <code>[Q#id]</code>), so a number is never a
            dead end — you can always see what it's counting.
          </Field>
        </>
      ),
    },
    {
      id: 'tag-suggestions',
      icon: <Lightbulb size={16} />,
      title: 'Tag suggestions',
      body: (
        <>
          <Field name="Tracked tags">
            Tags you already fetch/track — excluded from the suggestion list.
          </Field>
          <Field name="Instance volume">
            Total question count for that tag on the SO instance overall (from the cached tag
            index — primed whenever a tag is fetched or validated on the Fetch page).
          </Field>
          <Field name="Local count / Coverage">
            How many of that tag's questions you've already stored locally, and
            <code> local_count / instance_count</code> as a percentage — low coverage on a
            high-volume untracked tag is the strongest "you're probably missing signal here" flag.
          </Field>
        </>
      ),
    },
    {
      id: 'snoozed',
      icon: <BellOff size={16} />,
      title: 'Snoozed',
      body: (
        <>
          <p>
            Lets you acknowledge a recurring (product, main, sub) pattern so it stops cluttering
            the Dashboard's Key patterns / Top issues / Recommended actions until the snooze
            expires (or indefinitely, if you leave Days blank). The underlying questions and
            classifications are untouched — snoozing only hides the pattern's presentation, and it
            reappears automatically the moment the snooze lapses.
          </p>
        </>
      ),
    },
    {
      id: 'runs',
      icon: <History size={16} />,
      title: 'Run history',
      body: (
        <>
          <p>
            Every Fetch and Analysis run ever started, newest first: when it started/finished, its
            status, which products/window it covered, how long it took, and a summary counts
            object (e.g. patterns found, questions processed). Use this to confirm a scheduled or
            manual run actually completed before trusting the Dashboard/Metrics numbers for that
            period.
          </p>
        </>
      ),
    },
  ]

  return (
    <>
      <div className="page-header">
        <div className="page-title">User guide</div>
        <div className="page-subtitle">
          What every tab does, what its inputs mean, and how each number is calculated.
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-title" style={{ marginBottom: 8 }}>On this page</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {TOC.map((t) => (
            <a key={t.id} href={`#${t.id}`} className="btn btn-secondary btn-sm">
              {t.label}
            </a>
          ))}
        </div>
      </div>

      {sections.map((s) => (
        <div key={s.id} id={s.id} className="card" style={{ marginBottom: 16, scrollMarginTop: 16 }}>
          <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {s.icon} {s.title}
          </div>
          <div style={{ marginTop: 8, fontSize: 14, lineHeight: 1.6 }}>{s.body}</div>
        </div>
      ))}
    </>
  )
}
