import * as React from "react";
import { cn } from "@/lib/utils";

type Variant = "info" | "success" | "warning" | "error";

const styles: Record<Variant, string> = {
  info: "bg-ccm-red/5 text-ccm-red-dark border-ccm-red/20",
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
