// A time relative to now, such as "5 minutes ago", in the largest whole unit, from the browser's own
// Intl.RelativeTimeFormat.
const rtf = new Intl.RelativeTimeFormat([], { numeric: "auto" })
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 86_400],
  ["month", 30 * 86_400],
  ["day", 86_400],
  ["hour", 3600],
  ["minute", 60],
  ["second", 1],
]

export function relative(iso: string, now: number): string {
  const seconds = (Date.parse(iso) - now) / 1000
  for (const [unit, size] of UNITS) if (Math.abs(seconds) >= size || unit === "second") return rtf.format(Math.round(seconds / size), unit)
  return ""
}
