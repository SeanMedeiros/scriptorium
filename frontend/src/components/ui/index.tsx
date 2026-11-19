"use client";

// Component primitives in the shadcn/ui style (Radix + CVA + Tailwind), adapted to
// the Scriptorium editorial palette.

import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as LabelPrimitive from "@radix-ui/react-label";
import { Slot } from "@radix-ui/react-slot";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cva, type VariantProps } from "class-variance-authority";
import { X } from "lucide-react";
import * as React from "react";
import { cn } from "@/lib/utils";

// ---------- Button ----------
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-md)] text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-accent text-white hover:bg-accent-hover",
        secondary: "border border-rule-strong bg-surface text-ink hover:bg-sunken",
        ghost: "text-ink-2 hover:bg-sunken hover:text-ink",
        danger: "border border-danger/30 bg-surface text-danger hover:bg-danger-soft",
        link: "text-accent underline-offset-4 hover:underline px-0",
      },
      size: { sm: "h-8 px-3 text-[13px]", md: "h-9 px-4", lg: "h-10 px-5", icon: "h-8 w-8" },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild, ...props }, ref) => {
  const Comp = asChild ? Slot : "button";
  return <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
});
Button.displayName = "Button";

// ---------- Form controls ----------
const field =
  "w-full rounded-[var(--radius-md)] border border-rule-strong bg-surface px-3 text-sm text-ink placeholder:text-muted/70 focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/15 disabled:bg-sunken aria-[invalid=true]:border-danger";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...p }, ref) => (
  <input ref={ref} className={cn(field, "h-9", className)} {...p} />
));
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...p }, ref) => <textarea ref={ref} className={cn(field, "min-h-[88px] py-2 leading-relaxed", className)} {...p} />,
);
Textarea.displayName = "Textarea";

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...p }, ref) => <select ref={ref} className={cn(field, "h-9 pr-8", className)} {...p} />,
);
Select.displayName = "Select";

export function Label({ className, ...p }: React.ComponentProps<typeof LabelPrimitive.Root>) {
  return <LabelPrimitive.Root className={cn("text-[13px] font-medium text-ink-2", className)} {...p} />;
}

export function Field({
  label,
  hint,
  error,
  htmlFor,
  optional,
  children,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  htmlFor: string;
  optional?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor}>
        {label}
        {optional && <span className="ml-1 font-normal text-muted">({optional})</span>}
      </Label>
      {children}
      {hint && !error && <p className="text-xs text-muted">{hint}</p>}
      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

// ---------- Surfaces ----------
export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-[var(--radius-lg)] border border-rule bg-surface", className)} {...p} />;
}

export function CardHeader({ title, description, action }: { title: React.ReactNode; description?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-rule px-5 py-4">
      <div>
        <h2 className="font-serif text-[17px] font-semibold text-ink">{title}</h2>
        {description && <p className="mt-0.5 text-[13px] text-muted">{description}</p>}
      </div>
      {action}
    </div>
  );
}

const badgeVariants = cva("inline-flex items-center rounded-[var(--radius-sm)] px-1.5 py-0.5 text-[11px] font-medium tracking-wide", {
  variants: {
    tone: {
      neutral: "bg-sunken text-ink-2",
      accent: "bg-accent-soft text-accent",
      success: "bg-success-soft text-success",
      warn: "bg-warn-soft text-warn",
      danger: "bg-danger-soft text-danger",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export function Badge({ className, tone, ...p }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...p} />;
}

export function Alert({ tone = "danger", children }: { tone?: "danger" | "success" | "warn" | "accent"; children: React.ReactNode }) {
  const tones = {
    danger: "border-danger/25 bg-danger-soft text-danger",
    success: "border-success/25 bg-success-soft text-success",
    warn: "border-warn/25 bg-warn-soft text-warn",
    accent: "border-accent/20 bg-accent-soft text-accent",
  };
  return (
    <div role={tone === "danger" ? "alert" : "status"} className={cn("rounded-[var(--radius-md)] border px-3 py-2 text-sm", tones[tone])}>
      {children}
    </div>
  );
}

export function EmptyState({ title, body, action }: { title: string; body?: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-14 text-center">
      <p className="font-serif text-lg text-ink">{title}</p>
      {body && <p className="mt-1 max-w-md text-sm text-muted">{body}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function PageHeader({ title, subtitle, action, eyebrow }: { title: string; subtitle?: React.ReactNode; action?: React.ReactNode; eyebrow?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4 border-b border-rule pb-5">
      <div>
        {eyebrow && <div className="mb-1 text-xs font-medium uppercase tracking-[0.08em] text-muted">{eyebrow}</div>}
        <h1 className="font-serif text-[28px] font-semibold leading-tight tracking-[-0.01em] text-ink">{title}</h1>
        {subtitle && <div className="mt-1 text-sm text-muted">{subtitle}</div>}
      </div>
      {action}
    </div>
  );
}

// ---------- Table ----------
export function Table({ className, ...p }: React.TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="overflow-x-auto">
      <table className={cn("w-full border-collapse text-sm", className)} {...p} />
    </div>
  );
}
export function Th({ className, ...p }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn("border-b border-rule px-4 py-2 text-left text-xs font-medium uppercase tracking-[0.06em] text-muted", className)} {...p} />;
}
export function Td({ className, ...p }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("border-b border-rule px-4 py-2.5 align-top text-ink-2", className)} {...p} />;
}

// ---------- Dialog ----------
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogContent({ title, description, children, className }: { title: string; description?: string; children: React.ReactNode; className?: string }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-ink/30" />
      <DialogPrimitive.Content
        className={cn(
          "fixed left-1/2 top-[10vh] z-50 max-h-[80vh] w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 overflow-y-auto rounded-[var(--radius-lg)] border border-rule bg-surface shadow-[0_12px_40px_-12px_rgba(30,28,25,0.35)]",
          className,
        )}
      >
        <div className="flex items-start justify-between border-b border-rule px-5 py-4">
          <div>
            <DialogPrimitive.Title className="font-serif text-lg font-semibold text-ink">{title}</DialogPrimitive.Title>
            {description ? (
              <DialogPrimitive.Description className="mt-0.5 text-[13px] text-muted">{description}</DialogPrimitive.Description>
            ) : (
              <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>
            )}
          </div>
          <DialogPrimitive.Close className="rounded p-1 text-muted hover:bg-sunken hover:text-ink" aria-label="Close">
            <X className="size-4" />
          </DialogPrimitive.Close>
        </div>
        <div className="px-5 py-4">{children}</div>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

// ---------- Tabs ----------
export const Tabs = TabsPrimitive.Root;
export function TabsList({ className, ...p }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return <TabsPrimitive.List className={cn("mb-5 flex gap-6 border-b border-rule", className)} {...p} />;
}
export function TabsTrigger({ className, ...p }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "-mb-px border-b-2 border-transparent pb-2.5 text-sm text-muted transition-colors hover:text-ink data-[state=active]:border-accent data-[state=active]:font-medium data-[state=active]:text-ink",
        className,
      )}
      {...p}
    />
  );
}
export const TabsContent = TabsPrimitive.Content;

export function Stat({ label, value, note }: { label: string; value: React.ReactNode; note?: string }) {
  return (
    <div className="px-5 py-4">
      <div className="text-xs font-medium uppercase tracking-[0.07em] text-muted">{label}</div>
      <div className="tabular mt-1.5 font-serif text-[30px] font-semibold leading-none text-ink">{value}</div>
      {note && <div className="mt-1 text-xs text-muted">{note}</div>}
    </div>
  );
}
