/**
 * Triage: which deals have been read, and what was decided about them.
 *
 * The workflow this supports is one pass per deal — open it, read it, mark it
 * shortlisted or rejected, move on. So the CONTROLS live in the detail panel,
 * where the reading happens, and only the RESULT shows on the feed: a
 * shortlisted deal is highlighted, a rejected one collapses to a single line.
 *
 * Rejected is collapsed rather than hidden. A hidden deal cannot be
 * un-rejected, and a decision made in two seconds on a headline deserves to
 * be reversible.
 *
 * ## Where this lives, and what that costs
 *
 * The site is static files on Cloudflare with no backend, so this is browser
 * storage. It survives reloads and deploys because an artifact keeps its
 * origin, but it is PER BROWSER: shortlist something on a laptop and a phone
 * will not know. Moving it to a real store later is small — the whole state
 * is a map of deal id to verdict — but it would need a Worker route, a KV
 * namespace and auth, since an open endpoint would let anyone edit the list.
 *
 * Every read is defensive. Storage throws in a private window, and a user who
 * clears site data gets an empty object rather than a crash.
 */
export type Verdict = "shortlist" | "reject";
export type ReviewMap = Record<number, Verdict>;

const KEY = "lr-review-v1";

export function loadReview(): ReviewMap {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return {};
    const out: ReviewMap = {};
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      const id = Number(k);
      if (Number.isFinite(id) && (v === "shortlist" || v === "reject")) out[id] = v;
    }
    return out;
  } catch {
    return {};
  }
}

export function saveReview(map: ReviewMap): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(map));
  } catch {
    /* private window, or storage full — the session still works, it just
       forgets. Better than refusing to mark anything. */
  }
}

/** Toggle: marking with the verdict a deal already has clears it. */
export function setVerdict(map: ReviewMap, id: number, verdict: Verdict | null): ReviewMap {
  const next = { ...map };
  if (verdict === null || next[id] === verdict) delete next[id];
  else next[id] = verdict;
  return next;
}

export type ReviewFilter = "all" | "shortlist" | "reject" | "unreviewed";

export function matchesReview(map: ReviewMap, id: number, filter: ReviewFilter): boolean {
  if (filter === "all") return true;
  if (filter === "unreviewed") return !map[id];
  return map[id] === filter;
}

export function reviewCounts(map: ReviewMap, ids: number[]) {
  let shortlist = 0;
  let reject = 0;
  for (const id of ids) {
    if (map[id] === "shortlist") shortlist += 1;
    else if (map[id] === "reject") reject += 1;
  }
  return { shortlist, reject, unreviewed: ids.length - shortlist - reject };
}
