const STEPS = [
  { title: "Sources", lines: ["Official company", "job boards"] },
  { title: "Eligibility", lines: ["Three-way, with the", "listing quoted"] },
  { title: "Ranking", lines: ["Heuristic fit,", "labelled as such"] },
  { title: "Tracker", lines: ["Applications and", "outcomes, on your PC"] },
];
const WIDTH = 190;
const GAP = 46;

/** How a job moves through Career Agent. Drawn with the page's own colours, so it follows light and dark. */
export function FlowDiagram() {
  const total = STEPS.length * WIDTH + (STEPS.length - 1) * GAP;
  return (
    <svg viewBox={`0 0 ${total} 120`} role="img" aria-labelledby="flow-title flow-desc" className="h-auto w-full min-w-[640px]">
      <title id="flow-title">How Career Agent works</title>
      <desc id="flow-desc">
        Jobs come from official company job boards, pass a three-way eligibility check that quotes the listing, are ranked by a
        heuristic fit score, and the ones you apply to are followed in a tracker on your own computer.
      </desc>
      <defs>
        <marker id="flow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
          <path d="M0 0L10 5L0 10z" fill="var(--color-muted)" />
        </marker>
      </defs>
      {STEPS.map((step, index) => {
        const x = index * (WIDTH + GAP);
        return (
          <g key={step.title}>
            <rect x={x + 1} y={11} width={WIDTH - 2} height={98} rx={10} fill="var(--color-surface)" stroke="var(--color-line)" strokeWidth={1.5} />
            <text x={x + WIDTH / 2} y={45} textAnchor="middle" fontSize={17} fontWeight={600} fill="var(--color-ink)">{step.title}</text>
            {step.lines.map((line, row) => (
              <text key={line} x={x + WIDTH / 2} y={70 + row * 19} textAnchor="middle" fontSize={13.5} fill="var(--color-muted)">{line}</text>
            ))}
            {index < STEPS.length - 1 && (
              <line x1={x + WIDTH + 5} y1={60} x2={x + WIDTH + GAP - 6} y2={60} stroke="var(--color-muted)" strokeWidth={1.5} markerEnd="url(#flow-arrow)" />
            )}
          </g>
        );
      })}
    </svg>
  );
}
