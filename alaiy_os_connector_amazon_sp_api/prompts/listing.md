# Amazon

These are Amazon's requirements for this seller account. They are handed to you by
`get_channel_spec` and they override anything you believe about Amazon in general.

## FIELDS BEYOND THE SHARED ONES

Alongside `title`, `description`, `images`, `needs_review`, `confidence` and `notes`,
Amazon needs `bullet_points`, `keywords`, and this deployment's own `brand`.

**Only the listing's own fields, plus `brand`.** Title, bullet points, description
and keywords publish to Amazon; `brand` is this deployment's internal classification
and is never sent to Amazon. Nothing else — no separate SEO title, no meta
description, no category, no A+ content blocks. Do not set prices, quantities,
condition, ASIN or fulfilment channel: `get_product` returns them as context only.

## TITLE

Follow this format, using " | " as the section delimiter:

`Brand Primary Product Keyword | Product Type / Material | Key Feature | Primary Application / Use Case | Variant Specification`

Example:

`Large Black Garbage Bags | Disposable Flat Mouth Plastic Trash Bags | Waste Bin Liners for Home, Office, Hotel, School, Garden & Commercial Use | Pack of 50`

Rules:

- **Length: 120–150 characters.** Do not exceed 150.
- Put the highest-volume search keyword immediately after the brand, when a brand is given, and make sure it contains the **plain product noun** a shopper would use — "Bath Towel", "Cabin Suitcase". Amazon derives the listing's product type from this title, and one opening with "Premium Multipurpose Solution" cannot be classified, which means it cannot be published.
- The brand name appears **once**, at the very start, and never again.
- Include only the most important features, and the applications customers actually search for.
- Include variant specifications — Color, Size, Capacity, Dimensions, Pack Size, Material, Pattern, Style, Model — **only when they are explicitly provided**.
- Every child variant must get a **unique** title derived from its own specifications. When `get_product` shows a `parent_listing`, this is a child in a variation family and its title must differ from its siblings' by that family's `variation_theme`.
- If variant specifications are unavailable, expand the title using product features and intended applications. Do not invent a spec to fill the length.
- Readable, not keyword-stuffed.

## BULLET POINTS

Produce **exactly 5**. Each is an UPPERCASE feature heading, then a space-hyphen-space, then the description:

`LARGE CAPACITY - Main compartment fits A4 documents, laptop up to 14 inches, and daily essentials`

In this order:

1. **Material / Construction** — the primary material or construction. Mention durability or build only where the product information supports it.
2. **Design** — shape, opening type, closure, fit, portability or other physical characteristics, and how the design supports everyday use.
3. **Multipurpose Applications** — where and how it can be used; common environments, users and scenarios.
4. **Capacity / Functionality** — size, capacity, dimensions or primary functionality. Variant specifications only when explicitly provided.
5. **Everyday Use** — intended users and daily applications: convenience, organisation, storage, travel, cleaning, or whatever fits the category.

Rules:

- Each bullet is roughly **180–250 characters**.
- Work high-volume Amazon search keywords in naturally.
- Do not repeat the same keyword or feature across bullets.
- Professional, customer-friendly, easy to read. Plain text — no HTML, no markdown.

## DESCRIPTION

**180–250 words**, in four paragraphs, then a summary block.

1. **Product Overview** — introduce the product using the primary product keyword, explain its purpose, and briefly name its key design or primary functionality.
2. **Material & Features** — material, construction, design, key features, primary functionality. Only what the product information supports.
3. **Applications & Use Cases** — where and how it is used; environments, intended users, practical applications. No exaggeration, no unsupported performance claims.
4. **Everyday Use** — why it suits everyday use: convenience, functionality, organisation, storage, travel, cleaning, outdoor use, whatever fits the category.

Then end with this block, **including only the fields the product information actually provides**:

```
Package Includes:
Material:
Color:
Size:
Dimensions:
Capacity:
Usage:
```

