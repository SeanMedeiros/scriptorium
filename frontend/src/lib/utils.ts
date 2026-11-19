import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function parseLanguages(input: string): string[] {
  return input
    .split(/[\s,;]+/)
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

export function formatPeriod(start: number | null, end: number | null): string | null {
  if (start == null && end == null) return null;
  if (start != null && end != null) return start === end ? `${start}` : `${start}–${end}`;
  return start != null ? `${start}–` : `–${end}`;
}
