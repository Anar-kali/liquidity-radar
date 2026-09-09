/**
 * Data access behind one interface, so swapping the static JSON for an HTTP
 * API later is a new adapter rather than a component rewrite.
 */
import type { Deal, Feed } from "./types";

export interface DataAdapter {
  getFeed(): Promise<Feed>;
  getDeal(id: number): Promise<Deal>;
}

/** Reads the files export_site.py writes into site/public/data. */
export class StaticJsonAdapter implements DataAdapter {
  constructor(private base = "/data") {}

  async getFeed(): Promise<Feed> {
    const res = await fetch(`${this.base}/deals.json`);
    if (!res.ok) throw new Error(`feed ${res.status}`);
    return res.json();
  }

  async getDeal(id: number): Promise<Deal> {
    const res = await fetch(`${this.base}/deals/${id}.json`);
    if (!res.ok) throw new Error(`deal ${id}: ${res.status}`);
    return res.json();
  }
}

export const data: DataAdapter = new StaticJsonAdapter();
