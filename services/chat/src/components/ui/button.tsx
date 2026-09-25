import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

// Sizing/radius/focus match the portal's own button (services/ingest/
// frontend/src/components/ui/button.tsx): 40px default / 32px "sm" (grows
// to 40px on a coarse pointer via the `.btn-sm` marker class — see
// globals.css), radius-control (8px), 14px/600 label, a reserved 1px
// border so focus can recolor it without a layout shift, and a
// accent-border + accent-soft ring focus treatment.
const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-control)] border border-transparent text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:border-[var(--accent)] focus-visible:ring-[3px] focus-visible:ring-[var(--accent-soft)] disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default: 'bg-[var(--accent)] text-[var(--on-accent)] hover:bg-[var(--accent-hover)]',
        outline:
          'border-[var(--line-2)] bg-[var(--surface)] text-[var(--ink)] hover:bg-[var(--surface-2)]',
        ghost: 'text-[var(--ink)] hover:bg-[var(--surface-2)]',
        danger: 'bg-[var(--err)] text-white hover:opacity-90',
      },
      size: {
        default: 'h-10 px-4 py-2',
        sm: 'btn-sm h-8 px-3',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
  )
);
Button.displayName = 'Button';

export { Button, buttonVariants };
