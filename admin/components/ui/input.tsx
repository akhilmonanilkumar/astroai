import { cn } from "@/lib/utils";

const FIELD =
  "rounded-md border border-border bg-surface px-3 text-sm placeholder:text-muted-foreground " +
  "focus:border-accent focus:outline-none disabled:opacity-50";

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(FIELD, "h-10 w-full", className)} {...props} />;
}

export function Textarea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(FIELD, "w-full py-2", className)} {...props} />;
}

export function Select({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(FIELD, "h-10", className)} {...props} />;
}

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label className={cn("mb-1 block text-xs font-medium text-muted-foreground", className)} {...props} />
  );
}
