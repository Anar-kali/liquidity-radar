import { useCallback, useEffect, useMemo, useState } from "react";
import { data } from "./data/adapter";
import type { Deal, Feed, FeedDeal } from "./data/types";
import {
  type Band,
  amtStyle,
  amountLabel,
  bandOf,
  dateLine,
  matchesQuery,
  minutesAgo,
  provenanceOf,
  sortValue,
  standfirst,
  timeLabel,
} from "./data/view";
import { DealCell } from "./components/DealCell";
import { HeroCell } from "./components/HeroCell";
import { Panel } from "./components/Panel";
import { BANDS, type Conf, FilterGroups, type Listed, chipStyle } from "./components/Filters";
import { ArrowUp, Refresh, Search, X } from "./components/icons";
import "./app.css";

type Sort = "newest" | "largest" | "covered";

const SORTS: { k: Sort; label: string }[] = [
  { k: "newest", label: "Newest" },
  { k: "largest", label: "Largest" },
  { k: "covered", label: "Most covered" },
];

const ALL_OUTLETS = "All outlets";

const microLabel: React.CSSProperties = {
  fontSize: 10.5,
  fontWeight: 600,
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "var(--lr-faint)",
};

export default function App({
  defaultTheme = "light",
  heroCard = true,
  showProvenanceLegend = true,
}: {
  defaultTheme?: "light" | "dark";
  heroCard?: boolean;
  showProvenanceLegend?: boolean;
}) {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [q, setQ] = useState("");
  const [types, setTypes] = useState<string[]>([]);
  const [band, setBand] = useState<Band>("all");
  const [conf, setConf] = useState<Conf>("all");
  const [listed, setListed] = useState<Listed>("all");
  const [outlet, setOutlet] = useState(ALL_OUTLETS);
  const [sort, setSort] = useState<Sort>("newest");
  const [theme, setTheme] = useState<"light" | "dark">(defaultTheme);
  const [sheet, setSheet] = useState<"filters" | null>(null);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  /* The archive is 578 deals — rendering all of them is a 50,000px page and
     ~6,000 DOM nodes. Render a screenful at a time instead. */
  const PAGE = 60;
  const [shown, setShown] = useState(PAGE);

  const [openId, setOpenId] = useState<number | null>(null);
  const [openDeal, setOpenDeal] = useState<Deal | null>(null);

  // Fixed at load so a cell's "12m" does not drift while the page is read.
  const [now, setNow] = useState(() => Date.now());

  /*
   * Two surfaces, one bundle. `/archive` is a real path rather than a query
   * param because it is a page, not a view state — the Worker's
   * not_found_handling is set to single-page-application, so the shell is
   * served for it, and Vite's dev server does the same.
   */
  const [view, setView] = useState<"today" | "archive">(() =>
    window.location.pathname.replace(/\/+$/, "").endsWith("/archive") ? "archive" : "today",
  );
  const go = (v: "today" | "archive") => {
    setView(v);
    clearAll();
    window.history.pushState({}, "", v === "archive" ? "/archive" : "/");
    window.scrollTo({ top: 0 });
  };
  useEffect(() => {
    const onPop = () =>
      setView(window.location.pathname.replace(/\/+$/, "").endsWith("/archive") ? "archive" : "today");
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const f = await data.getFeed();
      setFeed(f);
      setNow(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Deep link: ?deal=123 is shareable and needs no server-side routing.
  useEffect(() => {
    const id = Number(new URLSearchParams(window.location.search).get("deal"));
    if (id) setOpenId(id);
  }, []);

  useEffect(() => {
    if (openId === null) {
      setOpenDeal(null);
      return;
    }
    let live = true;
    data
      .getDeal(openId)
      .then((d) => live && setOpenDeal(d))
      .catch(() => live && setOpenId(null));
    return () => {
      live = false;
    };
  }, [openId]);

  const openPanel = (id: number) => {
    setOpenId(id);
    const url = new URL(window.location.href);
    url.searchParams.set("deal", String(id));
    window.history.replaceState({}, "", url);
  };
  const closePanel = () => {
    setOpenId(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("deal");
    window.history.replaceState({}, "", url);
  };

  const allDeals = feed?.deals ?? [];

  /*
   * The design is "Deals today" — a finite list you read to the bottom. The
   * export carries a 90-day window (619 deals), which under that headline
   * reads wrong and renders a 59-screen page.
   *
   * So the front page is the last 24 hours, and everything older lives at
   * /archive. Both surfaces share the cells, filters and panel; only the
   * heading, the hero and the digest differ, because "latest" and "most
   * covered today" are front-page ideas.
   */
  // Expressed in hours, and phrased as "24 hours" rather than "1 day" —
  // the label is written out because "the last 1 days" is what a naive
  // pluralisation gives you.
  const RECENT_HOURS = 24;
  const RECENT_LABEL = "the last 24 hours";
  const [recent, older] = useMemo(() => {
    const cut = new Date(now - RECENT_HOURS * 3600 * 1000).toISOString();
    const a: FeedDeal[] = [];
    const b: FeedDeal[] = [];
    for (const d of allDeals) ((d.createdAt ?? "") >= cut ? a : b).push(d);
    return [a, b];
  }, [allDeals, now]);

  const scope = view === "archive" ? older : recent;

  // Reset paging whenever the surface or the filters change.
  useEffect(() => setShown(PAGE), [view, q, types, band, conf, listed, outlet, sort]);

  // Chips and the outlet list come from the whole window, so filtering never
  // offers a value that today happens not to contain.
  const outlets = useMemo(
    () => [ALL_OUTLETS, ...Array.from(new Set(scope.map((d) => d.primaryOutlet).filter(Boolean) as string[])).sort()],
    [scope],
  );
  const dealTypes = useMemo(
    () => Array.from(new Set(scope.map((d) => d.dealType))).sort((a, b) => a.localeCompare(b)),
    [scope],
  );

  const matches = useCallback(
    (d: FeedDeal) =>
      matchesQuery(d, q) &&
      (types.length === 0 || types.includes(d.dealType)) &&
      (band === "all" || bandOf(d) === band) &&
      (conf === "all" || d.confidence === conf) &&
      (listed === "all" || (listed === "listed" ? d.listed : !d.listed)) &&
      (outlet === ALL_OUTLETS || d.primaryOutlet === outlet),
    [q, types, band, conf, listed, outlet],
  );

  const nFilters =
    (q ? 1 : 0) + types.length + (band !== "all" ? 1 : 0) + (conf !== "all" ? 1 : 0) +
    (listed !== "all" ? 1 : 0) + (outlet !== ALL_OUTLETS ? 1 : 0);
  const anyFilter = nFilters > 0;

  const rows = useMemo(() => {
    const out = scope.filter(matches);
    if (sort === "largest") {
      // Undisclosed sorts last rather than as zero — unknown, not small.
      return out.sort((a, b) => (sortValue(b) ?? -1) - (sortValue(a) ?? -1));
    }
    if (sort === "covered") return out.sort((a, b) => b.sourceCount - a.sourceCount);
    return out.sort((a, b) => minutesAgo(a.createdAt, now) - minutesAgo(b.createdAt, now));
  }, [scope, matches, sort, now]);

  // Counts describe what the size filter would actually return, so they
  // follow the same scope the list does.
  const bandCounts = useMemo(() => {
    const c: Record<string, number> = { all: scope.length };
    for (const b of BANDS) if (b.k !== "all") c[b.k] = scope.filter((d) => bandOf(d) === b.k).length;
    return c;
  }, [scope]);

  const clearAll = () => {
    setQ(""); setTypes([]); setBand("all"); setConf("all"); setListed("all"); setOutlet(ALL_OUTLETS);
  };

  // Derived page shape: hero is the first row, the body splits 4 / rest
  // around the digest, and the digest is the top 5 by coverage.
  const wantHero = view === "today" && heroCard && rows.length > 2;
  const hero = wantHero ? rows[0] : null;
  const body = wantHero ? rows.slice(1) : rows;
  const rowsA = body.slice(0, 4);
  // The archive has no hero or digest, so everything after the first four
  // cells is one grid, capped at `shown`.
  const rowsB = body.slice(4, view === "archive" ? shown : body.length);
  const more = view === "archive" ? Math.max(0, body.length - shown) : 0;
  const digest = useMemo(
    () => [...rows].sort((a, b) => b.sourceCount - a.sourceCount).slice(0, 5),
    [rows],
  );

  const leadLabel = sort === "largest" ? "Largest" : sort === "covered" ? "Most covered" : "Latest";
  const scopeName = view === "archive" ? "the archive" : RECENT_LABEL;
  const resultLine = anyFilter
    ? `${rows.length} of ${scope.length} in ${scopeName} match`
    : view === "archive"
      ? `${scope.length} earlier ${scope.length === 1 ? "deal" : "deals"}`
      : `${scope.length} ${scope.length === 1 ? "deal" : "deals"} in ${RECENT_LABEL} · ~18 a day is normal`;

  const filterState = {
    band, conf, listed, outlet, bandCounts, outlets,
    setBand, setConf, setListed, setOutlet,
  };

  const updatesFor = (d: FeedDeal) =>
    d.updates.map((u) => ({
      time: u.attachedAt ? timeLabel(u.attachedAt, now) : "—",
      headline: u.title,
    }));

  const cellProps = (d: FeedDeal) => ({
    key: d.id,
    deal: d,
    now,
    updates: updatesFor(d),
    expanded: !!expanded[d.id],
    onToggle: () => setExpanded({ ...expanded, [d.id]: !expanded[d.id] }),
    onOpen: () => openPanel(d.id),
  });

  return (
    <div className={`lr${theme === "dark" ? " dark" : ""}`}>
      {/* ── top bar ────────────────────────────────────────────────── */}
      <div className="lr-topbar">
        <div className="lr-bar">
          <div style={{ display: "flex", alignItems: "center", gap: 9, marginRight: "auto", minWidth: 0 }}>
            <div style={{ width: 10, height: 10, background: "var(--lr-accent)", flex: "none" }} />
            <div
              style={{
                fontFamily: "var(--font-heading)",
                fontWeight: 800,
                fontSize: 15,
                letterSpacing: "-0.01em",
                whiteSpace: "nowrap",
              }}
            >
              Liquidity Radar
            </div>
          </div>
          <button
            type="button"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            style={{
              minHeight: 44,
              padding: "0 10px",
              display: "inline-flex",
              alignItems: "center",
              fontFamily: "var(--font-heading)",
              fontWeight: 600,
              fontSize: 11,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              cursor: "pointer",
              border: "1px solid var(--lr-line-soft)",
              background: "transparent",
              color: "var(--lr-muted)",
            }}
          >
            {theme === "dark" ? "Light" : "Dark"}
          </button>
          <button
            type="button"
            onClick={() => void load()}
            aria-label="Refresh"
            style={{
              width: 44,
              height: 44,
              display: "grid",
              placeItems: "center",
              cursor: "pointer",
              border: "1px solid var(--lr-line-soft)",
              background: "transparent",
              color: "var(--lr-muted)",
            }}
          >
            <Refresh />
          </button>
        </div>
      </div>

      <div className="lr-wrap">
        {/* ── desktop rail ─────────────────────────────────────────── */}
        <aside className="lr-rail">
          <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                paddingBottom: 9,
                borderBottom: "2px solid var(--lr-line)",
              }}
            >
              <div
                style={{
                  fontFamily: "var(--font-heading)",
                  fontWeight: 800,
                  fontSize: 11,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  color: "var(--lr-text)",
                }}
              >
                Filters
              </div>
              {anyFilter && (
                <button
                  type="button"
                  onClick={clearAll}
                  style={{
                    marginLeft: "auto",
                    fontFamily: "inherit",
                    fontSize: 11,
                    letterSpacing: "0.06em",
                    textTransform: "uppercase",
                    cursor: "pointer",
                    border: 0,
                    background: "transparent",
                    color: "var(--lr-accent-text)",
                    padding: "2px 0",
                  }}
                >
                  Clear all
                </button>
              )}
            </div>
            <FilterGroups s={filterState} rail />
          </div>
        </aside>

        {/* ── feed ─────────────────────────────────────────────────── */}
        <div className="lr-main">
          <div
            style={{
              fontSize: 10.5,
              fontWeight: 600,
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              color: "var(--lr-accent-text)",
            }}
          >
            {view === "archive"
              ? `Everything older than ${RECENT_LABEL.replace("the last ", "")}`
              : feed
                ? dateLine(feed.generatedAt)
                : ""}
          </div>
          <h1 className="lr-h1">{view === "archive" ? "Earlier deals" : "Deals today"}</h1>
          <div
            className="lr-stand"
            style={{ marginTop: 12, fontSize: 14, lineHeight: 1.5, color: "var(--lr-muted)", textWrap: "pretty" }}
          >
            {scope.length ? standfirst(scope, view === "archive" ? "in the archive" : `in ${RECENT_LABEL}`) : " "}
          </div>

          <div style={{ marginTop: 18, position: "relative" }}>
            <input
              type="search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Company, person, entity"
              aria-label="Search deals"
              style={{
                width: "100%",
                minHeight: 46,
                padding: "0 14px 0 40px",
                font: "inherit",
                fontSize: 14,
                color: "var(--lr-text)",
                caretColor: "var(--lr-accent)",
                background: "var(--lr-inset)",
                border: "1px solid var(--lr-line-soft)",
                borderRadius: 0,
              }}
            />
            <div
              style={{
                position: "absolute",
                left: 14,
                top: 0,
                height: 46,
                display: "grid",
                placeItems: "center",
                color: "var(--lr-faint)",
                pointerEvents: "none",
              }}
            >
              <Search />
            </div>
          </div>

          <div className="lr-chips">
            <div>
              <button type="button" onClick={() => setTypes([])} style={chipStyle(types.length === 0)}>
                All
              </button>
              {dealTypes.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setTypes(types.includes(t) ? types.filter((x) => x !== t) : [...types, t])}
                  style={chipStyle(types.includes(t))}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          <div
            className="lr-sort"
            style={{
              marginTop: 12,
              display: "grid",
              gridTemplateColumns: "repeat(3,1fr)",
              border: "1px solid var(--lr-line-soft)",
            }}
          >
            {SORTS.map((o, i) => (
              <button
                key={o.k}
                type="button"
                onClick={() => setSort(o.k)}
                style={{
                  fontFamily: "var(--font-heading)",
                  fontWeight: sort === o.k ? 800 : 600,
                  fontSize: 11,
                  letterSpacing: "0.06em",
                  textTransform: "uppercase",
                  minHeight: 44,
                  cursor: "pointer",
                  border: 0,
                  borderLeft: i ? "1px solid var(--lr-line-soft)" : 0,
                  background: sort === o.k ? "var(--lr-accent)" : "transparent",
                  color: sort === o.k ? "var(--lr-on-accent)" : "var(--lr-muted)",
                }}
              >
                {o.label}
              </button>
            ))}
          </div>

          {/* Closed by a 2px rule — this is the head of the feed. */}
          <div
            style={{
              marginTop: 18,
              paddingBottom: 9,
              borderBottom: "2px solid var(--lr-line)",
              display: "flex",
              alignItems: "baseline",
              gap: 10,
            }}
          >
            <div style={{ ...microLabel, fontSize: 11.5, flex: 1, minWidth: 0 }}>{resultLine}</div>
            {anyFilter && (
              <button
                type="button"
                onClick={clearAll}
                style={{
                  fontFamily: "inherit",
                  fontSize: 11.5,
                  letterSpacing: "0.06em",
                  textTransform: "uppercase",
                  cursor: "pointer",
                  border: 0,
                  background: "transparent",
                  color: "var(--lr-accent-text)",
                  padding: "2px 0",
                }}
              >
                Clear
              </button>
            )}
          </div>

          {error && (
            <div style={{ marginTop: 28, paddingBottom: 28, borderBottom: "2px solid var(--lr-line)" }}>
              <div style={{ fontFamily: "var(--font-heading)", fontWeight: 800, fontSize: 21, letterSpacing: "-0.02em" }}>
                Could not load the feed.
              </div>
              <div style={{ marginTop: 10, fontSize: 13.5, color: "var(--lr-faint)" }}>{error}</div>
              <button
                type="button"
                onClick={() => void load()}
                style={{
                  marginTop: 18,
                  minHeight: 46,
                  padding: "0 18px",
                  fontFamily: "var(--font-heading)",
                  fontWeight: 800,
                  fontSize: 12,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  cursor: "pointer",
                  border: 0,
                  background: "var(--lr-accent)",
                  color: "var(--lr-on-accent)",
                }}
              >
                Try again
              </button>
            </div>
          )}

          {loading && !error && (
            <div className="lr-grid">
              {[0, 1, 2, 3, 4].map((i) => (
                <div
                  key={i}
                  className="lr-cell"
                  style={{ padding: "17px 16px", animation: "lrpulse 1.5s ease-in-out infinite" }}
                >
                  <div style={{ height: 11, width: "32%", background: "var(--lr-line-soft)" }} />
                  <div style={{ height: 17, width: `${55 + i * 7}%`, background: "var(--lr-line-soft)", marginTop: 13 }} />
                  <div style={{ height: 26, width: `${34 + (i % 3) * 12}%`, background: "var(--lr-line-soft)", marginTop: 13 }} />
                  <div style={{ height: 11, width: "88%", background: "var(--lr-line-soft)", marginTop: 15 }} />
                </div>
              ))}
            </div>
          )}

          {!loading && !error && rows.length === 0 && (
            <div style={{ marginTop: 28, paddingBottom: 28, borderBottom: "2px solid var(--lr-line)" }}>
              <div
                style={{
                  fontFamily: "var(--font-heading)",
                  fontWeight: 800,
                  fontSize: 21,
                  lineHeight: 1.15,
                  letterSpacing: "-0.02em",
                }}
              >
                Nothing matches that combination.
              </div>
              <div
                style={{
                  marginTop: 10,
                  fontSize: 13.5,
                  lineHeight: 1.5,
                  color: "var(--lr-faint)",
                  textWrap: "pretty",
                  maxWidth: "46ch",
                }}
              >
                Widen the size band or clear the type filters — {allDeals.length} deals are in the window.
              </div>
              <button
                type="button"
                onClick={clearAll}
                style={{
                  marginTop: 18,
                  minHeight: 46,
                  padding: "0 18px",
                  display: "inline-flex",
                  alignItems: "center",
                  fontFamily: "var(--font-heading)",
                  fontWeight: 800,
                  fontSize: 12,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  cursor: "pointer",
                  border: 0,
                  background: "var(--lr-accent)",
                  color: "var(--lr-on-accent)",
                }}
              >
                Clear all filters
              </button>
            </div>
          )}

          {!loading && !error && hero && (
            <HeroCell deal={hero} leadLabel={leadLabel} now={now} onOpen={() => openPanel(hero.id)} />
          )}

          {!loading && !error && showProvenanceLegend && rows.length > 0 && (
            <div style={{ marginTop: 20, padding: "15px 16px", border: "1px solid var(--lr-line-soft)" }}>
              <div
                style={{
                  fontFamily: "var(--font-heading)",
                  fontWeight: 800,
                  fontSize: 10.5,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  color: "var(--lr-text)",
                }}
              >
                How to read the numbers
              </div>
              <div
                style={{
                  marginTop: 13,
                  display: "grid",
                  gridTemplateColumns: "auto minmax(0,1fr)",
                  gap: "10px 14px",
                  alignItems: "baseline",
                  fontSize: 12.5,
                }}
              >
                <span style={{ ...amtStyle("stated", 12.5), textUnderlineOffset: "4px" }}>₹1,256cr</span>
                <span style={{ color: "var(--lr-muted)" }}>stated in a filing — quotable</span>
                <span style={{ ...amtStyle("derived", 12.5), textUnderlineOffset: "4px" }}>~₹706cr</span>
                <span style={{ color: "var(--lr-muted)" }}>we derived it — stake × market cap</span>
                <span style={{ ...amtStyle("estimate", 12.5), textUnderlineOffset: "4px" }}>₹500–2,000cr</span>
                <span style={{ color: "var(--lr-muted)" }}>estimated band — do not quote</span>
                <span style={{ fontStyle: "italic", color: "var(--lr-faint)" }}>undisclosed</span>
                <span style={{ color: "var(--lr-muted)" }}>no figure exists — unknown, not small</span>
              </div>
            </div>
          )}

          {!loading && !error && rowsA.length > 0 && (
            <div className="lr-grid" style={{ marginTop: 20 }}>
              {rowsA.map((d) => (
                <DealCell {...cellProps(d)} />
              ))}
            </div>
          )}

          {!loading && !error && view === "today" && rows.length >= 8 && (
            <div style={{ marginTop: 26, paddingTop: 14, borderTop: "2px solid var(--lr-line)" }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                <div
                  style={{
                    fontFamily: "var(--font-heading)",
                    fontWeight: 800,
                    fontSize: 11,
                    letterSpacing: "0.1em",
                    textTransform: "uppercase",
                    color: "var(--lr-text)",
                  }}
                >
                  Most covered today
                </div>
                <div style={{ ...microLabel, marginLeft: "auto" }}>By article count</div>
              </div>
              <div className="lr-digest" style={{ marginTop: 10 }}>
                {digest.map((d, i) => (
                  <div
                    key={d.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => openPanel(d.id)}
                    onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), openPanel(d.id))}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--lr-hover)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 14,
                      minHeight: 54,
                      padding: "8px 0",
                      borderBottom: "1px solid var(--lr-line-soft)",
                      cursor: "pointer",
                    }}
                  >
                    <div
                      style={{
                        width: 26,
                        flex: "none",
                        fontFamily: "var(--font-heading)",
                        fontWeight: 800,
                        fontSize: 21,
                        lineHeight: 1,
                        color: "var(--lr-accent)",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {i + 1}
                    </div>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div
                        style={{
                          fontSize: 13.5,
                          fontWeight: 600,
                          lineHeight: 1.3,
                          color: "var(--lr-text)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {d.company}
                      </div>
                      <div style={{ ...microLabel, marginTop: 3 }}>
                        {d.sourceCount} {d.sourceCount === 1 ? "article" : "articles"} · {d.dealType}
                      </div>
                    </div>
                    <div style={amtStyle(provenanceOf(d), 14)}>{amountLabel(d)}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {!loading && !error && rowsB.length > 0 && (
            <div className="lr-grid" style={{ marginTop: 26 }}>
              {rowsB.map((d) => (
                <DealCell {...cellProps(d)} />
              ))}
            </div>
          )}

          {!loading && !error && more > 0 && (
            <button
              type="button"
              onClick={() => setShown(shown + PAGE)}
              style={{
                marginTop: 20,
                width: "100%",
                minHeight: 52,
                fontFamily: "var(--font-heading)",
                fontWeight: 600,
                fontSize: 11,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                cursor: "pointer",
                border: "1px solid var(--lr-line-soft)",
                background: "transparent",
                color: "var(--lr-text)",
              }}
            >
              Show {Math.min(PAGE, more)} more · {more} remaining
            </button>
          )}

          {!loading && !error && rows.length > 0 && (
            <div
              style={{
                marginTop: 26,
                paddingTop: 14,
                borderTop: "2px solid var(--lr-line)",
                ...microLabel,
                fontSize: 11,
                letterSpacing: "0.08em",
              }}
            >
              {view === "archive" ? "End of the archive" : "End of the recent feed"}
              <button
                type="button"
                onClick={() => go(view === "archive" ? "today" : "archive")}
                style={{
                  marginLeft: 10,
                  fontFamily: "inherit",
                  fontSize: 11,
                  fontWeight: 600,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  cursor: "pointer",
                  border: 0,
                  background: "transparent",
                  color: "var(--lr-accent-text)",
                  padding: 0,
                }}
              >
                {view === "archive"
                  ? "← Back to recent"
                  : `${older.length} earlier deals →`}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── phone bottom bar ───────────────────────────────────────── */}
      <div className="lr-bottombar">
        <button
          type="button"
          onClick={() => setSheet("filters")}
          style={{
            flex: 1,
            minHeight: 56,
            display: "flex",
            alignItems: "center",
            padding: "0 18px",
            fontFamily: "var(--font-heading)",
            fontWeight: 800,
            fontSize: 12,
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            cursor: "pointer",
            border: 0,
            background: "transparent",
            color: "var(--lr-text)",
          }}
        >
          {nFilters ? `Filters · ${nFilters}` : "Filters"}
        </button>
        <button
          type="button"
          onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
          aria-label="Back to top"
          style={{
            width: 56,
            minHeight: 56,
            display: "grid",
            placeItems: "center",
            cursor: "pointer",
            border: 0,
            borderLeft: "2px solid var(--lr-line)",
            background: "transparent",
            color: "var(--lr-muted)",
          }}
        >
          <ArrowUp />
        </button>
      </div>

      {/* ── phone filter sheet ─────────────────────────────────────── */}
      {sheet === "filters" && (
        <>
          <div
            className="lr-scrim"
            onClick={() => setSheet(null)}
            style={{
              position: "fixed",
              inset: 0,
              zIndex: 40,
              background: "color-mix(in srgb, var(--color-neutral-900) 55%, transparent)",
            }}
          />
          <div className="lr-sheet">
            <div
              style={{
                position: "sticky",
                top: 0,
                zIndex: 2,
                background: "var(--lr-bg)",
                padding: "14px 16px",
                borderBottom: "2px solid var(--lr-line)",
                display: "flex",
                alignItems: "center",
                gap: 10,
              }}
            >
              <div style={{ fontFamily: "var(--font-heading)", fontWeight: 800, fontSize: 17, letterSpacing: "-0.01em" }}>
                Filters
              </div>
              <button
                type="button"
                onClick={clearAll}
                style={{
                  marginLeft: "auto",
                  minHeight: 40,
                  fontFamily: "inherit",
                  fontSize: 11,
                  fontWeight: 600,
                  letterSpacing: "0.06em",
                  textTransform: "uppercase",
                  cursor: "pointer",
                  border: 0,
                  background: "transparent",
                  color: "var(--lr-accent-text)",
                }}
              >
                Clear all
              </button>
              <button
                type="button"
                onClick={() => setSheet(null)}
                aria-label="Close"
                style={{
                  width: 40,
                  height: 40,
                  display: "grid",
                  placeItems: "center",
                  cursor: "pointer",
                  border: "1px solid var(--lr-line-soft)",
                  background: "transparent",
                  color: "var(--lr-muted)",
                }}
              >
                <X />
              </button>
            </div>
            <div style={{ padding: "20px 16px 110px", display: "flex", flexDirection: "column", gap: 24 }}>
              <FilterGroups s={filterState} />
            </div>
          </div>
        </>
      )}

      {openDeal && <Panel deal={openDeal} onClose={closePanel} now={now} />}
    </div>
  );
}
