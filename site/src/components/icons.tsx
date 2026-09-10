/**
 * Lucide icons, inlined. The design ships four and specifies Lucide for any
 * icon added later — stroke-width 2, 15–17px.
 */
type Props = { size?: number; className?: string };

const base = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  "aria-hidden": true,
};

export const Search = ({ size = 16 }: Props) => (
  <svg width={size} height={size} {...base}>
    <circle cx="11" cy="11" r="8" />
    <path d="m21 21-4.3-4.3" />
  </svg>
);

export const Refresh = ({ size = 16 }: Props) => (
  <svg width={size} height={size} {...base}>
    <path d="M3 12a9 9 0 0 1 15.5-6.2L21 8" />
    <path d="M21 3v5h-5" />
    <path d="M21 12a9 9 0 0 1-15.5 6.2L3 16" />
    <path d="M3 21v-5h5" />
  </svg>
);

export const ArrowUp = ({ size = 17 }: Props) => (
  <svg width={size} height={size} {...base}>
    <path d="M12 19V5" />
    <path d="m5 12 7-7 7 7" />
  </svg>
);

export const X = ({ size = 15 }: Props) => (
  <svg width={size} height={size} {...base}>
    <path d="M18 6 6 18" />
    <path d="m6 6 12 12" />
  </svg>
);
