import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@alaiy-os/ui/empty";
import type { LucideIcon } from "lucide-react";

/**
 * The in-card empty state these screens share.
 *
 * "Nothing came back" has several distinct meanings across them — a register
 * that was never synced, a filter that matched nothing, a read that failed, an
 * account health sync that has not run — and telling them apart is the whole
 * point, so every caller passes its own wording.
 */
export function ListEmpty({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <Empty className="py-12">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Icon />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        {description && <EmptyDescription>{description}</EmptyDescription>}
      </EmptyHeader>
      {action}
    </Empty>
  );
}
