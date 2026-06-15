import * as React from "react";
import { cn } from "@/lib/utils";

type Variant = "info" | "success" | "warning" | "error";

// ``info`` is meant for neutral notices ("nothing to report", help text,
// onboarding hints). It must not read as a warning — previously it used the
// CCM red palette which made benign messages look like errors. Slate/blue
// keeps the tone informational.
const styles: Record<Variant, string> = {
  info: "bg-slate-50 text-slate-700 border-slate-200",
  success: "bg-emerald-50 text-emerald-800 border-emerald-200",
  warning: "bg-amber-50 text-amber-800 border-amber-200",
  error: "bg-red-50 text-red-800 border-red-200",
};

export function Alert({
  variant = "info",
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { variant?: Variant }) {
  return (
    <div
      role="alert"
      className={cn("rounded-md border px-4 py-3 text-sm", styles[variant], className)}
      {...props}
    />
  );
}
