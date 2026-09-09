import { useCallback, useEffect, useMemo, useState } from "react";
import { data } from "./data/adapter";
import type { Deal, Feed, FeedDeal } from "./data/types";
import { type Band, bandOf, matchesQuery, minutesAgo, sortValue, timeLabel } from "./data/view";
import { Drawer } from "./components/Drawer";
import { FeedRow } from "./components/FeedRow";
import { Chip, GhostButton, OutlineButton, SegmentedSort, sectionLabel } from "./components/controls";
import "./app.css";

type Sort = "newest" | "largest" | "covered";
type Conf = "all" | "high" | "medium";
type Listed = "all" | "listed" | "unlisted";

const BANDS: { k: Band; label: string }[] = [
  { k: "all", label: "Any size" },
  { k: "big", label: "₹2,000cr and up" },
  { k: "mid", label: "₹500–2,000cr" },
  { k: "small", label: "Under ₹500cr" },
  { k: "unknown", label: "Undisclosed" },
];

const SORTS: { k: Sort; label: string }[] = [
  { k: "newest", label: "Newest" },
  { k: "largest", label: "Largest" },
  { k: "covered", label: "Most covered" },
];

const ALL_OUTLETS = "All outlets";

export default function App({
  defaultTheme = "dark",
  density = "compact",
  showProvenanceLegend = true,
}: {
  defaultTheme?: "dark" | "light";
  density?: "compact" | "comfortable";
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
  const [theme, setTheme] = useState<"dark" | "light">(defaultTheme);
  const [rail, setRail] = useState(false);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  const [openId, setOpenId] = useState<number | null>(null);
  const [openDeal, setOpenDeal] = useState<Deal | null>(null);

  // Fixed at load so a row's "12m" does not drift while the list is read.
  const [now, setNow] = useState(() => Date.now());

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

  // Deep link: ?deal=123 survives reload and is shareable, and works on any
  // static host without server-side routing.
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

  const openDrawer = (id: number) => {
    setOpenId(id);
    const url = new URL(window.location.href);
    url.searchParams.set("deal", String(id));
    window.history.replaceState({}, "", url);
  };
  const closeDrawer = () => {
    setOpenId(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("deal");
    window.history.replaceState({}, "", url);
  };

  const deals = feed?.deals ?? [];

  const outlets = useMemo(
    () => [ALL_OUTLETS, ...Array.from(new Set(deals.map((d) => d.primaryOutlet).filter(Boolean) as string[])).sort()],
    [deals],
  );

  const dealTypes = useMemo(
    () => Array.from(new Set(deals.map((d) => d.dealType))).sort((a, b) => a.localeCompare(b)),
    [deals],
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

  const rows = useMemo(() => {
    const out = deals.filter(matches);
    if (sort === "largest") {
      // Undisclosed sorts last rather than as zero — it is unknown, not small.
      return out.sort((a, b) => (sortValue(b) ?? -1) - (sortValue(a) ?? -1));
    }
    if (sort === "covered") return out.sort((a, b) => b.sourceCount - a.sourceCount);
    return out.sort((a, b) => minutesAgo(a.createdAt, now) - minutesAgo(b.createdAt, now));
  }, [deals, matches, sort, now]);

  const bandCounts = useMemo(() => {
    const c: Record<string, number> = { all: deals.length };
    for (const b of BANDS) if (b.k !== "all") c[b.k] = deals.filter((d) => bandOf(d) === b.k).length;
    return c;
  }, [deals]);

  const activeFilters =
    (q ? 1 : 0) + types.length + (band !== "all" ? 1 : 0) + (conf !== "all" ? 1 : 0) +
    (listed !== "all" ? 1 : 0) + (outlet !== ALL_OUTLETS ? 1 : 0);

  const clearAll = () => {
    setQ(""); setTypes([]); setBand("all"); setConf("all"); setListed("all"); setOutlet(ALL_OUTLETS);
  };

  const total = deals.length;
  const resultLine =
    activeFilters === 0
      ? `${total} deals · ~18 a day is normal · read to the bottom`
      : `${rows.length} of ${total} deals match`;

  return (
    <div className={`lr${theme === "light" ? " light" : ""}`} data-density={density}>
      {/* ── header ─────────────────────────────────────────────────── */}
      <div
        style={{
          position: "sticky", top: 0, zIndex: 30, background: "var(--lr-bg)",
          borderBottom: "1px solid var(--lr-line)", padding: "11.2px 16.8px",
          display: "flex", alignItems: "center", gap: 16.8, flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8.4, flex: "none" }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--lr-accent)", animation: "lrpulse 2.4s ease-in-out infinite" }} />
          <div style={{ fontSize: 15, fontWeight: 500, letterSpacing: "-.01em" }}>Liquidity Radar</div>
          <div style={{ fontSize: 11, color: "var(--lr-faint)", paddingLeft: 5.6, borderLeft: "1px solid var(--lr-line)" }}>
            deal flow, with its provenance attached
          </div>
        </div>

        <div
          style={{
            flex: 1, minWidth: 180, display: "flex", alignItems: "center", gap: 5.6,
            background: "var(--lr-surface)", border: "1px solid var(--lr-line)",
            borderRadius: 8, padding: "5.6px 8.4px",
          }}
        >
          <span style={{ fontSize: 12, color: "var(--lr-faint)" }}>⌕</span>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Company, person, or phrase"
            aria-label="Search deals"
            style={{ flex: 1, minWidth: 0, background: "transparent", border: 0, outline: "none", color: "var(--lr-text)", fontFamily: "inherit", fontSize: 13 }}
          />
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 5.6, flex: "none" }}>
          <GhostButton onClick={() => void load()}>Refresh</GhostButton>
          <GhostButton onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
            {theme === "dark" ? "Light" : "Dark"}
          </GhostButton>
        </div>
      </div>

      <div className="lr-shell">
        {/* ── filter rail ──────────────────────────────────────────── */}
        <div className="lr-rail" data-open={rail ? "open" : "closed"}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 11.2 }}>
            <div style={sectionLabel}>Filter</div>
            <button
              type="button"
              onClick={clearAll}
              style={{ background: "transparent", border: 0, color: "var(--lr-accent-text)", fontFamily: "inherit", fontSize: 11.5, cursor: "pointer", padding: 0 }}
            >
              Clear
            </button>
          </div>

          <div style={{ fontSize: 11, color: "var(--lr-faint)", marginBottom: 5.6 }}>Deal type</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 16.8 }}>
            {dealTypes.map((t) => (
              <Chip
                key={t}
                label={t}
                active={types.includes(t)}
                onClick={() => setTypes(types.includes(t) ? types.filter((x) => x !== t) : [...types, t])}
              />
            ))}
          </div>

          <div style={{ fontSize: 11, color: "var(--lr-faint)", marginBottom: 5.6 }}>Size</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2.8, marginBottom: 16.8 }}>
            {BANDS.map((b) => (
              <Chip key={b.k} label={b.label} count={bandCounts[b.k]} active={band === b.k} onClick={() => setBand(b.k)} wide />
            ))}
          </div>

          <div style={{ fontSize: 11, color: "var(--lr-faint)", marginBottom: 5.6 }}>Confidence</div>
          <div style={{ display: "flex", gap: 4, marginBottom: 16.8 }}>
            {(["all", "high", "medium"] as Conf[]).map((k) => (
              <Chip key={k} label={k === "all" ? "Any" : k[0].toUpperCase() + k.slice(1)} active={conf === k} onClick={() => setConf(k)} />
            ))}
          </div>

          <div style={{ fontSize: 11, color: "var(--lr-faint)", marginBottom: 5.6 }}>Listing</div>
          <div style={{ display: "flex", gap: 4, marginBottom: 16.8 }}>
            {(["all", "listed", "unlisted"] as Listed[]).map((k) => (
              <Chip key={k} label={k === "all" ? "Any" : k[0].toUpperCase() + k.slice(1)} active={listed === k} onClick={() => setListed(k)} />
            ))}
          </div>

          <div style={{ fontSize: 11, color: "var(--lr-faint)", marginBottom: 5.6 }}>Outlet</div>
          <select
            value={outlet}
            onChange={(e) => setOutlet(e.target.value)}
            aria-label="Filter by outlet"
            style={{ width: "100%", background: "var(--lr-surface)", color: "var(--lr-text)", border: "1px solid var(--lr-line)", borderRadius: 8, padding: 5.6, fontFamily: "inherit", fontSize: 12, outline: "none" }}
          >
            {outlets.map((o) => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>

          {showProvenanceLegend && (
            <div style={{ marginTop: 22.4, paddingTop: 11.2, borderTop: "1px solid var(--lr-line-soft)", fontSize: 11, color: "var(--lr-faint)", lineHeight: 1.6 }}>
              Size is shown as{" "}
              <span style={{ textDecoration: "underline", textDecorationThickness: "1.5px", textUnderlineOffset: "3px" }}>stated</span>,{" "}
              <span style={{ textDecoration: "underline", textDecorationStyle: "dashed", textDecorationThickness: "1.5px", textUnderlineOffset: "3px" }}>derived</span>,{" "}
              <span style={{ textDecoration: "underline", textDecorationStyle: "dotted", textDecorationThickness: "1.5px", textUnderlineOffset: "3px" }}>estimated</span>, or left undisclosed. The underline is the claim.
            </div>
          )}
        </div>

        {/* ── feed ─────────────────────────────────────────────────── */}
        <div style={{ minWidth: 0, padding: "16.8px 16.8px 56px" }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 11.2, flexWrap: "wrap", marginBottom: 11.2 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 11.2, minWidth: 0 }}>
              <GhostButton className="lr-filterbtn" onClick={() => setRail(!rail)}>
                {`Filters${activeFilters ? ` · ${activeFilters}` : ""}`}
              </GhostButton>
              <div style={{ fontSize: 12.5, color: "var(--lr-muted)" }}>{resultLine}</div>
            </div>
            <SegmentedSort options={SORTS} value={sort} onChange={setSort} />
          </div>

          {error && (
            <div style={{ padding: "56px 0", textAlign: "center" }}>
              <div style={{ fontSize: 16, marginBottom: 5.6 }}>Could not load the feed</div>
              <div style={{ fontSize: 13, color: "var(--lr-muted)", marginBottom: 16.8 }}>{error}</div>
              <OutlineButton onClick={() => void load()}>Try again</OutlineButton>
            </div>
          )}

          {loading && !error && (
            <div>
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} style={{ display: "flex", gap: 16.8, padding: "14px 0", borderBottom: "1px solid var(--lr-line-soft)", animation: "lrpulse 1.4s ease-in-out infinite" }}>
                  <div style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--lr-chip)", marginTop: 5, flex: "none" }} />
                  <div style={{ width: `${34 + ((i * 7) % 22)}%`, height: 13, background: "var(--lr-chip)", borderRadius: 4 }} />
                  <div style={{ width: 96, height: 13, background: "var(--lr-chip)", borderRadius: 4 }} />
                  <div style={{ width: `${18 + ((i * 5) % 16)}%`, height: 13, background: "var(--lr-chip)", borderRadius: 4 }} />
                </div>
              ))}
            </div>
          )}

          {!loading && !error && rows.length === 0 && (
            <div style={{ padding: "56px 0", textAlign: "center" }}>
              <div style={{ fontSize: 16, marginBottom: 5.6 }}>Nothing matches those filters</div>
              <div style={{ fontSize: 13, color: "var(--lr-muted)", marginBottom: 16.8 }}>
                {total} deals in the feed. Widen the size band or clear the outlet.
              </div>
              <OutlineButton onClick={clearAll}>Clear filters</OutlineButton>
            </div>
          )}

          {!loading && !error && rows.length > 0 && (
            <div>
              <div className="lr-head">
                <div />
                <div>Company</div>
                <div>Type</div>
                <div style={{ textAlign: "right" }}>Size</div>
                <div>Summary</div>
                <div style={{ textAlign: "right" }}>Seen</div>
              </div>
              {rows.map((d) => (
                <FeedRow
                  key={d.id}
                  deal={d}
                  now={now}
                  updates={d.updates.map((u) => ({
                    time: u.attachedAt ? timeLabel(u.attachedAt, now) : "—",
                    headline: u.title,
                  }))}
                  expanded={!!expanded[d.id]}
                  onToggle={() => setExpanded({ ...expanded, [d.id]: !expanded[d.id] })}
                  onOpen={() => openDrawer(d.id)}
                />
              ))}
              <div style={{ padding: "22.4px 0", textAlign: "center", fontSize: 12, color: "var(--lr-faint)" }}>
                End of the feed{feed?.feedWindowDays ? ` · last ${feed.feedWindowDays} days` : ""} · next sweep within 15 min
              </div>
            </div>
          )}
        </div>
      </div>

      {openDeal && <Drawer deal={openDeal} onClose={closeDrawer} now={now} />}
    </div>
  );
}
