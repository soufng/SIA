import { cn } from "@/lib/utils";

interface LogoProps {
  /** Pixel size of the visual mark (the square symbol). */
  size?: number;
  /** Show the SPM / CCM lockup text next to the mark. */
  withText?: boolean;
  /**
   * When true (default) the lockup text uses white-on-red navbar styling.
   * Set to false for use on light backgrounds (cards, hero, footer).
   */
  onDark?: boolean;
  className?: string;
}

export function Logo({
  size = 36,
  withText = true,
  onDark = true,
  className,
}: LogoProps) {
  return (
    <div className={cn("flex items-center gap-3", className)}>
      <span
        className={cn(
          "inline-flex items-center justify-center shrink-0 rounded-md overflow-hidden",
          onDark ? "bg-white p-1 shadow-sm" : "bg-transparent"
        )}
        style={{ width: size, height: size }}
      >
        <img
          src="/ccm-logo-mark.png"
          alt="Logo Centre Cinematographique Marocain"
          className="h-full w-full object-contain"
        />
      </span>
      {withText && (
        <div className="leading-tight min-w-0">
          <p
            className={cn(
              "text-base font-bold tracking-tight",
              onDark ? "text-white" : "text-ccm-ink"
            )}
          >
            SPM{" "}
            <span
              className={cn(
                "font-normal",
                onDark ? "opacity-80" : "text-slate-500"
              )}
            >
              / CCM
            </span>
          </p>
          <p
            className={cn(
              "text-[10px] uppercase tracking-[0.18em] truncate",
              onDark ? "text-white/70" : "text-slate-500"
            )}
          >
            Centre Cinematographique Marocain
          </p>
        </div>
      )}
    </div>
  );
}

/**
 * Full lockup with the official CCM logo + bilingual text. Use this for
 * hero sections / standalone branding moments where the whole mark fits.
 */
export function LogoLockup({
  width = 220,
  className,
}: {
  width?: number;
  className?: string;
}) {
  return (
    <img
      src="/ccm-logo.png"
      alt="Centre Cinematographique Marocain"
      style={{ width }}
      className={cn("h-auto", className)}
    />
  );
}
