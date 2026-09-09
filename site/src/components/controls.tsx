/** The shared controls: chips, ghost/outline buttons, the segmented sort. */
import type { CSSProperties, ReactNode } from "react";

export const ghostBtn: CSSProperties = {
  fontSize: 12,
  padding: "5px 9px",
  borderRadius: 8,
  border: "1px solid var(--lr-line)",
  background: "transparent",
  color: "var(--lr-muted)",
  fontFamily: "inherit",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

export const outlineBtn: CSSProperties = {
  ...ghostBtn,
  fontSize: 12.5,
  padding: "6px 11px",
  color: "var(--lr-accent-text)",
  borderColor: "var(--lr-accent)",
};

function hoverable(base: CSSProperties) {
  return {
    style: base,
    onMouseEnter: (e: React.MouseEvent<HTMLElement>) => {
      e.currentTarget.style.background = "var(--lr-accent-soft)";
    },
    onMouseLeave: (e: React.MouseEvent<HTMLElement>) => {
      e.currentTarget.style.background = "transparent";
    },
  };
}

export function GhostButton({
  children,
  onClick,
  className,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
  title?: string;
}) {
  return (
    <button type="button" onClick={onClick} className={className} title={title} {...hoverable(ghostBtn)}>
      {children}
    </button>
  );
}

export function OutlineButton({
  children,
  onClick,
  style,
}: {
  children: ReactNode;
  onClick?: () => void;
  style?: CSSProperties;
}) {
  const h = hoverable({ ...outlineBtn, ...style });
  return (
    <button type="button" onClick={onClick} {...h}>
      {children}
    </button>
  );
}

/** The one shared chip. `wide` is the full-width rail variant. */
export function Chip({
  label,
  active,
  onClick,
  wide,
  count,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  wide?: boolean;
  count?: number;
}) {
  const style: CSSProperties = {
    fontSize: 11.5,
    padding: wide ? "4.5px 8px" : "3px 7px",
    borderRadius: 6,
    border: `1px solid ${active ? "var(--lr-accent)" : "var(--lr-line)"}`,
    background: active ? "var(--lr-accent-soft)" : "transparent",
    color: active ? "var(--lr-accent-text)" : "var(--lr-muted)",
    fontFamily: "inherit",
    cursor: "pointer",
    width: wide ? "100%" : undefined,
    textAlign: wide ? "left" : "center",
    display: wide ? "block" : undefined,
  };
  return (
    <button type="button" onClick={onClick} style={style}>
      {label}
      {count !== undefined && (
        <span style={{ float: "right", color: "var(--lr-faint)", fontVariantNumeric: "tabular-nums" }}>
          {count}
        </span>
      )}
    </button>
  );
}

export function SegmentedSort<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { k: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        gap: 2.8,
        background: "var(--lr-surface)",
        border: "1px solid var(--lr-line)",
        borderRadius: 8,
        padding: 2.8,
      }}
    >
      {options.map((o) => (
        <button
          key={o.k}
          type="button"
          onClick={() => onChange(o.k)}
          style={{
            fontSize: 11.5,
            padding: "4px 9px",
            borderRadius: 6,
            border: 0,
            cursor: "pointer",
            fontFamily: "inherit",
            background: value === o.k ? "var(--lr-accent-soft)" : "transparent",
            color: value === o.k ? "var(--lr-accent-text)" : "var(--lr-muted)",
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export const railLabel: CSSProperties = {
  fontSize: 11,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "var(--lr-faint)",
  marginBottom: 5.6,
};

export const sectionLabel: CSSProperties = {
  fontSize: 11,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "var(--lr-faint)",
};
