export function BrandMark({ className = "size-7" }: { className?: string }) {
  // A rubricated initial "S" on a ruled leaf — original mark.
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <rect x="1.5" y="1.5" width="29" height="29" rx="4" fill="#23395b" />
      <path d="M8 23.5h16M8 26.5h11" stroke="#f7f5f0" strokeOpacity=".45" strokeWidth="1.2" />
      <path
        d="M20.6 8.4c-1-1-2.5-1.5-4.3-1.5-2.8 0-4.6 1.5-4.6 3.6 0 4.4 8.9 2.7 8.9 6.9 0 2.2-2 3.7-4.9 3.7-2 0-3.7-.7-4.8-2"
        fill="none"
        stroke="#f7f5f0"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function Wordmark() {
  return (
    <span className="flex items-center gap-2.5">
      <BrandMark />
      <span className="font-serif text-[19px] font-semibold tracking-[0.02em] text-ink">Scriptorium</span>
    </span>
  );
}