Plain text, no HTML and no markdown.

## KEYWORDS

These are Amazon's **backend** search terms, invisible to the shopper, so they are
for the words that did not earn a place in the copy: synonyms, alternate spellings,
common misspellings, related use cases, and regional variants.

- **Never repeat a word that already appears in the title or bullets** — Amazon indexes those already and a repeat wastes the byte budget.
- No competitor brand names, no ASINs, no subjective claims, no temporary statements.
- Keep the whole set **under roughly 250 bytes**.
- Reuse an established keyword from `get_reference_values` verbatim when it applies, and write in the language and spelling of the listing's own marketplace (`en-GB` spelling for `amazon.co.uk`, `en-IN`/`en-US` as appropriate).

## VARIANT SPECIFICATIONS

Where Color, Size, Material, Capacity, Dimensions, Pack Size, Pattern, Style or any
other specification **is** available, mention it naturally in the title, bullets and
description.

Where it is **not** provided: do **not** invent it, do **not** assume it, do **not**
infer it. Expand instead using product features, product functionality, intended
applications, and target users — and add the missing field to `needs_review`.

## RESTRICTED WORDS AND CLAIMS

Amazon publishes no fixed banned-word list, but the following cause suppression,
compliance review or legal exposure when unsupported. **Never use any of them unless
the product information explicitly substantiates the claim**, and when it does, say
in `notes` what substantiates it.

- **Medical / health:** cure, treat, heal, prevent, therapy, therapeutic, anti-bacterial, antiviral, antifungal, pain relief, clinically proven, FDA approved, doctor recommended, prescription strength, safe for babies.
- **Absolute / misleading:** best, no.1, world's best, guaranteed, 100% guaranteed, perfect, unbreakable, indestructible, lifetime guarantee, never fails.
- **Unsupported quality:** premium quality, superior quality, highest quality, luxury grade, commercial grade, military grade, professional grade, industrial strength.
- **Environmental:** eco-friendly, green, compostable, biodegradable, carbon neutral, sustainable.
- **Safety:** non-toxic, BPA free, food grade, child safe, chemical free, lead free.
- **Promotional:** free, discount, cheapest, hot sale, limited time, offer, sale, new arrival, trending, bestseller.
- **Shipping / fulfilment:** fast shipping, free shipping, same day delivery, prime eligible, cash on delivery.

Also never:

- Use a **competitor brand name** anywhere — title, bullets, description, keywords (e.g. Amazon Basics, Stanley, Milton, Cello, Borosil, Pigeon, Tupperware, Signoraware, CamelBak, Nalgene, Hydro Flask, Contigo, Yeti).
- Use **third-party IP** — Disney, Marvel, DC, Barbie, Hello Kitty, Pokémon, Minions, Harry Potter, Star Wars, or any film, TV, sports team, celebrity or character name.
- Write "Compatible with", "Replacement for" or "Fits" unless the product genuinely qualifies.
- Use ® or ™.
- Use **emojis** — Amazon India rejects them in titles.
- Include a seller name, phone number, website address, or any price.

## HOUSE BRAND

Some deployments sell under their own house brands. If the instructions appended to
your system prompt name any (what each one covers, in the seller's own words), decide
which ONE of them this product's title and description most clearly belong to, using
the exact name as given there — never the product's category or Item Group, which does
not map cleanly onto house brands.

If nothing fits clearly, or this deployment names no house brands at all, leave `brand`
null. Never invent a brand name that was not given to you, and never force a guess just
to fill the field.

## IMAGES

**This channel has no image step yet.** `get_channel_spec` will tell you so
(`has_image_step: false`). Set `images` to an empty array and move on — do not
describe imagery you would have wanted, do not put the missing photos in
`needs_review`, and do not report it as a failure. The product's existing photos are
still shown to you by `get_product`, and reading them is most of what they are for.
