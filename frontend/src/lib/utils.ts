import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatScore(value: unknown, suffix = ""): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return `0${suffix}`;
  const scaled = n <= 1 && suffix === "%" ? n * 100 : n;
  return `${scaled.toFixed(2)}${suffix}`;
}

export function formatDate(value: unknown): string {
  if (!value) return "n/a";
  const text = String(value);
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return text;
  return parsed.toLocaleString("fr-FR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function riskColor(level: string | undefined): string {
  const k = String(level || "").toLowerCase();
  if (k === "tres_eleve" || k === "très élevé" || k === "tres eleve")
    return "bg-red-200 text-red-900 border-red-300";
  if (k === "high" || k === "élevé" || k === "eleve")
    return "bg-red-100 text-red-700 border-red-200";
  if (k === "medium" || k === "moyen")
    return "bg-amber-100 text-amber-700 border-amber-200";
  if (k === "low" || k === "faible")
    return "bg-emerald-100 text-emerald-700 border-emerald-200";
  return "bg-slate-100 text-slate-600 border-slate-200";
}

/** Render a risk level token as a human-readable French label.
 *
 * Backend uses the legacy English vocabulary (``low``/``medium``/``high``)
 * plus the new ``tres_eleve`` floor introduced by the Moroccan constants
 * pipeline. The raw tokens leak in the UI as ``TRES_ELEVE`` which looks
 * like a debug string. This helper normalises everything.
 */
export function formatRiskLabel(level: string | undefined): string {
  const k = String(level || "").toLowerCase().trim();
  switch (k) {
    case "tres_eleve":
    case "tres eleve":
    case "très élevé":
      return "TRÈS ÉLEVÉ";
    case "high":
    case "eleve":
    case "élevé":
      return "ÉLEVÉ";
    case "medium":
    case "moyen":
      return "MOYEN";
    case "low":
    case "faible":
      return "FAIBLE";
    case "":
    case "unknown":
      return "INCONNU";
    default:
      return String(level || "").toUpperCase();
  }
}
