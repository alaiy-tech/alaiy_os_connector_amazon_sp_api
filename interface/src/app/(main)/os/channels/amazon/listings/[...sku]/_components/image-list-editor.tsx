"use client";

import { Badge } from "@alaiy-os/ui/badge";
import { Button } from "@alaiy-os/ui/button";
import { Input } from "@alaiy-os/ui/input";
import { cn } from "@alaiy-os/utils";
import { ImageIcon, Plus, Star, Trash2 } from "lucide-react";

import type { AmazonPushImage } from "@/lib/amazon/types";

/**
 * The listing's image set: a main image and the rest, by URL.
 *
 * URLs, not uploads — Amazon fetches every image itself from a publicly reachable
 * address, so there is nothing here to attach to a document. Exactly one image is
 * the main one; picking a new one demotes the old, because Amazon's schema has a
 * single `main_product_image_locator` and two mains is not a state it can be sent.
 */
export function ImageListEditor({
  images,
  onChange,
  disabled = false,
}: {
  images: AmazonPushImage[];
  onChange: (images: AmazonPushImage[]) => void;
  disabled?: boolean;
}) {
  function setUrl(index: number, url: string) {
    onChange(images.map((image, position) => (position === index ? { ...image, url } : image)));
  }

  function setMain(index: number) {
    onChange(images.map((image, position) => ({ ...image, is_main: position === index })));
  }

  function remove(index: number) {
    const next = images.filter((_, position) => position !== index);
    // Removing the main image leaves a set with no main at all, which Amazon
    // refuses — promote the first survivor rather than submitting that.
    if (next.length > 0 && !next.some((image) => image.is_main)) next[0] = { ...next[0], is_main: true };
    onChange(next);
  }

  function add() {
    onChange([...images, { url: "", is_main: images.length === 0 }]);
  }

  return (
    <div className="space-y-3">
      {images.length === 0 && (
        <p className="text-muted-foreground text-sm">
          No images on this row. Adding one here submits it to Amazon on the next push; it never clears what Amazon
          already has.
        </p>
      )}

      {images.map((image, index) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: position is the identity — a blank row being typed into has no other one
        <div key={index} className="flex items-start gap-2">
          <Thumbnail url={image.url} isMain={image.is_main} />

          <div className="min-w-0 flex-1 space-y-1">
            <Input
              className="h-8"
              inputMode="url"
              placeholder="https://..."
              value={image.url}
              disabled={disabled}
              onChange={(event) => setUrl(index, event.target.value)}
            />
            {image.is_main && (
              <Badge
                variant="outline"
                className="border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
              >
                <Star /> Main image
              </Badge>
            )}
          </div>

          <Button
            variant="ghost"
            size="icon"
            className={cn("size-8 shrink-0", image.is_main ? "text-amber-500" : "text-muted-foreground")}
            disabled={image.is_main || disabled}
            onClick={() => setMain(index)}
            aria-label="Make this the main image"
            title="Make this the main image"
          >
            <Star />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-8 shrink-0 text-muted-foreground hover:text-destructive"
            disabled={disabled}
            onClick={() => remove(index)}
            aria-label="Remove image"
          >
            <Trash2 />
          </Button>
        </div>
      ))}

      <Button variant="outline" size="sm" onClick={add} disabled={disabled}>
        <Plus /> Add image URL
      </Button>
    </div>
  );
}

/**
 * A plain `<img>`, not `next/image`: these hosts are Amazon's CDN and whatever the
 * seller uses, unknowable at build time, so `images.remotePatterns` cannot
 * allowlist them and the optimizer would refuse every URL. `no-referrer` because
 * several image hosts answer 403 to a cross-origin Referer.
 */
function Thumbnail({ url, isMain }: { url: string; isMain: boolean }) {
  if (!url.trim()) {
    return (
      <div className="flex size-14 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
        <ImageIcon className="size-4" />
      </div>
    );
  }
  return (
    <div className={cn("size-14 shrink-0 overflow-hidden rounded-md bg-muted", isMain && "ring-2 ring-emerald-500/40")}>
      {/* biome-ignore lint/performance/noImgElement: listing image hosts are unknown at build time */}
      <img src={url} alt="" loading="lazy" referrerPolicy="no-referrer" className="size-full object-cover" />
    </div>
  );
}
