import { REPOSITORY, RESULTS, sourceUrl } from "../../lib/results";
import { FlowDiagram } from "./FlowDiagram";

const SECTION = "mx-auto max-w-5xl px-4 py-12 sm:py-16";
const HEADING = "text-2xl font-semibold tracking-tight";
const LINK = "underline underline-offset-2 hover:text-accent";
const CARD = "rounded-lg border border-line bg-surface p-5";

const PROBLEMS = [
  { title: "Listings that are no longer open", text: "A posting can stay online long after the role is filled. Career Agent shows a job only when it was seen open recently or was posted recently, and says which." },
  { title: "Roles a fresher cannot get", text: "A search for fresher jobs returns many that ask for years of experience. Each listing is marked eligible, uncertain or excluded, with the line from the listing that decided it." },
  { title: "Scores that claim too much", text: "A match percentage looks like a probability and is not one. Every score here carries its level: a heuristic is called a heuristic." },
];

const PRINCIPLES = [
  { title: "Local-first", text: "It runs on your own computer. Your CV, applications and email tokens stay there." },
  { title: "No scraping of LinkedIn, Naukri or Indeed", text: "Jobs come from official company job boards, public feeds, and alert emails or pages you save yourself." },
  { title: "You approve every email", text: "An email is a draft until you approve it and then send it. Nothing is sent or applied for you." },
  { title: "Your CV never goes to a hosted model", text: "Neither the file nor any text taken from it. Nothing on a CV is invented." },
];

/** The public front page. Static: no data is fetched, no cookie is set and nothing is loaded from another site. */
export function Landing({ demoUrl }: { demoUrl?: string }) {
  return (
    <div className="landing min-h-screen bg-paper text-ink">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:rounded focus:bg-surface focus:px-3 focus:py-2">
        Skip to the content
      </a>
      <header className="border-b border-line bg-surface">
        <nav aria-label="Main" className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-3">
          <a href="/" className="text-base font-semibold tracking-tight">Career Agent</a>
          <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm text-muted">
            <li><a href="#how" className={LINK}>How it works</a></li>
            <li><a href="#results" className={LINK}>Results</a></li>
            <li><a href="#privacy" className={LINK}>Privacy</a></li>
            <li><a href={REPOSITORY} className={LINK}>GitHub</a></li>
          </ul>
        </nav>
      </header>

      <main id="main">
        <section aria-labelledby="hero-title" className={SECTION}>
          <h1 id="hero-title" className="max-w-3xl text-4xl font-semibold tracking-tight sm:text-5xl">Evidence-first job search for Indian freshers</h1>
          <p className="mt-5 max-w-2xl text-lg text-muted">
            Career Agent finds jobs on official company boards, checks whether a fresher can actually apply and quotes the line
            that says so, ranks what is left, and keeps track of your applications. It runs on your own computer.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            {demoUrl ? (
              <a href={demoUrl} className="rounded-md bg-accent px-5 py-2.5 text-base font-medium text-surface hover:opacity-90">Try the demo</a>
            ) : (
              <span aria-disabled="true" className="rounded-md border border-line bg-surface px-5 py-2.5 text-base font-medium text-muted">
                Try the demo: coming soon
              </span>
            )}
            <a href={REPOSITORY} className="rounded-md border border-line bg-surface px-5 py-2.5 text-base font-medium hover:bg-accent-soft">View the code on GitHub</a>
          </div>
          <p className="mt-3 text-sm text-muted">The demo will use synthetic applications and public job posts only.</p>
        </section>

        <section aria-labelledby="problem-title" className="border-y border-line bg-surface">
          <div className={SECTION}>
            <h2 id="problem-title" className={HEADING}>The problem</h2>
            <ul className="mt-6 grid gap-4 md:grid-cols-3">
              {PROBLEMS.map((problem) => (
                <li key={problem.title} className="rounded-lg border border-line bg-paper p-5">
                  <h3 className="font-semibold">{problem.title}</h3>
                  <p className="mt-2 text-sm text-muted">{problem.text}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section id="how" aria-labelledby="how-title" className={SECTION}>
          <h2 id="how-title" className={HEADING}>How it works</h2>
          <div className="mt-6 overflow-x-auto pb-2">
            <FlowDiagram />
          </div>
          <ol className="mt-6 grid gap-4 text-sm sm:grid-cols-2">
            <li className={CARD}><strong className="font-semibold">1. Sources.</strong> A daily index of official company job boards, plus job-alert emails and pages you save yourself.</li>
            <li className={CARD}><strong className="font-semibold">2. Eligibility.</strong> Eligible, uncertain or excluded. A job is excluded only when the listing says so, and the words are shown.</li>
            <li className={CARD}><strong className="font-semibold">3. Ranking.</strong> A deterministic fit score, shown as "Heuristic fit", never as a chance of getting the job.</li>
            <li className={CARD}><strong className="font-semibold">4. Tracker.</strong> A board for your applications with a timeline and undo, stored on your computer.</li>
          </ol>
        </section>

        <section id="results" aria-labelledby="results-title" className="border-y border-line bg-surface">
          <div className={SECTION}>
            <h2 id="results-title" className={HEADING}>Measured results</h2>
            <p className="mt-3 max-w-3xl text-sm text-muted">
              Every figure links to the file it comes from. All are level L0: measurements on one labelled batch of jobs for one
              candidate profile, or counts from one run. None is a calibrated probability or a claim about other people.
            </p>
            <ul className="mt-6 grid gap-4 md:grid-cols-2">
              {RESULTS.map((result) => (
                <li key={result.id} data-testid={`result-${result.id}`} className="rounded-lg border border-line bg-paper p-5">
                  <h3 className="flex flex-wrap items-baseline justify-between gap-2 font-semibold">
                    {result.title}
                    <span className="rounded border border-line px-1.5 py-0.5 text-xs font-medium text-muted" title="Claim level L0: a deterministic measurement">{result.level}</span>
                  </h3>
                  <p className="mt-2 text-sm">{result.text}</p>
                  <p className="mt-3 text-sm text-muted">
                    Source: <a href={sourceUrl(result)} className={LINK}>{result.source}</a>
                  </p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section id="privacy" aria-labelledby="privacy-title" className={SECTION}>
          <h2 id="privacy-title" className={HEADING}>Privacy and ethics</h2>
          <ul className="mt-6 grid gap-4 sm:grid-cols-2">
            {PRINCIPLES.map((principle) => (
              <li key={principle.title} className={CARD}>
                <h3 className="font-semibold">{principle.title}</h3>
                <p className="mt-2 text-sm text-muted">{principle.text}</p>
              </li>
            ))}
          </ul>
          <p className="mt-6 max-w-3xl text-sm text-muted">
            Risk flags on a listing are signals with their evidence, never an accusation. This page sets no cookie, runs no tracker and
            loads nothing from another site.
          </p>
        </section>
      </main>

      <footer className="border-t border-line bg-surface">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-6 text-sm text-muted">
          <p>Career Agent. Open source.</p>
          <ul className="flex flex-wrap gap-x-5 gap-y-1">
            <li><a href={REPOSITORY} className={LINK}>GitHub</a></li>
            <li><a href={`${REPOSITORY}/blob/main/PRIVACY.md`} className={LINK}>Privacy</a></li>
            <li><a href="/app" className={LINK}>Open the tracker (local install)</a></li>
          </ul>
        </div>
      </footer>
    </div>
  );
}
