"use client";

import { Button } from "@alaiy-os/ui/button";
import { Input } from "@alaiy-os/ui/input";
import { GripVertical, Plus, Trash2 } from "lucide-react";

/**
 * The bullet-points and keywords editor.
 *
 * Order is meaningful for bullets — Amazon shows them in the order given — so the
 * rows can be moved, and a blank row is kept while it is being typed into rather
 * than being dropped from under the cursor. Blanks are filtered out on the way to
 * a push, not here.
 *
 * There is no way to express "remove every bullet": an empty list reads as "no
 * opinion" all the way down to `diff_from_remote`, deliberately, so that a
 * half-rendered form cannot wipe a live listing's content.
 */
export function StringListEditor({
  values,
  onChange,
  disabled = false,
  placeholder,
  addLabel,
  max,
}: {
  values: string[];
  onChange: (values: string[]) => void;
  disabled?: boolean;
  placeholder: string;
  addLabel: string;
  /** Amazon's own cap for the field, so the limit is visible before a rejection. */
  max?: number;
}) {
  function set(index: number, value: string) {
    onChange(values.map((entry, position) => (position === index ? value : entry)));
  }

  function remove(index: number) {
    onChange(values.filter((_, position) => position !== index));
  }

  function move(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= values.length) return;
    const next = [...values];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }

  return (
    <div className="space-y-2">
      {values.map((value, index) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: position *is* the identity here — two identical bullets are two rows, and a value-based key would collapse them
        <div key={index} className="flex items-center gap-1">
          <div className="flex flex-col">
            <Button
              variant="ghost"
              size="icon"
              className="size-4 text-muted-foreground"
              disabled={index === 0 || disabled}
              onClick={() => move(index, -1)}
              aria-label="Move up"
            >
              <GripVertical className="rotate-90" />
            </Button>
          </div>
          <Input
            className="h-8"
            value={value}
            placeholder={placeholder}
            disabled={disabled}
            onChange={(event) => set(index, event.target.value)}
          />
          <Button
            variant="ghost"
            size="icon"
            className="size-8 shrink-0 text-muted-foreground hover:text-destructive"
            disabled={disabled}
            onClick={() => remove(index)}
            aria-label="Remove"
          >
            <Trash2 />
          </Button>
        </div>
      ))}

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={(max !== undefined && values.length >= max) || disabled}
          onClick={() => onChange([...values, ""])}
        >
          <Plus /> {addLabel}
        </Button>
        {max !== undefined && (
          <span className="text-muted-foreground text-xs">
            {values.length} of {max}
          </span>
        )}
      </div>
    </div>
  );
}
