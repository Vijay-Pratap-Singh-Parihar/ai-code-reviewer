/** Formatting for the Usage & Budget page. */

export function formatUsd(value: number): string {
  // Small local or cheap-model spend still shows as more than "$0.00".
  if (value > 0 && value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  if (value >= 10_000) return `${Math.round(value / 1000)}k`;
  if (value >= 1000) return `${(value / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return String(value);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-11" → "Nov 2026". */
export function formatMonth(month: string): string {
  const [year, m] = month.split("-");
  return `${MONTHS[Number(m) - 1]} ${year}`;
}

/** "2026-11-05" (or an ISO timestamp) → "5 Nov", read in UTC like the API's months. */
export function formatDay(day: string): string {
  const date = new Date(day.length === 10 ? `${day}T00:00:00Z` : day);
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}`;
}

export function reviewsLabel(count: number): string {
  return `${count.toLocaleString("en-US")} ${count === 1 ? "review" : "reviews"}`;
}
