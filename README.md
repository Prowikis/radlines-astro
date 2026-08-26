# Radlines Astro conversion

Generated from the supplied MediaWiki backup.

## Run

```bash
npm install
npm run dev
```

Then open `http://localhost:4321/Main`.

## Shared top section

The site-wide header lives in `src/components/SiteHeader.astro`. The text and About URL are stored in `src/data/site.json`. All pages use `src/layouts/RadlinesLayout.astro`, so changing the header once changes every page.

## Content architecture

The 129 main-namespace wiki pages were converted into `src/data/pages.json`. Astro generates static routes through `src/pages/[...slug].astro`.

## Scrollable CT/image stacks

Legacy MediaWiki `Imagestack` content is replaced by a prominent Wikimedia Commons link rather than emulating the old JavaScript viewer.

## Code tags with braces

Text inside legacy `<code>` tags is HTML-escaped during conversion, including explicit escaping of `{` and `}`, so Astro does not parse brace content as expressions if the content is later inlined into `.astro` files.

## Media

Current uploaded files from the MediaWiki `images/` tree were copied to `public/media/`. Deleted files, archived revisions, and generated thumbnails were intentionally excluded. Missing files are linked to their Wikimedia Commons file page.

## Conversion note

This is a static conversion. MediaWiki editing, talk pages, Lua modules, account management, and other server-side wiki functions are not reproduced. Complex legacy templates are simplified to their useful rendered content where possible.
