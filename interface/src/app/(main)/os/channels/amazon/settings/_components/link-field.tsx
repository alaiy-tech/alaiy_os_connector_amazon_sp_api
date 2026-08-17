"use client";

import { useEffect, useState } from "react";

import { searchLinkOptions } from "@alaiy-os/frappe/link";
import { Button } from "@alaiy-os/ui/button";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@alaiy-os/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@alaiy-os/ui/popover";
import { cn } from "@alaiy-os/utils";
import { Check, ChevronsUpDown, X } from "lucide-react";

const SEARCH_DEBOUNCE_MS = 300;

/**
 * A picker for one of the order defaults' Link fields (Customer, Company,
 * Warehouse, Price List, Item).
 *
 * Matches on `name` alone, which is what `searchLinkOptions` offers — fine for
 * every DocType here, since all five are named after the thing they are. The
 * `Amazon Marketplace` picker is separate for exactly that reason: it is not.
 */
export function LinkField({
  doctype,
  value,
  onChange,
  disabled,
  placeholder = "Not set",
}: {
  doctype: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [options, setOptions] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);

    const timeout = setTimeout(() => {
      searchLinkOptions(doctype, term)
        .then((results) => {
          if (!cancelled) setOptions(results.map((option) => option.name));
        })
        .catch(() => {
          if (!cancelled) setOptions([]);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, SEARCH_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      clearTimeout(timeout);
    };
  }, [doctype, term, open]);

  return (
    <div className="flex items-center gap-1">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            disabled={disabled}
            aria-expanded={open}
            className={cn("h-8 w-full justify-between font-normal", !value && "text-muted-foreground")}
          >
            <span className="truncate">{value || placeholder}</span>
            <ChevronsUpDown className="opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-(--radix-popover-trigger-width) p-0" align="start">
          <Command shouldFilter={false}>
            <CommandInput placeholder={`Search ${doctype}...`} value={term} onValueChange={setTerm} />
            <CommandList>
              <CommandEmpty>{loading ? "Searching..." : `No ${doctype} found.`}</CommandEmpty>
              <CommandGroup>
                {options.map((option) => (
                  <CommandItem
                    key={option}
                    value={option}
                    onSelect={() => {
                      onChange(option);
                      setOpen(false);
                    }}
                  >
                    <Check className={cn("size-3.5", option === value ? "opacity-100" : "opacity-0")} />
                    {option}
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>

      {value && !disabled && (
        <Button variant="ghost" size="icon" className="size-8 shrink-0" onClick={() => onChange("")} aria-label="Clear">
          <X />
        </Button>
      )}
    </div>
  );
}
