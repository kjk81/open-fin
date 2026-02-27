import { useEffect, useRef, useState } from "react";
import type { MentionOption } from "../types";

interface Props {
  query: string;
  portfolioSymbols: string[];
  onSelect: (option: MentionOption) => void;
  onClose: () => void;
}

export function MentionPopover({ query, portfolioSymbols, onSelect, onClose }: Props) {
  const q = query.toLowerCase();
  const [activeIdx, setActiveIdx] = useState(0);

  const options: MentionOption[] = [];

  // Always offer @portfolio
  if ("portfolio".startsWith(q)) {
    options.push({ type: "portfolio", label: "portfolio — your positions", value: "portfolio" });
  }

  // Portfolio symbols as ticker shortcuts
  for (const sym of portfolioSymbols) {
    if (sym.toLowerCase().startsWith(q)) {
      options.push({ type: "ticker", label: sym, value: sym });
    }
  }

  // If query has chars and isn't matched by a portfolio symbol, offer lookup
  const queryUpper = query.toUpperCase();
  const alreadyListed = options.some((o) => o.value === queryUpper);
  if (queryUpper.length >= 1 && !alreadyListed && /^[A-Z]+$/.test(queryUpper)) {
    options.push({ type: "ticker", label: `${queryUpper} — look up ticker`, value: queryUpper });
  }

  // Clamp active index
  const clampedActive = Math.min(activeIdx, options.length - 1);

  const listRef = useRef<HTMLDivElement>(null);

  // Keyboard navigation
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIdx((i) => Math.min(i + 1, options.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIdx((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        if (options[clampedActive]) onSelect(options[clampedActive]);
      } else if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [options, clampedActive, onSelect, onClose]);

  if (options.length === 0) return null;

  return (
    <div className="mention-popover" ref={listRef}>
      {options.map((opt, i) => (
        <div
          key={opt.value}
          className={`mention-option${i === clampedActive ? " mention-option--active" : ""}`}
          onMouseDown={(e) => {
            e.preventDefault(); // prevent textarea blur
            onSelect(opt);
          }}
          onMouseEnter={() => setActiveIdx(i)}
        >
          <span className="mention-option-type">{opt.type === "portfolio" ? "●" : "$"}</span>
          {opt.label}
        </div>
      ))}
    </div>
  );
}
