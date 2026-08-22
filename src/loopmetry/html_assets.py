"""Static assets for the self-contained HTML report."""

REPORT_CSS = r"""
    :root {
      --background: #f5f3ef;
      --surface: #ffffff;
      --surface-muted: #f8f7f4;
      --text: #1f2428;
      --muted: #667078;
      --line: #dedbd4;
      --accent: #c7662f;
      --accent-soft: #f1dfd3;
      --warning: #7d4a18;
      --radius: 18px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--background);
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--background); color: var(--text); }
    button { font: inherit; }
    .shell { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 36px 0 64px; }
    .hero {
      position: relative;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 24px;
      background: var(--surface);
      padding: clamp(28px, 5vw, 56px);
      box-shadow: 0 18px 50px rgba(44, 38, 31, 0.06);
    }
    .hero::after {
      content: "";
      position: absolute;
      width: 230px;
      height: 230px;
      border-radius: 50%;
      right: -75px;
      top: -90px;
      background: var(--accent-soft);
    }
    .hero__content { position: relative; z-index: 1; max-width: 820px; }
    .brand { display: flex; align-items: center; gap: 10px; margin-bottom: 54px; font-weight: 750; letter-spacing: -0.02em; }
    .brand__mark { width: 12px; height: 12px; border-radius: 50%; background: var(--accent); box-shadow: 17px 0 0 var(--accent-soft); margin-right: 17px; }
    .eyebrow { margin: 0 0 8px; color: var(--accent); font-size: 0.75rem; font-weight: 800; letter-spacing: 0.12em; text-transform: uppercase; }
    h1, h2, h3, p { margin-top: 0; }
    h1 { margin-bottom: 12px; font-size: clamp(2.1rem, 5vw, 4.2rem); line-height: 1.02; letter-spacing: -0.055em; overflow-wrap: anywhere; }
    h2 { margin-bottom: 20px; font-size: clamp(1.45rem, 3vw, 2.1rem); letter-spacing: -0.035em; }
    h3 { margin-bottom: 0; font-size: 1.25rem; letter-spacing: -0.025em; }
    .hero__lede { color: var(--muted); font-size: 1.06rem; line-height: 1.65; max-width: 720px; }
    .hero__meta { display: flex; flex-wrap: wrap; gap: 10px 18px; margin-top: 28px; color: var(--muted); font-size: 0.88rem; }
    .hero__meta code { color: var(--text); background: var(--surface-muted); border: 1px solid var(--line); border-radius: 7px; padding: 3px 7px; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 22px; }
    .toolbar button { border: 1px solid var(--line); background: var(--surface); color: var(--text); border-radius: 999px; padding: 9px 14px; cursor: pointer; }
    .toolbar button:hover { border-color: var(--accent); }
    .section { margin-top: 42px; }
    .section-heading { display: flex; justify-content: space-between; gap: 20px; align-items: end; }
    .section-heading p { color: var(--muted); max-width: 640px; line-height: 1.55; }
    .snapshot-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 10px; }
    .snapshot-card { border: 1px solid var(--line); border-radius: 14px; background: var(--surface); padding: 16px; min-height: 105px; display: flex; flex-direction: column; justify-content: space-between; }
    .snapshot-card span { color: var(--muted); font-size: 0.82rem; }
    .snapshot-card strong { font-size: 1.75rem; letter-spacing: -0.04em; }
    .metric-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
    .metric-card { border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); padding: 24px; box-shadow: 0 10px 30px rgba(44, 38, 31, 0.035); }
    .metric-card__header { display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; }
    .metric-score { display: flex; align-items: baseline; white-space: nowrap; }
    .metric-score strong { font-size: 2rem; letter-spacing: -0.055em; }
    .metric-score span { color: var(--muted); font-size: 0.76rem; }
    .progress { height: 9px; border-radius: 999px; overflow: hidden; background: #ece9e3; margin: 18px 0; }
    .progress span { display: block; height: 100%; border-radius: inherit; background: var(--accent); }
    .progress--compact { height: 6px; margin: 0; }
    .metric-summary { min-height: 72px; color: var(--muted); line-height: 1.6; }
    .confidence-row { display: grid; grid-template-columns: auto auto minmax(90px, 1fr); gap: 10px; align-items: center; color: var(--muted); font-size: 0.82rem; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); padding: 12px 0; }
    .confidence-row strong { color: var(--text); }
    .components { padding: 14px 0 4px; }
    .component { display: grid; grid-template-columns: minmax(120px, 1fr) minmax(100px, 1.1fr) 44px; gap: 12px; align-items: center; margin: 10px 0; font-size: 0.82rem; }
    .component__label { color: var(--muted); }
    .component__value { text-align: right; font-variant-numeric: tabular-nums; }
    .details-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 16px; }
    details { border: 1px solid var(--line); background: var(--surface-muted); border-radius: 12px; padding: 12px; }
    summary { cursor: pointer; display: flex; justify-content: space-between; gap: 12px; font-size: 0.84rem; font-weight: 700; }
    summary span { color: var(--muted); }
    .evidence-list, .gap-list { margin: 14px 0 0; padding-left: 18px; }
    .evidence-item, .gap-list li { margin: 12px 0; color: var(--muted); line-height: 1.5; }
    .evidence-item__meta { display: flex; flex-wrap: wrap; gap: 5px 9px; font-size: 0.7rem; }
    .evidence-item__meta code { color: var(--accent); }
    .evidence-item p { margin: 5px 0 0; color: var(--text); font-size: 0.82rem; }
    .signal-grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 0.8fr); gap: 18px; }
    .signal-card, .gap-card { border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); padding: 24px; }
    .signal-label { display: inline-flex; padding: 7px 10px; border-radius: 999px; background: var(--accent-soft); color: var(--warning); font-weight: 760; margin-bottom: 16px; }
    .signal-card p, .gap-card li { color: var(--muted); line-height: 1.6; }
    .gap-card ul { margin-bottom: 0; padding-left: 20px; }
    .empty-state { color: var(--muted); font-style: italic; }
    .footer { margin-top: 36px; border-top: 1px solid var(--line); padding-top: 20px; color: var(--muted); font-size: 0.78rem; line-height: 1.55; }
    @media (max-width: 980px) {
      .snapshot-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
      .metric-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 680px) {
      .shell { width: min(100% - 20px, 1180px); padding-top: 10px; }
      .hero { border-radius: 18px; }
      .brand { margin-bottom: 36px; }
      .snapshot-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .signal-grid, .details-grid { grid-template-columns: 1fr; }
      .component { grid-template-columns: 1fr 90px 38px; }
      .section-heading { display: block; }
    }
    @media print {
      body, :root { background: #fff; }
      .shell { width: 100%; padding: 0; }
      .hero, .metric-card, .snapshot-card, .signal-card, .gap-card { box-shadow: none; break-inside: avoid; }
      .toolbar { display: none; }
      details { break-inside: avoid; }
    }

"""
